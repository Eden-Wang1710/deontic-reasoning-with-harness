#!/usr/bin/env python3
"""Summarise a Harbor job produced by the rubric-verifier agent (AGENT=t2verify).

Usage:
    python tools/verifier_job_summary.py <job_dir> [--baseline <job_dir>] [--trials] [--json out.json]

Per subdomain: accuracy, rounds used, continue/finish decisions, answer changes between the
first completion claim and the end (right→wrong / wrong→right within the trial), per-rubric
status counts on round 1, confidence metrics (AUROC, ECE, Brier, Kuiper, selective accuracy)
for (a) the solver's stated confidence and (b) a rubric score = number of rubrics not YES on
the last round, and verifier cost. With --baseline, a per-task comparison with another job
(e.g. sara-t2fix-run1): agree / gained / lost. With --trials, one line per trial.

Reads: <trial>/result.json, verifier/test-stdout.txt, agent/trajectory.json,
agent/rubric_verifier/{summary.json,round-N.json}. Works while a job is running
(unfinished trials are counted separately). Metric functions are shared with
tools/verbalized_confidence.py.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dar_job_summary import load_trial, macro_f1  # noqa: E402
from verbalized_confidence import auroc, brier, ece, kuiper, selective  # noqa: E402

RUBRIC_NAMES = (
    "tool_call_integrity", "statute_coverage", "grounded_reasoning",
    "provision_selection", "tool_assisted_computation",
)

RUBRICS = [
    "tool_call_integrity",
    "statute_coverage",
    "grounded_reasoning",
    "provision_selection",
    "tool_assisted_computation",
]


def _norm_answer(text: str | None) -> str | None:
    if not text:
        return None
    for line in text.splitlines():
        if line.lower().startswith("answer:"):
            return line.split(":", 1)[1].strip()
    return text.strip()


def _grade(answer: str | None, expected: str | None) -> float | None:
    """DAR's test.sh rule: exact integer match (decimals stripped) or case-insensitive string."""
    if answer is None or expected is None:
        return None
    a, e = answer.strip(), expected.strip()
    if e.lstrip("-").isdigit():
        return 1.0 if a.split(".")[0] == e else 0.0
    return 1.0 if a.lower() == e.lower() else 0.0


def _task_name(tdir: str) -> str:
    """The task directory name (e.g. deonticbench-direct-sara-numeric-tax-case-15).

    Trial directory names carry only the subdomain plus a random suffix, so the task identity
    has to come from the trial's config.json (written at start) or result.json.
    """
    for fname in ("config.json", "result.json"):
        path = os.path.join(tdir, fname)
        if os.path.exists(path):
            try:
                d = json.load(open(path))
            except Exception:
                continue
            if fname == "config.json":
                task_path = (d.get("task") or {}).get("path")
            else:
                task_path = (d.get("task_id") or {}).get("path") or d.get("task_name")
            if task_path:
                return os.path.basename(task_path.rstrip("/"))
    return os.path.basename(tdir).split("__")[0]


def load_verifier_trial(tdir: str) -> dict:
    out = load_trial(tdir)
    out["task"] = _task_name(tdir)
    vdir = os.path.join(tdir, "agent", "rubric_verifier")
    out["rounds"] = []
    out["record_ok"] = None  # run-2: answer file has the three sections and ends with Answer:
    for path in sorted(glob.glob(os.path.join(vdir, "round-*.json"))):
        try:
            r = json.load(open(path))
        except Exception:
            continue
        raw = r.get("answer") or ""
        if raw and raw != "(no answer file)":
            lines = [ln for ln in raw.strip().splitlines() if ln.strip()]
            out["record_ok"] = (
                "## Statute" in raw and "## Reasoning" in raw and "## Answer" in raw
                and bool(lines) and lines[-1].lower().startswith("answer:")
            )
        statuses = {}
        report = r.get("report") or {}
        # Some verifier replies wrap the whole reply object inside "report"
        # ({"analysis", "commands", "report": {...}}); unwrap to the rubrics.
        while isinstance(report, dict) and "rubrics" not in report and isinstance(report.get("report"), dict):
            report = report["report"]
        items = list((report.get("rubrics", []) if isinstance(report, dict) else []) or [])
        if isinstance(report, dict):
            listed = {it.get("name") for it in items if isinstance(it, dict)}
            for name in RUBRIC_NAMES:
                entry = report.get(name)
                if name not in listed and isinstance(entry, dict) and "status" in entry:
                    items.append({"name": name, **entry})
        for item in items:
            if isinstance(item, dict) and item.get("name"):
                statuses[item["name"]] = str(item.get("status", "UNKNOWN")).upper()
        out["rounds"].append(
            {
                "round": r.get("round"),
                "answer": _norm_answer(r.get("answer")),
                "statuses": statuses,
                "n_not_yes": sum(1 for s in statuses.values() if s not in ("YES", "NA")),
                "has_report": r.get("report") is not None,
                "steps_used": r.get("steps_used"),
                "parse_failures": r.get("parse_failures"),
                "context_exceeded": r.get("context_exceeded"),
                "prompt_tokens": r.get("prompt_tokens"),
                "completion_tokens": r.get("completion_tokens"),
                "elapsed_sec": r.get("elapsed_sec"),
                "n_commands": len(r.get("commands") or []),
                "n_unresolved": len((r.get("report") or {}).get("unresolved") or []),
            }
        )
    spath = os.path.join(vdir, "summary.json")
    if os.path.exists(spath):
        try:
            s = json.load(open(spath))
            out["final_confidence"] = s.get("final_confidence")
            out["decisions"] = s.get("decisions") or []
            out["finish_with_commands"] = s.get("finish_with_commands")
            out["unverified_finish"] = s.get("answer_changed_after_last_round")
            out["completed_via_confirmation"] = s.get("completed_via_confirmation")
            out["verifier_prompt_tok"] = s.get("verifier_prompt_tokens")
            out["verifier_compl_tok"] = s.get("verifier_completion_tokens")
        except Exception:
            pass
    res = os.path.join(tdir, "result.json")
    if os.path.exists(res):
        d = json.load(open(res))
        ae = d.get("agent_execution") or {}
        if ae.get("started_at") and ae.get("finished_at"):
            from dar_job_summary import _ts

            out["agent_s"] = (_ts(ae["finished_at"]) - _ts(ae["started_at"])).total_seconds()
    # First-claim answer vs final answer, graded with the same rule
    if out["rounds"]:
        out["first_answer"] = out["rounds"][0]["answer"]
        out["first_reward"] = _grade(out["first_answer"], out.get("expected"))
    return out


def _fmt(x, nd=2):
    if x is None:
        return "—"
    if isinstance(x, float):
        return "nan" if x != x else f"{x:.{nd}f}"
    return str(x)


def conf_metrics(conf: list[float], y: list[int]) -> dict:
    if not conf:
        return {}
    m = {
        "n": len(conf),
        "acc": sum(y) / len(y),
        "mean_conf": sum(conf) / len(conf),
        "auroc": auroc(conf, y),
        "ece": ece(conf, y),
        "brier": brier(conf, y),
        "kuiper": kuiper(conf, y),
    }
    for thr in (0.5, 0.7, 0.9):
        cov, acc = selective(conf, y, thr)
        m[f"cov@{thr}"] = cov
        m[f"acc@{thr}"] = acc
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job_dir")
    ap.add_argument("--baseline", help="another job dir to compare per task (same tasks)")
    ap.add_argument("--trials", action="store_true")
    ap.add_argument("--json", help="write the per-trial records to this file")
    args = ap.parse_args()

    tdirs = sorted(
        d for d in glob.glob(os.path.join(args.job_dir, "*")) if os.path.isdir(d)
    )
    trials = [load_verifier_trial(d) for d in tdirs]
    done = [t for t in trials if t["done"]]
    running = [t for t in trials if not t["done"]]
    print(f"{args.job_dir}: {len(done)} finished, {len(running)} running/unfinished")

    baseline = {}
    if args.baseline:
        for d in glob.glob(os.path.join(args.baseline, "*")):
            if os.path.isdir(d):
                b = load_trial(d)
                if b["done"]:
                    baseline[_task_name(d)] = b

    by_sub = defaultdict(list)
    for t in done:
        by_sub[t["subdomain"]].append(t)

    for sub, ts in sorted(by_sub.items()):
        ok = [t for t in ts if t.get("exception") is None]
        n = len(ts)
        correct = sum(1 for t in ok if t.get("reward") == 1.0)
        print(f"\n== {sub}: n={n}, correct={correct}, acc={correct / n:.3f}" if n else f"\n== {sub}")
        if sub == "sara-binary":
            pairs = [(t.get("expected"), t.get("got")) for t in ok if t.get("expected")]
            print(f"   macro-F1 {macro_f1(pairs):.3f}")
        exc = [t for t in ts if t.get("exception")]
        if exc:
            print(f"   exceptions: {Counter(t['exception'] for t in exc)}")
        over600 = sum(1 for t in ok if (t.get("agent_s") or 0) > 600)
        over1800 = sum(1 for t in ok if (t.get("agent_s") or 0) >= 1800)
        agent_s = sorted(t.get("agent_s") or 0 for t in ok)
        if agent_s:
            med = agent_s[len(agent_s) // 2]
            print(
                f"   agent time median {med:.0f} s, max {agent_s[-1]:.0f} s; "
                f">600 s: {over600}; >=1800 s: {over1800}; "
                f"acc at 600 s: {sum(1 for t in ok if t.get('reward') == 1.0 and (t.get('agent_s') or 0) <= 600) / n:.3f}"
            )

        # Rounds and decisions
        rounds_used = Counter(len(t["rounds"]) for t in ok)
        print(f"   verifier rounds per trial: {dict(sorted(rounds_used.items()))}")
        n_cont = sum(
            1 for t in ok for d in (t.get("decisions") or []) if not d.get("task_complete")
        )
        n_fin = sum(
            1
            for t in ok
            for d in (t.get("decisions") or [])
            if d.get("task_complete") and not d.get("extra_confidence_turn")
        )
        n_fin_cmd = sum(1 for t in ok if t.get("finish_with_commands"))
        n_unver = sum(1 for t in ok if t.get("unverified_finish"))
        n_extra = sum(
            1 for t in ok for d in (t.get("decisions") or []) if d.get("extra_confidence_turn")
        )
        print(
            f"   decisions: continue {n_cont}, finish {n_fin}; finish turns with commands "
            f"{n_fin_cmd}, of which the answer changed after the last round {n_unver}; "
            f"extra confidence turn needed {n_extra}"
        )
        rec = [t.get("record_ok") for t in ok if t.get("record_ok") is not None]
        if rec:
            print(f"   answer file in the three-section format at the last round: {sum(rec)}/{len(rec)}")
        no_report = sum(1 for t in ok for r in t["rounds"] if not r["has_report"])
        ctx = sum(1 for t in ok for r in t["rounds"] if r.get("context_exceeded"))
        steps = [r["steps_used"] for t in ok for r in t["rounds"] if r.get("steps_used") is not None]
        vtok_p = [t.get("verifier_prompt_tok") or 0 for t in ok]
        vtok_c = [t.get("verifier_compl_tok") or 0 for t in ok]
        vsec = [sum(r.get("elapsed_sec") or 0 for r in t["rounds"]) for t in ok]
        if steps:
            print(
                f"   verifier: rounds without report {no_report}, context exceeded {ctx}, "
                f"mean steps/round {sum(steps) / len(steps):.1f}, mean verifier tokens/trial "
                f"{sum(vtok_p) / len(ok):.0f} prompt + {sum(vtok_c) / len(ok):.0f} completion, "
                f"mean verifier time/trial {sum(vsec) / len(ok):.0f} s"
            )

        # Answer changes within the trial
        with_first = [t for t in ok if t.get("first_reward") is not None and t.get("reward") is not None]
        if with_first:
            changed = sum(1 for t in with_first if (t["first_answer"] or "") != (t.get("got") or ""))
            w2r = sum(1 for t in with_first if t["first_reward"] == 0.0 and t["reward"] == 1.0)
            r2w = sum(1 for t in with_first if t["first_reward"] == 1.0 and t["reward"] == 0.0)
            first_acc = sum(t["first_reward"] for t in with_first) / len(with_first)
            print(
                f"   first-claim answer acc {first_acc:.3f} → final acc "
                f"{sum(t['reward'] for t in with_first) / len(with_first):.3f}; answer changed in "
                f"{changed}; wrong→right {w2r}, right→wrong {r2w}"
            )

        # Rubric statuses on round 1 and on the last round, split by final correctness
        for which in ("first", "last"):
            rows = []
            for t in ok:
                if not t["rounds"]:
                    continue
                r = t["rounds"][0] if which == "first" else t["rounds"][-1]
                rows.append((t.get("reward"), r["statuses"]))
            if not rows:
                continue
            print(f"   rubric statuses on the {which} round (correct / wrong trials):")
            for name in RUBRICS:
                c = Counter(st.get(name, "—") for rw, st in rows if rw == 1.0)
                w = Counter(st.get(name, "—") for rw, st in rows if rw == 0.0)
                fmt = lambda cnt: " ".join(f"{k}:{v}" for k, v in sorted(cnt.items()))  # noqa: E731
                print(f"     {name:26s} correct[{fmt(c)}]  wrong[{fmt(w)}]")

        # Confidence metrics
        recs = [t for t in ok if t.get("final_confidence") is not None and t.get("reward") is not None]
        if recs:
            conf = [float(t["final_confidence"]) for t in recs]
            y = [int(t["reward"]) for t in recs]
            m = conf_metrics(conf, y)
            print(
                f"   stated confidence (n={m['n']}): acc {m['acc']:.3f}, mean conf {m['mean_conf']:.3f}, "
                f"AUROC {_fmt(m['auroc'])}, ECE {_fmt(m['ece'])}, Brier {_fmt(m['brier'])}, Kuiper {_fmt(m['kuiper'])}; "
                f"cov/acc @0.9 {m['cov@0.9']:.2f}/{_fmt(m['acc@0.9'])}"
            )
            distinct = sorted(set(conf))
            print(f"     distinct confidence values: {distinct}")
        recs = [t for t in ok if t["rounds"] and t.get("reward") is not None]
        if recs:
            # rubric score: 1 - (not-YES count on the last round)/5, as a crude confidence
            score = [1 - t["rounds"][-1]["n_not_yes"] / 5 for t in recs]
            y = [int(t["reward"]) for t in recs]
            print(
                f"   rubric score (1 - notYES/5, last round, n={len(recs)}): AUROC {_fmt(auroc(score, y))}, "
                f"mean {sum(score) / len(score):.3f}; distribution of notYES: "
                f"{dict(sorted(Counter(t['rounds'][-1]['n_not_yes'] for t in recs).items()))}"
            )
            score1 = [1 - t["rounds"][0]["n_not_yes"] / 5 for t in recs]
            print(f"   rubric score on round 1: AUROC {_fmt(auroc(score1, y))}")

        if baseline:
            # Timed-out / excepted trials count as wrong, as in the job accuracy above.
            agree = both = gained = lost = lost_timeout = n_cmp = 0
            for t in ts:
                b = baseline.get(t["task"])
                if b is None or b.get("reward") is None:
                    continue
                r = t.get("reward") if t.get("exception") is None else 0.0
                if r is None:
                    continue
                n_cmp += 1
                if b["reward"] == r:
                    agree += 1
                    both += int(r == 1.0)
                elif r == 1.0:
                    gained += 1
                else:
                    lost += 1
                    lost_timeout += int(t.get("exception") is not None)
            bcorrect = sum(
                1 for t in ts if (baseline.get(t["task"]) or {}).get("reward") == 1.0
            )
            print(
                f"   vs baseline (same {n_cmp} tasks, timeouts counted wrong): baseline correct "
                f"{bcorrect}; agree {agree} (both correct {both}); gained {gained}, lost {lost} "
                f"(of which lost by timeout {lost_timeout})"
            )

    if args.trials:
        print("\nPer trial:")
        for t in sorted(trials, key=lambda t: (t["subdomain"], t["name"])):
            st = (
                f"R={t['reward']:.0f}" if t.get("reward") is not None
                else ("EXC:" + t["exception"] if t.get("exception") else "running")
            )
            rounds = ",".join(
                f"{r['round']}:{r['n_not_yes']}notYES" + ("" if r["has_report"] else "(no report)")
                for r in t["rounds"]
            )
            b = baseline.get(t["task"]) if baseline else None
            bs = f" base={b['reward']:.0f}" if b and b.get("reward") is not None else ""
            print(
                f"  {t['task'].split('-')[-1]:>3s} {t['name'][-7:]} {st:5s}{bs} exp={t.get('expected')} first={t.get('first_answer')} "
                f"got={t.get('got')} conf={t.get('final_confidence')} rounds=[{rounds}] "
                f"agent={_fmt(t.get('agent_s'), 0)}s unverified_finish={t.get('unverified_finish')}"
            )

    if args.json:
        json.dump(trials, open(args.json, "w"), indent=1, default=str)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
