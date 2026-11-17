#!/usr/bin/env python3
"""Aggregate numbers of solver-checks runs (AGENT=t2sc, tools/dar_terminus2_checks.py).

    tools/sc_run_report.py <job_dir> [<job_dir> ...] [--trials] [--findings]

Reads result.json and agent/rubric_verifier/{summary,round-N}.json of every finished trial.
Prints accuracy with a Wilson interval, finish reasons, verifier rounds, the status of every
check per round, how many findings carried a quote found in the rule document, what happened
to the answer after a round that did not pass (analysis only: reads the reward, never a gold
label), and tokens per check. ``--trials`` adds one line per trial, ``--findings`` prints every
finding. Cost is computed by tools/inference_cost.py. Run from the repo root.
Used for claude_docs/10.
"""

import argparse
import glob
import json
import os
import re
import statistics as st
from collections import Counter, defaultdict
from math import sqrt


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def answer_line(text: str) -> str | None:
    lines = [ln for ln in (text or "").splitlines() if re.match(r"(?i)^answer:", ln)]
    return lines[-1].split(":", 1)[1].strip() if lines else None


def load(job: str) -> dict[str, dict]:
    out = {}
    for d in sorted(glob.glob(os.path.join(job, "*", ""))):
        rp = os.path.join(d, "result.json")
        if not os.path.exists(rp):
            continue
        res = json.load(open(rp))
        vr = res.get("verifier_result") or {}
        reward = (vr.get("rewards") or {}).get("reward")
        sp = os.path.join(d, "agent/rubric_verifier/summary.json")
        summary = json.load(open(sp)) if os.path.exists(sp) else {}
        rounds = [
            json.load(open(f))
            for f in sorted(
                glob.glob(os.path.join(d, "agent/rubric_verifier/round-?.json"))
            )
        ]
        out[res["task_name"]] = dict(
            dir=d,
            reward=reward,
            exception=(res.get("exception_info") or {}).get("exception_type"),
            summary=summary,
            rounds=rounds,
        )
    return out


def short(task: str) -> str:
    return re.sub(r"^deonticbench-\w+-sara-", "", task).replace(
        "numeric-tax-case-", "n"
    )


def report(job: str, show_trials: bool, show_findings: bool) -> None:
    d = load(job)
    n = len(d)
    graded = {t: x for t, x in d.items() if x["reward"] is not None}
    k = sum(1 for x in graded.values() if x["reward"] == 1)
    lo, hi = wilson(k, len(graded))
    print(f"== {job}")
    print(
        f"trials {n}, graded {len(graded)}, correct {k} "
        f"({k / max(len(graded), 1):.1%}, Wilson 95% {lo:.1%}-{hi:.1%})"
    )
    exc = Counter(x["exception"] for x in d.values() if x["exception"])
    if exc:
        print("exceptions:", dict(exc))
    fin = defaultdict(lambda: [0, 0])
    for x in graded.values():
        f = fin[x["summary"].get("finish_reason") or "(none)"]
        f[0] += 1
        f[1] += x["reward"] == 1
    print("finish reason (trials, correct):", {a: tuple(b) for a, b in fin.items()})
    nr = [len(x["rounds"]) for x in d.values()]
    print("verifier rounds per trial:", dict(sorted(Counter(nr).items())))
    print(
        "rebuttals:",
        sum(x["summary"].get("n_rebuttals") or 0 for x in d.values()),
        "| verifier calls that used no round:",
        sum(x["summary"].get("n_free_verifier_calls") or 0 for x in d.values()),
    )

    # status of every check, by round
    by = defaultdict(Counter)
    toks = defaultdict(lambda: [[], [], []])
    n_find = Counter()
    for x in d.values():
        for r in x["rounds"]:
            for c in r["checks"]:
                if c.get("reused"):
                    continue
                by[(c["check"], r["round"])][c["status"]] += 1
                t = toks[c["check"]]
                t[0].append(c.get("prompt_tokens", 0))
                t[1].append(c.get("completion_tokens", 0))
                t[2].append(c.get("elapsed_sec", 0))
                for f in c.get("findings") or []:
                    n_find[(c["check"], f.get("quote_found"))] += 1
                n_find[(c["check"], "repairs")] += len(c.get("repairs") or [])
                n_find[(c["check"], "fallback")] += bool(c.get("thinking_fallback"))
    print("check status by round (runs only):")
    for (check, rnd), cnt in sorted(by.items()):
        print(f"  {check:20s} round {rnd}: {dict(cnt)}")
    print("findings by quote check (True = found, False = not found, None = no quote):")
    for check in sorted({c for c, _ in n_find}):
        print(
            f"  {check:20s} found {n_find[(check, True)]}, not found {n_find[(check, False)]}, "
            f"no quote {n_find[(check, None)]}; repair requests {n_find[(check, 'repairs')]}, "
            f"no-thinking fallbacks {n_find[(check, 'fallback')]}"
        )
    print(
        "tokens and seconds per check run (median prompt / completion / seconds; runs):"
    )
    for check, (p, c, s) in sorted(toks.items()):
        print(
            f"  {check:20s} {st.median(p):.0f} / {st.median(c):.0f} / {st.median(s):.0f}; {len(p)}"
        )

    # first report vs reward; what the solver did after a round that did not pass
    first = defaultdict(lambda: [0, 0])
    after = Counter()
    for x in graded.values():
        if not x["rounds"]:
            first["no round"][0] += 1
            first["no round"][1] += x["reward"] == 1
            continue
        r1 = x["rounds"][0]
        key = "round 1 passed" if r1["passed"] else "round 1 not passed"
        first[key][0] += 1
        first[key][1] += x["reward"] == 1
        if not r1["passed"]:
            final = answer_line(x["summary"].get("final_answer") or "") or answer_line(
                x["rounds"][-1]["answer"]
            )
            changed = final != answer_line(r1["answer"])
            after[
                ("answer changed" if changed else "answer kept")
                + (", correct" if x["reward"] == 1 else ", wrong")
            ] += 1
    print("first report (trials, correct):", {a: tuple(b) for a, b in first.items()})
    print("after a first report that did not pass:", dict(after))

    if show_trials:
        for t, x in sorted(d.items()):
            rs = " | ".join(
                ("P" if r["passed"] else "N")
                + ("r" if r.get("rebuttal") else "")
                + ":"
                + ",".join(
                    c["status"][:2] + ("*" if c.get("reused") else "")
                    for c in r["checks"]
                )
                + f" [{answer_line(r['answer'])}]"
                for r in x["rounds"]
            )
            print(
                f"  {short(t):22s} reward {x['reward']} {x['summary'].get('finish_reason')}: {rs}"
            )
    if show_findings:
        for t, x in sorted(d.items()):
            for r in x["rounds"]:
                for c in r["checks"]:
                    if c.get("reused"):
                        continue
                    for f in c.get("findings") or []:
                        print(
                            f"  {short(t)} r{r['round']} {c['check']} [{c['status']}, quote "
                            f"{f.get('quote_found')}] {f['issue'][:300]}"
                        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("jobs", nargs="+")
    ap.add_argument("--trials", action="store_true")
    ap.add_argument("--findings", action="store_true")
    a = ap.parse_args()
    for job in a.jobs:
        report(job, a.trials, a.findings)


if __name__ == "__main__":
    main()
