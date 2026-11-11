#!/usr/bin/env python3
"""Aggregate numbers of one reader-verifier(-compactor) run, optionally against a baseline run.

    tools/rv_run_report.py <job_dir> [<baseline_job_dir>] [--trials-json OUT]

Reads result.json, agent/trajectory.json, agent/rubric_verifier/{summary,round-N}.json and
agent/reader/call-N.json of every finished trial of a SARA numeric job (t2rv or t2rvc), and the
gold values under harbor-deonticbench/datasets/ (analysis only). Prints accuracy with Wilson
intervals, the paired comparison, time per role, time-limit fallbacks, compactions, and what
happened after the first verifier report. Used for claude_docs/09 §9.3 and §10.10.
Run from the repo root.
"""
import argparse
import glob
import json
import os
import random
import re
import statistics as st
from datetime import datetime
from math import comb

DATASET = "harbor-deonticbench/datasets/deonticbench-direct"


def ts(z):
    return datetime.fromisoformat(z.replace("Z", "+00:00"))


def answer_value(rd):
    """Last integer of the answer file's Answer section as the verifier round saw it."""
    a = rd.get("answer") or ""
    if "## Answer" not in a:
        return None
    m = re.findall(r"-?\$?\d[\d,]*(?:\.\d+)?", a.split("## Answer")[-1])
    return m[-1].replace("$", "").replace(",", "").split(".")[0] if m else None


def statuses(rd):
    rep = rd.get("report") or {}
    return [i.get("status") for i in rep.get("rubrics") or []]


def load(job):
    out = {}
    for d in sorted(glob.glob(job.rstrip("/") + "/*/")):
        if not os.path.exists(d + "result.json"):
            continue
        r = json.load(open(d + "result.json"))
        m = re.search(r"tax-case-(\d+)", r.get("task_name") or "")
        if not m:
            continue
        gold_file = glob.glob(f"{DATASET}/*{r['task_name'].split('/')[-1]}/tests/expected_label.txt")
        ae = r.get("agent_execution") or {}
        sp = d + "agent/rubric_verifier/summary.json"
        steps = json.load(open(d + "agent/trajectory.json"))["steps"] if os.path.exists(d + "agent/trajectory.json") else []
        first_report = next(
            ((ts(s["timestamp"]) - ts(steps[0]["timestamp"])).total_seconds() for s in steps
             if s["source"] == "system" and str(s.get("message", "")).startswith("Rubric verifier round 1")), None)
        s = json.load(open(sp)) if os.path.exists(sp) else {}
        out[int(m.group(1))] = dict(
            reward=((r.get("verifier_result") or {}).get("rewards") or {}).get("reward") or 0.0,
            exc=(r.get("exception_info") or {}).get("exception_type"),
            sec=(ts(ae["finished_at"]) - ts(ae["started_at"])).total_seconds() if ae.get("finished_at") else None,
            gold=open(gold_file[0]).read().strip() if gold_file else None,
            s=s, first_report=first_report,
            rounds=[json.load(open(f)) for f in sorted(glob.glob(d + "agent/rubric_verifier/round-?.json"))],
            readers=[json.load(open(f)) for f in sorted(glob.glob(d + "agent/reader/call-*.json"),
                                                        key=lambda f: int(re.search(r"call-(\d+)", f).group(1)))],
            compactions=s.get("compactor_calls") or [], skips=s.get("compactor_skips") or [])
    return out


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return ((c - h) / d, (c + h) / d)


def q(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return "n=0"
    return f"n={len(xs)} min={xs[0]:.0f} med={st.median(xs):.0f} mean={st.mean(xs):.0f} max={xs[-1]:.0f}"


def role_seconds(x):
    v = sum(r.get("elapsed_sec", 0) for r in x["rounds"])
    r = sum(r.get("elapsed_sec", 0) for r in x["readers"])
    c = sum((c.get("elapsed_sec") or 0) for c in x["compactions"])
    return v, r, c, (x["sec"] or 0) - v - r - c


def describe(name, d):
    n = len(d)
    k = sum(x["reward"] == 1.0 for x in d.values())
    kp = sum(x["reward"] == 1.0 and not x["exc"] for x in d.values())
    print(f"== {name}: n={n}")
    print("file-graded correct %d/%d = %.3f, Wilson 95%% [%.3f, %.3f]" % (k, n, k / n, *wilson(k, n)))
    print("protocol score (timeouts count wrong) %d/%d = %.3f, Wilson 95%% [%.3f, %.3f]" % (kp, n, kp / n, *wilson(kp, n)))
    to = sorted(t for t in d if d[t]["exc"])
    print(f"timeouts {len(to)} {to}; correct file among them: {sorted(t for t in to if d[t]['reward'] == 1.0)}")
    print("trial seconds:", q([x["sec"] for x in d.values()]))
    for grp, sel in (("all", lambda x: True), ("timed out", lambda x: x["exc"]), ("finished", lambda x: not x["exc"])):
        ps = [role_seconds(x) for x in d.values() if sel(x)]
        if ps:
            print(f"mean seconds per trial ({grp}, n={len(ps)}): verifier %.0f, reader %.0f, compactor %.0f, solver and commands %.0f"
                  % tuple(st.mean(p[i] for p in ps) for i in range(4)))
    fr = {}
    for x in d.values():
        fr[x["s"].get("finish_reason")] = fr.get(x["s"].get("finish_reason"), 0) + 1
    print("finish reasons:", fr)
    for kind, key in (("verifier rounds", "rounds"), ("reader calls", "readers")):
        rs = [r for x in d.values() for r in x[key]]
        fb = [r for r in rs if r.get("thinking_fallback")]
        print(f"{kind}: seconds {q([r.get('elapsed_sec') for r in rs])}; completion tokens {q([r.get('completion_tokens', 0) for r in rs])}")
        print(f"   fell back to no thinking {len(fb)}, ended at the limit {sum(bool(r.get('timed_out')) for r in rs)}, of {len(rs)}")
        if fb:
            calls = {}
            for r in fb:
                c = r["attempts"][0]["llm_calls"]
                calls[c] = calls.get(c, 0) + 1
            print("   model calls completed while thinking, in the fallback runs:", dict(sorted(calls.items())),
                  "; seconds of the no-thinking phase:", q([r["attempts"][1]["elapsed_sec"] for r in fb]))
            print("   seconds of the runs that finished with thinking:", q([r["elapsed_sec"] for r in rs if not r.get("thinking_fallback")]))
    print("verifier rounds per trial:", sorted(len(x["rounds"]) for x in d.values()))
    cc = [c for x in d.values() for c in x["compactions"]]
    if cc:
        rb = [c for c in cc if c.get("rebuilt")]
        trig = {}
        for c in cc:
            trig[c.get("trigger")] = trig.get(c.get("trigger"), 0) + 1
        comp = {t for t, x in d.items() if any(c.get("rebuilt") for c in x["compactions"])}
        print(f"compactor: calls {len(cc)} {trig}, rebuilds {len(rb)} in {len(comp)} trials, skips {sum(len(x['skips']) for x in d.values())}")
        print("   seconds", q([c.get("elapsed_sec") for c in cc]), "; calls with a timed-out attempt",
              sum(1 for c in cc if any(a["outcome"] in ("timeout", "output_limit") for a in c.get("attempts") or [])))
        print("   solver prompt tokens before", q([c["solver_prompt_tokens_before"] for c in rb]),
              "; after", q([c.get("solver_prompt_tokens_after") for c in rb]))
        print(f"   correct with a rebuild {sum(d[t]['reward'] == 1.0 for t in comp)}/{len(comp)}, "
              f"without {sum(d[t]['reward'] == 1.0 for t in d if t not in comp)}/{n - len(comp)}")
    # after the first verifier report
    has = {t: x for t, x in d.items() if x["rounds"]}
    def right(x, i):
        return answer_value(x["rounds"][i]) is not None and answer_value(x["rounds"][i]) == x["gold"]
    def passed(x, i):
        s = statuses(x["rounds"][i])
        return bool(s) and all(v == "YES" for v in s)
    print(f"trials with a verifier round: {len(has)}; first report at second", q([x["first_report"] for x in has.values()]))
    print("no verifier round:", sorted(t for t in d if t not in has))
    for label, grp in (("first report all YES", {t: x for t, x in has.items() if passed(x, 0)}),
                       ("first report not all YES", {t: x for t, x in has.items() if not passed(x, 0)})):
        print(f"{label}: {len(grp)} trials; first answer correct {sum(right(x, 0) for x in grp.values())}; "
              f"final correct {sum(x['reward'] == 1.0 for x in grp.values())}; timeouts {sum(bool(x['exc']) for x in grp.values())}")
        if label.endswith("not all YES"):
            print("   second round reached", sum(len(x["rounds"]) >= 2 for x in grp.values()),
                  "; answer value differs between rounds", sum(len({answer_value(r) for r in x["rounds"]}) > 1 for x in grp.values()),
                  "; first wrong or missing, final right", sorted(t for t, x in grp.items() if not right(x, 0) and x["reward"] == 1.0),
                  "; first right, final wrong", sorted(t for t, x in grp.items() if right(x, 0) and x["reward"] != 1.0),
                  "; last report all YES", sum(passed(x, -1) for x in grp.values()))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job_dir")
    ap.add_argument("baseline_job_dir", nargs="?")
    ap.add_argument("--trials-json", help="write one row per trial to this file")
    a = ap.parse_args()
    cur = load(a.job_dir)
    describe(os.path.basename(a.job_dir.rstrip("/")), cur)
    if a.baseline_job_dir:
        base = load(a.baseline_job_dir)
        describe(os.path.basename(a.baseline_job_dir.rstrip("/")), base)
        common = sorted(set(cur) & set(base))
        fixed = [t for t in common if cur[t]["reward"] == 1.0 and base[t]["reward"] != 1.0]
        broken = [t for t in common if cur[t]["reward"] != 1.0 and base[t]["reward"] == 1.0]
        both = sum(cur[t]["reward"] == 1.0 and base[t]["reward"] == 1.0 for t in common)
        print(f"== paired on {len(common)} tasks (file-graded): gained {fixed}, lost {broken}, both correct {both}, "
              f"both wrong {len(common) - len(fixed) - len(broken) - both}")
        b, c = len(fixed), len(broken)
        if b + c:
            p = min(1.0, 2 * sum(comb(b + c, i) for i in range(min(b, c) + 1)) / 2 ** (b + c))
            print(f"exact McNemar p = {p:.3f}")
        random.seed(0)
        diffs = sorted(sum(cur[t]["reward"] - base[t]["reward"] for t in smp) / len(common)
                       for smp in ([random.choice(common) for _ in common] for _ in range(10000)))
        print("difference %+.3f, paired bootstrap 95%% [%+.3f, %+.3f]"
              % (sum(cur[t]["reward"] - base[t]["reward"] for t in common) / len(common), diffs[250], diffs[9750]))
    if a.trials_json:
        rows = []
        for t in sorted(cur):
            x = cur[t]
            v, r, c, s = role_seconds(x)
            rows.append(dict(
                case=t, reward=x["reward"], exception=x["exc"], seconds=round(x["sec"] or 0),
                finish_reason=x["s"].get("finish_reason"), gold=x["gold"],
                rounds=[dict(statuses=statuses(rd), answer=answer_value(rd), seconds=round(rd.get("elapsed_sec", 0)),
                             thinking_fallback=bool(rd.get("thinking_fallback")), timed_out=bool(rd.get("timed_out")))
                        for rd in x["rounds"]],
                reader_calls=[dict(seconds=round(rd.get("elapsed_sec", 0)), thinking_fallback=bool(rd.get("thinking_fallback")),
                                   timed_out=bool(rd.get("timed_out"))) for rd in x["readers"]],
                compactions=[dict(trigger=cc.get("trigger"), rebuilt=bool(cc.get("rebuilt")), seconds=round(cc.get("elapsed_sec") or 0),
                                  tokens_before=cc.get("solver_prompt_tokens_before"), tokens_after=cc.get("solver_prompt_tokens_after"))
                             for cc in x["compactions"]],
                seconds_by_role=dict(verifier=round(v), reader=round(r), compactor=round(c), solver_and_commands=round(s))))
        with open(a.trials_json, "w") as f:
            json.dump(rows, f, indent=1)
        print("wrote", a.trials_json)


if __name__ == "__main__":
    main()
