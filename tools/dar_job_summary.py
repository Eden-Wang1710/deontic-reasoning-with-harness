#!/usr/bin/env python3
"""Summarise a Harbor job directory produced by the DAR harness.

Usage: python tools/dar_job_summary.py <jobs_dir>/<job_name> [--trials]

Prints per-subdomain accuracy (and macro-F1 for sara-binary), timing and token usage, counting
unfinished trials separately. With --trials, also one line per trial. Works while a job is running.
"""
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import datetime


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_trial(tdir):
    res = os.path.join(tdir, "result.json")
    out = {"name": os.path.basename(tdir), "done": os.path.exists(res)}
    out["subdomain"] = out["name"].split("__")[0].replace("deonticbench-direct-", "").replace(
        "deonticbench-zeroshot-", "").replace("deonticbench-fewshot-", "")
    out["subdomain"] = "-".join(out["subdomain"].split("-")[:2])
    traj = os.path.join(tdir, "agent", "trajectory.json")
    if os.path.exists(traj):
        try:
            t = json.load(open(traj))
            steps = [s for s in t.get("steps", []) if s.get("source") == "agent"]
            out["steps"] = len(steps)
            fm = t.get("final_metrics") or {}
            out["prompt_tok"] = fm.get("total_prompt_tokens")
            out["compl_tok"] = fm.get("total_completion_tokens")
            out["think_chars"] = sum(len(s.get("reasoning_content") or "") for s in steps)
        except Exception:
            pass
    if out["done"]:
        d = json.load(open(res))
        out["reward"] = ((d.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        out["exception"] = (d.get("exception_info") or {}).get("exception_type") if d.get("exception_info") else None
        if d.get("started_at") and d.get("finished_at"):
            out["wall_s"] = (_ts(d["finished_at"]) - _ts(d["started_at"])).total_seconds()
        vs = os.path.join(tdir, "verifier", "test-stdout.txt")
        if os.path.exists(vs):
            for line in open(vs):
                if line.startswith("Expected:"):
                    out["expected"] = line.split(":", 1)[1].strip()
                elif line.startswith("Got:"):
                    out["got"] = line.split(":", 1)[1].strip()
    return out


def macro_f1(pairs):
    """pairs: list of (expected, got) strings, case-insensitive."""
    labels = sorted({e.lower() for e, _ in pairs})
    f1s = []
    for lab in labels:
        tp = sum(1 for e, g in pairs if e.lower() == lab and (g or "").lower() == lab)
        fp = sum(1 for e, g in pairs if e.lower() != lab and (g or "").lower() == lab)
        fn = sum(1 for e, g in pairs if e.lower() == lab and (g or "").lower() != lab)
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * p * r / (p + r) if p + r else 0.0)
    return sum(f1s) / len(f1s) if f1s else float("nan")


def main():
    job = sys.argv[1].rstrip("/")
    show_trials = "--trials" in sys.argv
    trials = [load_trial(d) for d in sorted(glob.glob(os.path.join(job, "*")) ) if os.path.isdir(d)]
    by = defaultdict(list)
    for t in trials:
        by[t["subdomain"]].append(t)
    print(f"job: {job}")
    print(f"trials: {len(trials)} total, {sum(t['done'] for t in trials)} finished, "
          f"{sum(1 for t in trials if t.get('exception'))} with exceptions")
    for sub, ts in sorted(by.items()):
        done = [t for t in ts if t["done"]]
        ok = [t for t in done if not t.get("exception")]
        correct = sum(1 for t in ok if t.get("reward") == 1.0)
        line = f"  {sub:14s} finished {len(done):3d}/{len(ts):3d}  correct {correct:3d}/{len(ok):3d}"
        if ok:
            line += f"  acc {correct/len(ok):.3f}"
            if sub == "sara-binary":
                line += f"  macroF1 {macro_f1([(t.get('expected',''), t.get('got','')) for t in ok]):.3f}"
            walls = [t["wall_s"] for t in ok if "wall_s" in t]
            comps = [t["compl_tok"] for t in ok if t.get("compl_tok") is not None]
            if walls:
                line += f"  mean wall {sum(walls)/len(walls)/60:.1f} min"
            if comps:
                line += f"  mean compl tok {sum(comps)/len(comps):,.0f}"
            n_to = sum(1 for t in done if t.get("exception"))
            if n_to:
                line += f"  exceptions {n_to}"
        print(line)
    if show_trials:
        print()
        for t in trials:
            status = ("R=%.0f" % t["reward"]) if t.get("reward") is not None else ("EXC:" + t["exception"] if t.get("exception") else "running")
            print(f"  {t['name'][:48]:48s} {status:14s} exp={t.get('expected','?'):12s} got={t.get('got','?'):12s} "
                  f"steps={t.get('steps','?')} compl={t.get('compl_tok','?')} wall={int(t.get('wall_s',0))}s")


if __name__ == "__main__":
    main()
