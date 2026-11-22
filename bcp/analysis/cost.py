#!/usr/bin/env python3
"""Tokens and list-price cost per question of BrowseComp-Plus jobs (claude_docs/13 §4, §5).

Same two numbers as tools/inference_cost.py reports for the DeonticBench runs: the cost without
a cache, and the cost with a prompt cache simulated inside each trial. Nothing is shared
between questions. The judge is not priced.

Where the token counts come from:
- solver: agent/usage.json (all prompts and completions of the trial). Cached tokens are the
  prompt tokens minus CLM's cache_aware_prefill_tokens (agent/trajectory.ctx.json), CLM's own
  replay of the prompts through a prefix cache. Unlike tools/inference_cost.py it counts the
  tokens the model generated as cached.
- reader and verifier (v0.1): the "llm" rows of the tools log. The prompts are not stored, so
  the cached part is an ESTIMATE: for every reader call but the first of a trial, the reader
  skill and the question, plus the state table when an earlier call had the same one; for the
  verifier, a prompt identical in size to the previous one (the retry without thinking).

Usage: cost.py JOB [JOB ...] [--json OUT]      (JOB is a job name under <data>/jobs)
"""
import argparse, bisect, collections, glob, json, statistics as st
from pathlib import Path

DATA = Path.home() / "data/browsecomp-plus"
JOBS = DATA / "jobs"
PRICING = Path(__file__).resolve().parents[2] / "tools/pricing"
CARDS = {"qwen3.5-9b_own": "price_card_qwen3.5-9b.json", "thesis_card": "price_card.json"}
ZERO = dict(calls=0, inp=0, cached=0, out=0)


def solver(job):
    out = {}
    for d in glob.glob(str(JOBS / job / "bcp-q*__*")):
        q = d.split("bcp-q")[1].split("__")[0]
        u = json.loads(Path(d, "agent/usage.json").read_text())
        k = json.loads(Path(d, "agent/trajectory.ctx.json").read_text())["final_metrics"]["extra"].get("kv_cache_flops", {})
        uncached = k.get("cache_aware_prefill_tokens") or u["prompt_tokens"]
        out[q] = dict(calls=u["n_lm_calls"], inp=u["prompt_tokens"], cached=u["prompt_tokens"] - uncached,
                      out=u["completion_tokens"])
    return out


def roles(job, question_chars):
    calls, states = collections.defaultdict(list), collections.defaultdict(list)
    for line in open(JOBS / f"{job}.tools.jsonl"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("op") == "llm":
            calls[str(d["qid"])].append(d)
        elif d.get("op") == "state_version":
            states[str(d["qid"])].append((d["t"], d["sha"], d["chars"]))
    out = {}
    for q, cs in calls.items():
        cs.sort(key=lambda d: d["t"])
        sv = sorted(states[q]); ts = [x[0] for x in sv]
        r = dict(reader=dict(ZERO), verifier=dict(ZERO))
        seen, first, prev = set(), True, None
        for d in cs:
            x = r[d["role"]]; p = d["prompt_tokens"] or 0
            x["calls"] += 1; x["inp"] += p; x["out"] += d["completion_tokens"] or 0
            ratio = p / max(1, d["system_chars"] + d["user_chars"])
            if d["role"] == "reader":
                i = bisect.bisect_right(ts, d["t"]) - 1
                sha, chars = (sv[i][1], sv[i][2]) if i >= 0 else ("none", 7)
                prefix = d["system_chars"] + len("# Question\n\n\n") + question_chars.get(q, 0)
                if sha in seen:
                    prefix += len("# State table of the solver\n\n\n") + chars
                if not first:
                    x["cached"] += min(p, int(prefix * ratio))
                first = False; seen.add(sha)
            else:
                if prev == (d["system_chars"], d["user_chars"]):
                    x["cached"] += p
                prev = (d["system_chars"], d["user_chars"])
        out[q] = r
    return out


def cents(parts, card):
    i, o, c = card["input"], card["output"], card.get("cache_read")
    no_cache = sum(p["inp"] * i + p["out"] * o for p in parts) / 1e4
    cache = sum((p["inp"] - p["cached"]) * i + p["cached"] * (i if c is None else c) + p["out"] * o for p in parts) / 1e4
    return no_cache, cache


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jobs", nargs="+"); ap.add_argument("--json")
    a = ap.parse_args()
    qchars = {}
    for line in open(DATA / "prepared/queries.jsonl"):
        x = json.loads(line); qchars[str(x["query_id"])] = len(x["query"])
    cards = {n: json.loads((PRICING / f).read_text())["usd_per_million_tokens"] for n, f in CARDS.items()}
    result = {"cards": cards, "jobs": {}}
    for job in a.jobs:
        s, r = solver(job), roles(job, qchars)
        per = {q: {"solver": s[q], **{k: r.get(q, {}).get(k, dict(ZERO)) for k in ("reader", "verifier")}} for q in s}
        mean = {role: {k: round(st.mean(per[q][role][k] for q in per), 1) for k in ZERO} for role in ("solver", "reader", "verifier")}
        mean["all"] = {k: round(sum(mean[role][k] for role in ("solver", "reader", "verifier")), 1) for k in ZERO}
        job_out = {"n": len(per), "mean_per_question": mean, "cents_per_question": {}}
        print(f"== {job}: {len(per)} questions")
        for role, m in mean.items():
            print(f"  {role:8} calls {m['calls']:7.1f}  input {m['inp']:10.0f}  cached {m['cached']:9.0f}  output {m['out']:8.0f}")
        for name, card in cards.items():
            c = [cents(list(per[q].values()), card) for q in per]
            so = [cents([per[q]["solver"]], card) for q in per]
            nc = sorted(x[0] for x in c)
            job_out["cents_per_question"][name] = dict(
                no_cache=round(st.mean(nc), 2), with_cache=round(st.mean(x[1] for x in c), 2),
                solver_no_cache=round(st.mean(x[0] for x in so), 2), solver_with_cache=round(st.mean(x[1] for x in so), 2),
                median_no_cache=round(st.median(nc), 2), p95_no_cache=round(nc[int(.95 * len(nc)) - 1], 2),
                max_no_cache=round(nc[-1], 2), job_usd_no_cache=round(sum(nc) / 100, 2),
                job_usd_with_cache=round(sum(x[1] for x in c) / 100, 2))
            print(f"  {name}: {job_out['cents_per_question'][name]}")
        result["jobs"][job] = job_out
    if a.json:
        Path(a.json).write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
