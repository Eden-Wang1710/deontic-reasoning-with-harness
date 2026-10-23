#!/usr/bin/env python3
"""Self-verbalised confidence on finished DAR trajectories.

For every finished trial of a Harbor job, rebuild the conversation the agent had when it
marked the task complete, append one user turn that asks for the probability that the final
answer is exactly correct, call the same model once, and parse ``Confidence: NN%``.
Then score the confidences against the verifier's reward (ECE, Kuiper, Brier, AUROC,
selective accuracy). See claude_docs/08_verbalized_confidence.md.

Conditions (``--condition``):
  incontext   the agent's own full conversation (terminus-2: exact messages from the last
              episode's debug.json; KIRA: rebuilt from agent/trajectory.json in KIRA's native
              tool-call format) + the elicitation turn. "Self" confidence.
  answeronly  a fresh context: statute + task + the agent's final answer, no trajectory.
              A judge-style baseline that shows how much the trajectory context matters.
  lastturn    the conversation as it was *before* the step that wrote the final answer
              (terminus-2: that episode's debug.json messages; KIRA: trajectory steps before
              the answer-writing execute_commands). The model is asked to give the answer and
              its confidence in the same response, so the confidence is produced with the
              live reasoning of the answer step, at zero extra cost in a real harness. The
              answer is re-sampled, so it is re-graded with DAR's test.sh rule; the record
              keeps ``reward`` (new answer) and ``reward_orig`` (the trajectory's).

Usage (from the repo root, vLLM server from tools/serve_qwen35_35b.sh running):
  python3 tools/verbalized_confidence.py --job harbor-deonticbench/jobs/qwen3.5-35b-a3b/direct/sara-t2-run1 \
      --harness t2 --condition incontext --out outputs/verbalized_confidence/t2_incontext
  python3 tools/verbalized_confidence.py --summary outputs/verbalized_confidence/t2_incontext   # metrics only

Per-trial JSON files land in --out (gitignored); --summary recomputes the metrics from them.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ELICIT_INCONTEXT = """The task is now finished. Your answer file contains:

Answer: {answer}

Before it is graded, estimate how likely it is that this answer is exactly correct under the statute and the case facts. The grader uses exact match: a dollar amount must match the gold integer exactly, and Entailment/Contradiction must match the gold label. Consider whether you may have misread or misapplied a provision, missed a cross-reference or an exception, misjudged filing status or a dependency, or made an arithmetic slip.

Reply in plain text, not JSON, and do not run any commands. The first line must be exactly:
Confidence: <integer from 0 to 100>%
followed by at most three sentences naming the main risk to the answer."""

ELICIT_ANSWERONLY = """Below are a statute, a case with a question, and an answer that an AI agent produced for it.

=== STATUTE ===
{statute}
=== END STATUTE ===

=== TASK ===
{task}
=== END TASK ===

=== AGENT'S FINAL ANSWER ===
Answer: {answer}
=== END ANSWER ===

Estimate how likely it is that this answer is exactly correct under the statute and the case facts. The grader uses exact match: a dollar amount must match the gold integer exactly, and Entailment/Contradiction must match the gold label. You may reason about the statute and the facts as much as you need.

Reply in plain text. The first line must be exactly:
Confidence: <integer from 0 to 100>%
followed by at most three sentences naming the main risk to the answer."""

ELICIT_LASTTURN = """You are now at the point of giving your final answer. Do not run any more commands and do not reply in JSON. Reply in plain text with exactly these two lines first:
Answer: <value>
Confidence: <integer from 0 to 100>%
where <value> is the whole-dollar amount (no $ sign) or Entailment/Contradiction, and the confidence is your estimate of how likely it is that this answer is exactly correct under the statute and the case facts (the grader uses exact match). Then add at most three sentences naming the main risk to the answer."""

ANSWER_RE = re.compile(r"^\s*\**\s*Answer\s*[:：]\s*\**\s*([^\n]+?)\s*\**\s*$", re.I | re.M)

CONF_RE = re.compile(r"confidence\s*[:=]?\s*\**\s*([0-9]{1,3}(?:\.[0-9]+)?)\s*%", re.I)
# fallbacks must be tied to a confidence word: a bare "15%" in a tax answer is a tax rate
PCT_RE = re.compile(r"([0-9]{1,3}(?:\.[0-9]+)?)\s*%\s*(?:confident|confidence|probability|likely|chance)", re.I)
PROB_RE = re.compile(r"(?:confidence|probability)\s*(?:of|is|:|=)?\s*(0\.[0-9]+|1\.0+)\b", re.I)


# ----------------------------------------------------------------------------- trial loading

def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def load_trials(job_dir: str) -> list[dict]:
    """Finished, exception-free trials with the verifier's expected/got and reward."""
    out = []
    for d in sorted(glob.glob(os.path.join(job_dir, "*", ""))):
        res = os.path.join(d, "result.json")
        if not os.path.exists(res):
            continue
        r = json.load(open(res))
        if r.get("exception_info"):
            continue
        reward = ((r.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        if reward is None:
            continue
        exp = got = None
        vs = os.path.join(d, "verifier", "test-stdout.txt")
        if os.path.exists(vs):
            for line in open(vs):
                if line.startswith("Expected:"):
                    exp = line.split(":", 1)[1].strip()
                elif line.startswith("Got:"):
                    got = line.split(":", 1)[1].strip()
        if got is None:
            continue
        name = os.path.basename(d.rstrip("/"))
        out.append(
            {
                "trial": name,
                "dir": d,
                "task": r.get("task_name", name.split("__")[0]),
                "subdomain": "sara-numeric" if "numeric" in name else "sara-binary",
                "expected": exp,
                "got": got,
                "reward": float(reward),
            }
        )
    return out


def t2_messages(trial: dict) -> list[dict]:
    """terminus-2: the exact messages of the last LLM call plus its answer."""
    eps = sorted(
        glob.glob(os.path.join(trial["dir"], "agent", "episode-*")),
        key=lambda p: int(p.rsplit("-", 1)[1]),
    )
    dj = json.load(open(os.path.join(eps[-1], "debug.json")))
    msgs = [{"role": m["role"], "content": m["content"]} for m in dj["messages"]]
    resp = json.loads(dj["original_response"])
    msgs.append({"role": "assistant", "content": resp["choices"][0]["message"]["content"] or ""})
    return msgs


WRITE_RE = re.compile(r"answer\.txt")


def _writes_answer(keystrokes: str) -> bool:
    """A shell command that writes the answer file (echo/printf/cat/tee ... > answer.txt)."""
    return bool(WRITE_RE.search(keystrokes)) and (">" in keystrokes or "tee " in keystrokes)


def t2_lastturn_messages(trial: dict) -> list[dict]:
    """terminus-2: the messages of the last episode whose response wrote the answer file."""
    eps = sorted(
        glob.glob(os.path.join(trial["dir"], "agent", "episode-*")),
        key=lambda p: int(p.rsplit("-", 1)[1]),
    )
    chosen = None
    for e in eps:
        dj = json.load(open(os.path.join(e, "debug.json")))
        content = json.loads(dj["original_response"])["choices"][0]["message"]["content"] or ""
        try:
            cmds = json.loads(content.strip()).get("commands", [])
            ks = "\n".join(c.get("keystrokes", "") for c in cmds if isinstance(c, dict))
        except Exception:
            ks = content
        if _writes_answer(ks):
            chosen = dj
    if chosen is None:
        raise RuntimeError(f"{trial['trial']}: no answer-writing episode")
    return [{"role": m["role"], "content": m["content"]} for m in chosen["messages"]]


def kira_answer_step(steps: list[dict]) -> int:
    """Index of the last agent step whose bash_command keystrokes write the answer file."""
    idx = None
    for i, s in enumerate(steps):
        if s.get("source") != "agent":
            continue
        ks = "\n".join(
            tc["arguments"].get("keystrokes", "")
            for tc in s.get("tool_calls") or []
            if tc.get("function_name") == "bash_command"
        )
        if _writes_answer(ks):
            idx = i
    if idx is None:
        raise RuntimeError("no answer-writing step")
    return idx


def _split_analysis_plan(message: str) -> tuple[str, str]:
    analysis, plan = message, ""
    m = re.match(r"Analysis:\s*(.*?)\nPlan:\s*(.*)\Z", message, re.S)
    if m:
        analysis, plan = m.group(1).strip(), m.group(2).strip()
    return analysis, plan


def kira_messages(trial: dict, upto_step: int | None = None) -> list[dict]:
    """KIRA: rebuild the native tool-call conversation from the ATIF trajectory.

    With ``upto_step`` the rebuild stops before that step index (steps[upto_step:] are dropped).

    baseline_kira keeps: user prompt -> assistant{content, tool_calls} -> one tool message
    "executed" per call -> user (terminal output) -> ...  The final terminal output is merged
    into the elicitation turn by the caller (it is returned as a trailing user message).
    """
    t = json.load(open(os.path.join(trial["dir"], "agent", "trajectory.json")))
    steps = t["steps"] if upto_step is None else t["steps"][:upto_step]
    msgs = [{"role": "user", "content": steps[0]["message"]}]
    n_call = 0
    for s in steps[1:]:
        if s.get("source") != "agent":
            continue
        analysis, plan = _split_analysis_plan(s.get("message") or "")
        tool_calls = []
        cmds = [
            {"keystrokes": tc["arguments"].get("keystrokes", ""), "duration": tc["arguments"].get("duration", 1.0)}
            for tc in s.get("tool_calls") or []
            if tc.get("function_name") == "bash_command"
        ]
        if cmds:
            n_call += 1
            tool_calls.append(
                {
                    "id": f"call_{n_call}",
                    "type": "function",
                    "function": {
                        "name": "execute_commands",
                        "arguments": json.dumps({"analysis": analysis, "plan": plan, "commands": cmds}),
                    },
                }
            )
        for tc in s.get("tool_calls") or []:
            if tc.get("function_name") == "mark_task_complete":
                n_call += 1
                tool_calls.append(
                    {"id": f"call_{n_call}", "type": "function", "function": {"name": "task_complete", "arguments": "{}"}}
                )
        a = {"role": "assistant", "content": "" if tool_calls else (s.get("message") or "")}
        if tool_calls:
            a["tool_calls"] = tool_calls
        msgs.append(a)
        for tc in tool_calls:
            msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": "executed"})
        obs = s.get("observation") or {}
        content = "\n".join((r.get("content") or "") for r in obs.get("results", []))
        if content:
            msgs.append({"role": "user", "content": content})
    return msgs


def task_text(trial: dict, datasets_root: str) -> tuple[str, str]:
    tdir = os.path.join(datasets_root, trial["task"])
    instr = _read(os.path.join(tdir, "instruction.md"))
    statute = _read(os.path.join(tdir, "environment", "statute.txt"))
    return instr, statute


def build_messages(trial: dict, harness: str, condition: str, datasets_root: str) -> list[dict]:
    if condition == "answeronly":
        instr, statute = task_text(trial, datasets_root)
        return [{"role": "user", "content": ELICIT_ANSWERONLY.format(statute=statute, task=instr, answer=trial["got"])}]
    if condition == "lastturn":
        if harness == "t2":
            msgs = t2_lastturn_messages(trial)
        else:
            steps = json.load(open(os.path.join(trial["dir"], "agent", "trajectory.json")))["steps"]
            msgs = kira_messages(trial, upto_step=kira_answer_step(steps))
        if msgs and msgs[-1]["role"] == "user":
            msgs[-1]["content"] = msgs[-1]["content"] + "\n\n" + ELICIT_LASTTURN
        else:
            msgs.append({"role": "user", "content": ELICIT_LASTTURN})
        return msgs
    elicit = ELICIT_INCONTEXT.format(answer=trial["got"])
    if harness == "t2":
        msgs = t2_messages(trial)
        msgs.append({"role": "user", "content": elicit})
    else:
        msgs = kira_messages(trial)
        if msgs and msgs[-1]["role"] == "user":
            msgs[-1]["content"] = msgs[-1]["content"] + "\n\n" + elicit
        else:
            msgs.append({"role": "user", "content": elicit})
    return msgs


# ----------------------------------------------------------------------------- model call

def parse_confidence(text: str) -> tuple[float | None, str]:
    if not text:
        return None, "empty"
    m = CONF_RE.search(text)
    if m:
        return min(float(m.group(1)), 100.0) / 100.0, "confidence_line"
    m = PCT_RE.search(text)
    if m:
        return min(float(m.group(1)), 100.0) / 100.0, "percent_with_word"
    m = PROB_RE.search(text)
    if m:
        return float(m.group(1)), "probability"
    return None, "unparsed"


def parse_answer(text: str) -> str | None:
    m = ANSWER_RE.findall(text or "")
    return m[-1].strip().strip("*").strip() if m else None


def grade(answer: str | None, expected: str | None) -> float:
    """DAR's tests/test.sh: whitespace stripped; integer expected -> drop decimals from the
    answer and compare strings; otherwise case-insensitive string match."""
    if answer is None or expected is None:
        return 0.0
    a = re.sub(r"\s+", "", answer)
    e = re.sub(r"\s+", "", expected)
    if re.fullmatch(r"-?[0-9]+", e):
        a = a.replace("$", "").replace(",", "")
        a = re.sub(r"\..*", "", a)
        return 1.0 if a == e else 0.0
    return 1.0 if a.lower() == e.lower() else 0.0


def call_model(client, model: str, messages: list[dict], max_tokens: int, think: bool) -> dict:
    t0 = time.time()
    kw = dict(model=model, messages=messages, max_tokens=max_tokens)
    if not think:
        kw["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
    r = client.chat.completions.create(**kw)
    m = r.choices[0].message
    reasoning = getattr(m, "reasoning", None) or getattr(m, "reasoning_content", None) or ""
    return {
        "content": m.content or "",
        "reasoning": reasoning,
        "finish_reason": r.choices[0].finish_reason,
        "usage": {"prompt_tokens": r.usage.prompt_tokens, "completion_tokens": r.usage.completion_tokens},
        "elapsed_s": round(time.time() - t0, 1),
    }


def run_trial(trial, harness, condition, datasets_root, client, model, max_tokens, think, out_dir):
    out_path = os.path.join(out_dir, trial["trial"] + ".json")
    prev = None
    if os.path.exists(out_path):
        prev = json.load(open(out_path))
        # re-parse with the current parser; redo the call only if still unparsed (max 3 draws)
        prev["confidence"], prev["parse"] = parse_confidence(prev.get("content", ""))
        if prev["confidence"] is not None or prev.get("attempts", 1) >= 3:
            return prev, True
    messages = build_messages(trial, harness, condition, datasets_root)
    rec = {k: v for k, v in trial.items() if k != "dir"}
    rec.update(harness=harness, condition=condition, think=think, max_tokens=max_tokens,
               n_messages=len(messages), attempts=(prev.get("attempts", 1) + 1) if prev else 1)
    if prev:
        rec["previous_unparsed_content"] = prev.get("content", "")
    try:
        rec.update(call_model(client, model, messages, max_tokens, think))
        rec["confidence"], rec["parse"] = parse_confidence(rec["content"])
        if condition == "lastturn":
            rec["reward_orig"], rec["got_orig"] = trial["reward"], trial["got"]
            rec["got"] = parse_answer(rec["content"])
            rec["reward"] = grade(rec["got"], trial["expected"])
    except Exception as e:  # keep going; the record says what failed
        rec.update(error=f"{type(e).__name__}: {e}", confidence=None, parse="error")
    with open(out_path, "w") as f:
        json.dump(rec, f, indent=1, ensure_ascii=False)
    return rec, False


# ----------------------------------------------------------------------------- metrics

def ece(conf, y, bins=10):
    n = len(conf)
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, c in enumerate(conf) if (lo <= c < hi) or (b == bins - 1 and c == 1.0)]
        if not idx:
            continue
        pm = sum(conf[i] for i in idx) / len(idx)
        ym = sum(y[i] for i in idx) / len(idx)
        total += len(idx) / n * abs(pm - ym)
    return total


def kuiper(conf, y):
    """Confidence-weighted Kuiper measure as transcribed in claude_docs/05 §3.2."""
    order = sorted(range(len(conf)), key=lambda i: conf[i])
    n = len(conf)
    c, cmax, cmin = 0.0, 0.0, 0.0
    for i in order:
        c += conf[i] * (y[i] - conf[i]) / n
        cmax, cmin = max(cmax, c), min(cmin, c)
    return cmax - cmin


def brier(conf, y):
    return sum((c - t) ** 2 for c, t in zip(conf, y)) / len(conf)


def auroc(conf, y):
    pos = [c for c, t in zip(conf, y) if t == 1]
    neg = [c for c, t in zip(conf, y) if t == 0]
    if not pos or not neg:
        return float("nan")
    s = 0.0
    for p in pos:
        for q in neg:
            s += 1.0 if p > q else 0.5 if p == q else 0.0
    return s / (len(pos) * len(neg))


def selective(conf, y, thr):
    keep = [(c, t) for c, t in zip(conf, y) if c >= thr]
    if not keep:
        return 0.0, float("nan")
    return len(keep) / len(conf), sum(t for _, t in keep) / len(keep)


def reliability(conf, y, bins=5):
    rows = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, c in enumerate(conf) if (lo <= c < hi) or (b == bins - 1 and c == 1.0)]
        if idx:
            rows.append((f"[{lo:.1f}, {hi:.1f}{']' if b == bins - 1 else ')'}", len(idx),
                         sum(conf[i] for i in idx) / len(idx), sum(y[i] for i in idx) / len(idx)))
    return rows


def metrics(recs):
    recs = [r for r in recs if r.get("confidence") is not None]
    if not recs:
        return None
    conf = [r["confidence"] for r in recs]
    y = [1.0 if r["reward"] == 1.0 else 0.0 for r in recs]
    m = {
        "n": len(recs),
        "accuracy": sum(y) / len(y),
        "mean_confidence": sum(conf) / len(conf),
        "ece10": ece(conf, y),
        "kuiper": kuiper(conf, y),
        "brier": brier(conf, y),
        "auroc": auroc(conf, y),
        "reliability5": reliability(conf, y),
    }
    for thr in (0.5, 0.7, 0.9):
        cov, acc = selective(conf, y, thr)
        m[f"cov@{thr}"], m[f"acc@{thr}"] = cov, acc
    return m


def summarise(out_dir: str) -> str:
    recs = [json.load(open(p)) for p in sorted(glob.glob(os.path.join(out_dir, "*.json"))) if not p.endswith("summary.json")]
    for r in recs:  # always score with the current parser
        r["confidence"], r["parse"] = parse_confidence(r.get("content", ""))
    lines = [f"# {out_dir}", f"records: {len(recs)}, unparsed/errors: {sum(1 for r in recs if r.get('confidence') is None)}",]
    if any("reward_orig" in r for r in recs):
        lt = [r for r in recs if "reward_orig" in r]
        lines.append(
            f"lastturn: answers re-sampled; new acc {sum(r['reward'] for r in lt)/len(lt):.3f} vs original "
            f"{sum(r['reward_orig'] for r in lt)/len(lt):.3f}; same answer as the trajectory in "
            f"{sum(1 for r in lt if (r.get('got') or '').lower().lstrip('$') == (r.get('got_orig') or '').lower())}/{len(lt)}; "
            f"no Answer line parsed in {sum(1 for r in lt if r.get('got') is None)}"
        )
    lines += [
             f"truncated (finish_reason=length): {sum(1 for r in recs if r.get('finish_reason') == 'length')}", ""]
    lines.append("| subset | n | acc | mean conf | ECE | Kuiper | Brier | AUROC | cov/acc @0.5 | @0.7 | @0.9 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    summary = {}
    for name, sub in (("all", recs), ("sara-binary", [r for r in recs if r["subdomain"] == "sara-binary"]),
                      ("sara-numeric", [r for r in recs if r["subdomain"] == "sara-numeric"])):
        m = metrics(sub)
        if not m:
            continue
        summary[name] = m
        lines.append(
            f"| {name} | {m['n']} | {m['accuracy']:.3f} | {m['mean_confidence']:.3f} | {m['ece10']:.3f} | "
            f"{m['kuiper']:.3f} | {m['brier']:.3f} | {m['auroc']:.3f} | "
            f"{m['cov@0.5']:.2f}/{m['acc@0.5']:.2f} | {m['cov@0.7']:.2f}/{m['acc@0.7']:.2f} | {m['cov@0.9']:.2f}/{m['acc@0.9']:.2f} |"
        )
    for name in ("sara-binary", "sara-numeric"):
        if name in summary:
            lines.append(f"\nreliability {name} (bin, n, mean conf, acc):")
            for row in summary[name]["reliability5"]:
                lines.append(f"  {row[0]:12s} n={row[1]:3d}  conf={row[2]:.2f}  acc={row[3]:.2f}")
    with open(os.path.join(out_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1)
    return "\n".join(lines)


# ----------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--job")
    ap.add_argument("--harness", choices=["t2", "kira"])
    ap.add_argument("--condition", choices=["incontext", "answeronly", "lastturn"], default="incontext")
    ap.add_argument("--out")
    ap.add_argument("--summary", help="only recompute metrics for this output dir")
    ap.add_argument("--datasets-root", default="harbor-deonticbench/datasets/deonticbench-direct")
    ap.add_argument("--base-url", default="http://127.0.0.1:9009/v1")
    ap.add_argument("--api-key", default=os.environ.get("VLLM_API_KEY", "token-abc123"))
    ap.add_argument("--model", default="qwen3.5-35b-a3b")
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--no-think", action="store_true", help="disable thinking for the confidence turn")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--subdomain", choices=["sara-numeric", "sara-binary"], help="only trials of this subdomain")
    a = ap.parse_args()

    if a.summary:
        print(summarise(a.summary))
        return
    if not (a.job and a.harness and a.out):
        ap.error("--job, --harness and --out are required unless --summary is given")
    from openai import OpenAI

    client = OpenAI(base_url=a.base_url, api_key=a.api_key, timeout=1800)
    os.makedirs(a.out, exist_ok=True)
    trials = load_trials(a.job)
    if a.subdomain:
        trials = [t for t in trials if t["subdomain"] == a.subdomain]
    trials = trials[: a.limit]
    print(f"{len(trials)} trials from {a.job}; harness={a.harness} condition={a.condition} think={not a.no_think}", flush=True)
    done = 0
    with ThreadPoolExecutor(max_workers=a.concurrency) as ex:
        futs = {
            ex.submit(run_trial, t, a.harness, a.condition, a.datasets_root, client, a.model, a.max_tokens, not a.no_think, a.out): t
            for t in trials
        }
        for fut in as_completed(futs):
            rec, cached = fut.result()
            done += 1
            print(
                f"[{done}/{len(trials)}] {rec['trial'][-30:]:30s} R={rec['reward']:.0f} conf={rec.get('confidence')} "
                f"({rec.get('parse')}) tok={(rec.get('usage') or {}).get('completion_tokens')} "
                f"{'cached' if cached else str(rec.get('elapsed_s')) + 's'} {rec.get('error', '')}",
                flush=True,
            )
    print()
    print(summarise(a.out))


if __name__ == "__main__":
    main()
