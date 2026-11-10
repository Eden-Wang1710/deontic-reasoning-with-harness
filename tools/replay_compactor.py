"""Offline replay of the compactor on a finished t2rv trial (debugging aid, claude_docs/09 §10.5).

Rebuilds the agent state of a reader-verifier trial at the step right after verifier round R
and runs _compactor_call against a local fake environment (commands run on this machine with
/app/statute.txt, the answer file and /tmp/compactor mapped to a temp dir). One compactor call
instead of a whole trial.

Run from harbor-deonticbench/ (task files are read from datasets/deonticbench-direct):

  PYTHONPATH=../tools uv run python ../tools/replay_compactor.py <trial_dir> [--round 1] \
      [--live] [--out DIR] [--ak key=value ...]

--live calls the model server (port 9009); otherwise a canned result is used (mechanics only).
--ak passes agent kwargs as strings, e.g. --ak compactor_thinking=false.
"""
import argparse, asyncio, json, re, shutil, subprocess, sys, tempfile
from pathlib import Path
from types import SimpleNamespace

from harbor.models.trajectories import Step

import dar_terminus2_compactor as mod
from dar_terminus2_multiagent import _sha

DATASETS = Path("datasets/deonticbench-direct")


class FakeEnv:
    def __init__(self, root: Path, statute: Path, answer: str):
        self.root = root
        (root / "app/output").mkdir(parents=True)
        shutil.copy(statute, root / "app/statute.txt")
        (root / "app/output/answer.txt").write_text(answer)
        (root / "tmp").mkdir()
        self.log = []

    def _map(self, text: str) -> str:
        return re.sub(r"(?<![\w.-])/(app|tmp)/", lambda m: f"{self.root}/{m.group(1)}/", text)

    async def exec(self, command, timeout_sec=60, **kw):
        self.log.append(command)
        p = subprocess.run(["bash", "-c", self._map(command)], capture_output=True, text=True, timeout=timeout_sec)
        out = p.stdout.replace(str(self.root), "")
        return SimpleNamespace(stdout=out, stderr=p.stderr.replace(str(self.root), ""), return_code=p.returncode)

    async def upload_file(self, local, remote):
        dst = Path(self._map(str(remote)))
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(local, dst)


class FakeChat:
    def __init__(self, n_pairs):
        self._messages = []
        for i in range(n_pairs):
            self._messages.append({"role": "user", "content": f"u{i}"})
            self._messages.append({"role": "assistant", "content": f"a{i}"})
        self.total_input_tokens = 0
        self.reset = 0

    @property
    def messages(self):
        return self._messages

    def reset_response_chain(self):
        self.reset += 1


def obs_of(step):
    r = (step.get("observation") or {}).get("results") or []
    return "\n".join(x.get("content") or "" for x in r)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trial")
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--ak", action="append", default=[])
    a = ap.parse_args()
    trial = Path(a.trial)
    task = json.loads((trial / "result.json").read_text())["task_name"]
    tdir = DATASETS / task
    agent_dir = trial / "agent"
    traj = json.loads((agent_dir / "trajectory.json").read_text())
    out = Path(a.out or tempfile.mkdtemp(prefix="replay-"))
    out.mkdir(parents=True, exist_ok=True)
    extra = dict(kv.split("=", 1) for kv in a.ak)
    agent = mod.Terminus2ReaderVerifierCompactor(
        logs_dir=out,
        model_name="hosted_vllm/qwen3.5-35b-a3b",
        api_base="http://localhost:9009/v1",
        llm_kwargs={"api_key": "token-abc123"},
        model_info={"max_input_tokens": 49152, "max_output_tokens": 16384, "max_tokens": 65536,
                    "input_cost_per_token": 0.0, "output_cost_per_token": 0.0},
        verifier_max_steps=3,
        **extra,
    )
    # cut the trajectory after the step whose observation carries the report of round R
    head = f"=== VERIFIER RESULT (round {a.round} of"
    steps, cut = traj["steps"], None
    for i, s in enumerate(steps):
        if s["source"] == "agent" and head in obs_of(s):
            cut = i
            break
    if cut is None:
        sys.exit(f"no verifier round {a.round} in {trial}")
    kept = steps[: cut + 1]
    agent._trajectory_steps = [Step.model_validate(s) for s in kept]
    obs = obs_of(steps[cut])
    report = obs[obs.find("VERIFICATION REPORT"):]
    report = report[: report.find("\n\nDecide how to proceed")] if "\n\nDecide how to proceed" in report else report
    rnd = json.loads((agent_dir / "rubric_verifier" / f"round-{a.round}.json").read_text())
    answer = rnd.get("answer") or ""
    agent._original_instruction = (tdir / "instruction.md").read_text()
    agent._verify_round = a.round
    agent._previous_report_text = report.strip()
    agent._previous_answer = answer
    agent._verified_hash = _sha(answer)
    agent._session_id = "replay"
    n_reader = max([int(x) for s in kept for x in re.findall(r"=== READER RESULT \(call (\d+)", obs_of(s))] or [0])
    for c in range(1, n_reader + 1):
        rec = json.loads((agent_dir / "reader" / f"call-{c}.json").read_text())
        agent._reader_calls.append(rec)
        for e in rec["excerpts"]:
            if e.get("inserted"):
                agent._reader_returned.append({"call": c, "file": e["file"], "lines": e.get("lines_shown", e["lines"]), "label": e.get("label", "")})
    n_agent = sum(1 for s in kept if s["source"] == "agent")
    chat = FakeChat(n_agent + 1)
    agent._solver_chat = chat
    nxt = next((s for s in steps[cut + 1:] if s["source"] == "agent"), None)
    agent._last_prompt_tokens = ((nxt or steps[cut]).get("metrics") or {}).get("prompt_tokens") or 12000
    agent._current_analysis = "(replay) the report has a NO; rebuilding the context before working on it"
    agent._current_plan = ""
    env = FakeEnv(Path(tempfile.mkdtemp(prefix="fakeenv-")), tdir / "environment/statute.txt", answer)
    if not a.live:
        first = agent._reader_returned[0]["lines"] if agent._reader_returned else "1-10"
        async def canned(environment, **kw):
            return {"result": {"rules": [{"lines": first, "label": "kept", "why": "cited"}, {"lines": "5-9", "label": "new", "why": "report item"}, {"lines": "999999-1000000"}],
                               "steps": [{"step": n_agent - 2, "why": "computation"}, "step 3", 999],
                               "notes": {"set_aside": ["x " * 30], "open": "one thing"}},
                    "chat": SimpleNamespace(total_input_tokens=1, total_output_tokens=1), "steps": [], "session_id": "s",
                    "commands": [], "parse_failures": 0, "steps_used": 0, "context_exceeded": False, "n_calls": 1}
        agent._run_tool_subagent = canned
        agent._save_subagent_trajectory = lambda *x, **k: None
    session = SimpleNamespace(environment=env)
    page = await agent._compactor_call(session, [], "", trigger="solver")
    rec = agent._compactor_calls[-1] if agent._compactor_calls else {}
    print("task", task, "| reader calls", n_reader, "| solver steps", n_agent, "| est tokens", agent._last_prompt_tokens)
    print("packet chars", rec.get("packet_chars"), "| page chars", len(page or ""), "| messages", rec.get("messages_before"), "->", len(chat.messages), "| reset", chat.reset)
    print("rebuilt", rec.get("rebuilt"), "| abort", rec.get("abort_reason"), "| steps_used", rec.get("steps_used"), "| elapsed", round(rec.get("elapsed_sec", 0)), "| tokens", rec.get("prompt_tokens"), rec.get("completion_tokens"))
    print("kept rules", [(e["lines"], e.get("inserted"), e.get("reason")) for e in rec.get("excerpts", [])])
    print("kept steps", rec.get("kept_steps"), "| notes", rec.get("notes"))
    print("in-context before:", [r["lines"] for r in agent._reader_returned])
    print("out dir", out)
    agent._write_summary()

asyncio.run(main())
