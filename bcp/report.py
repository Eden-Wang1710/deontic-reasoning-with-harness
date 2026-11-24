#!/usr/bin/env python3
"""Numbers of one BrowseComp-Plus job (claude_docs/12 §7), without any question text.

  report.py --job-dir <data>/jobs/<job> [--out summary.json]

Per trial, from the agent's own files (usage.json, timing.json, trajectory.ctx.json) and the
tools server's call log (<job>.tools.jsonl): steps, model calls, context edits, rollbacks,
how the run ended, FLOPs with prefix-cache reuse, searches, documents opened, recall of the
evidence and gold documents among everything the tools returned, and the judge's verdict if
bcp/judge.py has run. Standard library only; run with `python -I`.
"""
import argparse, json, re, statistics
from pathlib import Path


def qrels(path: Path) -> dict[str, set[str]]:
    rel: dict[str, set[str]] = {}
    for line in open(path):
        qid, _, docid, r = line.split()
        if int(r) > 0:
            rel.setdefault(qid, set()).add(docid)
    return rel


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-dir", required=True)
    ap.add_argument("--data", default=str(Path.home() / "data/browsecomp-plus"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    job = Path(a.job_dir); data = Path(a.data)
    ev = qrels(data / "qrels/qrel_evidence.txt"); gd = qrels(data / "qrels/qrel_golds.txt")
    seen: dict[str, set[str]] = {}; opened: dict[str, set[str]] = {}; n_search: dict[str, int] = {}
    # v0.1 (claude_docs/12 §5): role calls, state table, verifier, gate, source of the answer
    role: dict[str, dict] = {}; passed: dict[str, set[str]] = {}
    def R(q):
        return role.setdefault(q, {"reader_calls": 0, "reader_prompt_tokens": 0, "reader_completion_tokens": 0,
                                   "verifier_calls": 0, "verifier_prompt_tokens": 0, "verifier_completion_tokens": 0,
                                   "rereads": 0, "submits_refused": 0,
                                   "reader_cached_prompt_tokens": 0, "reader_docs": 0,
                                   "reader_docs_opened_further": 0, "reader_actions": 0})
    log = job.parent / f"{job.name}.tools.jsonl"
    if log.exists():
        for line in open(log, encoding="utf-8"):
            r = json.loads(line); q = r.get("qid", "")
            if r["op"] == "search":
                seen.setdefault(q, set()).update(r["docids"]); n_search[q] = n_search.get(q, 0) + 1
            elif r["op"] == "get_document" and r.get("found"):
                opened.setdefault(q, set()).add(r["docid"])
            if r["op"] == "search" and "strong" in r:
                passed.setdefault(q, set()).update(r["strong"] + r["weak"])
            elif r["op"] == "llm":
                x = R(q); k = r["role"]
                x[f"{k}_calls"] += 1; x[f"{k}_prompt_tokens"] += r.get("prompt_tokens") or 0
                x[f"{k}_completion_tokens"] += r.get("completion_tokens") or 0
                if k == "reader":       # v0.2: the part of a prompt that repeats the same conversation
                    x["reader_cached_prompt_tokens"] += r.get("cached_prompt_tokens") or 0
            elif r["op"] == "reader_doc":
                x = R(q); x["reader_docs"] += 1; x["reader_actions"] += len(r["actions"])
                x["reader_docs_opened_further"] += bool(r["actions"])
            elif r["op"] == "read" and r.get("found"):
                R(q)["rereads"] += 1; opened.setdefault(q, set()).add(r["docid"])
                if r.get("match") in ("strong", "weak"):
                    passed.setdefault(q, set()).add(r["docid"])
            elif r["op"] == "verify" and not r.get("stored"):
                R(q)["verdict"] = r.get("verdict")
            elif r["op"] == "submit":
                if r["accepted"]:
                    R(q)["submit_verified"] = r["verified"]
                else:
                    R(q)["submits_refused"] += 1
            elif r["op"] == "finalize":
                R(q).update(answer_source=r["answer_source"], state_versions=r["state_versions"],
                            documents_read=r["documents_read"], documents_strong=r.get("documents_strong"),
                            documents_weak=r.get("documents_weak"))
    verdicts = {}
    if (job / "judge/scores.json").exists():
        verdicts = {r["trial"]: r for r in json.loads((job / "judge/scores.json").read_text())["rows"]}

    rows = []
    for d in sorted(p for p in job.iterdir() if p.is_dir() and re.match(r"bcp-q\d+", p.name)):
        qid = re.match(r"bcp-q(\d+)", d.name).group(1)
        row = {"qid": qid, "trial": d.name, "answer_file": (d / "verifier/answer.md").exists()}
        u = d / "agent/usage.json"
        if u.exists():
            u = json.loads(u.read_text())
            row.update(lm_calls=u["n_lm_calls"], commands=u["n_bash"], context_edits=u["n_ctx_syncs_real"],
                       free_edit_turns=u["free_ctx_turns"], rollbacks=u["n_retry_on_limit"],
                       nudges=u["n_nudges"], completion_tokens=u["completion_tokens"],
                       ended_on_context_limit=bool(u["budget_finalized"]))
            tm = json.loads((d / "agent/timing.json").read_text())
            row["task_steps"] = max([x.get("step", 0) for x in tm] or [0])
            row["llm_seconds"] = round(sum(x.get("llm_s", 0) for x in tm))
            traj = json.loads((d / "agent/trajectory.json").read_text())
            last_cmds = [json.loads(m["tool_calls"][0]["function"]["arguments"]).get("command", "")
                         for m in traj if m.get("role") == "assistant" and m.get("tool_calls")]
            row["submitted"] = bool(last_cmds) and "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in last_cmds[-1]
            row["end"] = ("submit" if row["submitted"] else "context_limit" if row["ended_on_context_limit"]
                          else "step_limit" if row["task_steps"] >= u["max_steps"] else "other")
            ctx = d / "agent/trajectory.ctx.json"
            if ctx.exists():
                k = json.loads(ctx.read_text())["final_metrics"]["extra"].get("kv_cache_flops", {})
                row["pflops"] = round(k.get("cache_aware_flops", 0) / 1e15, 3)
                row["uncached_prefill_tokens"] = k.get("cache_aware_prefill_tokens")
        else:
            row["end"] = "no_usage_file"
        got = seen.get(qid, set()) | opened.get(qid, set())
        row.update(searches=n_search.get(qid, 0), documents_opened=len(opened.get(qid, set())),
                   evidence_recall=round(len(got & ev[qid]) / len(ev[qid]), 3) if ev.get(qid) else None,
                   gold_recall=round(len(got & gd[qid]) / len(gd[qid]), 3) if gd.get(qid) else None)
        if qid in role:
            x = role[qid]; row.update(x)
            # Role calls at the same linear rate as CLM's formula (2 x body parameters x tokens),
            # every prompt token counted as uncached and without the attention term.
            row["role_pflops"] = round(2 * 6.9196e9 * sum(x[f"{k}_{t}_tokens"] for k in ("reader", "verifier")
                                                          for t in ("prompt", "completion")) / 1e15, 3)
            row["total_pflops"] = round(row.get("pflops", 0) + row["role_pflops"], 3)
            # The same with the prefix a reader conversation repeats counted once (v0.2's reader
            # makes several calls on one document; in v0.1 every call is a new conversation).
            row["role_pflops_conversation_cache"] = round(row["role_pflops"] - 2 * 6.9196e9 * x["reader_cached_prompt_tokens"] / 1e15, 3)
            row["total_pflops_conversation_cache"] = round(row.get("pflops", 0) + row["role_pflops_conversation_cache"], 3)
            pd = passed.get(qid, set())
            row["evidence_recall_passed_on"] = round(len(pd & ev[qid]) / len(ev[qid]), 3) if ev.get(qid) else None
            row["gold_recall_passed_on"] = round(len(pd & gd[qid]) / len(gd[qid]), 3) if gd.get(qid) else None
        v = verdicts.get(d.name)
        if v:
            row.update(correct=v["correct"], confidence=v["confidence"], judge_status=v["status"])
        rows.append(row)

    def mean(key):
        xs = [r[key] for r in rows if r.get(key) is not None]
        return round(statistics.mean(xs), 3) if xs else None
    ends: dict[str, int] = {}
    for r in rows:
        ends[r["end"]] = ends.get(r["end"], 0) + 1
    summary = {"job": job.name, "trials": len(rows), "answer_files": sum(r["answer_file"] for r in rows),
               "ended": ends, "correct": sum(bool(r.get("correct")) for r in rows) if verdicts else None,
               "mean": {k: mean(k) for k in ("lm_calls", "task_steps", "context_edits", "rollbacks", "searches",
                                             "documents_opened", "completion_tokens", "pflops", "llm_seconds",
                                             "evidence_recall", "gold_recall", "reader_calls",
                                             "reader_prompt_tokens", "reader_completion_tokens", "rereads",
                                             "verifier_calls", "state_versions", "role_pflops", "total_pflops",
                                             "reader_cached_prompt_tokens", "reader_docs", "reader_docs_opened_further",
                                             "reader_actions", "role_pflops_conversation_cache",
                                             "total_pflops_conversation_cache",
                                             "evidence_recall_passed_on", "gold_recall_passed_on")}}
    if role:
        count = lambda key: {str(v): sum(1 for r in rows if r.get(key) == v) for v in {r.get(key) for r in rows}}  # noqa: E731
        summary["answer_source"] = count("answer_source"); summary["verdict"] = count("verdict")
        summary["submit_verified"] = count("submit_verified")
    print(json.dumps(summary, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1) + "\n")


if __name__ == "__main__":
    main()
