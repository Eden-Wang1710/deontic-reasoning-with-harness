#!/usr/bin/env python3
"""Inference cost of Harbor job directories, from token counts and a fixed price card.

Inference cost is the second metric next to accuracy since 2026-11-11 (claude_docs/04 §8,
claude_docs/09 §10.12). It is not the break-even price of Jurayj et al. (claude_docs/04
§3.3): it prices the tokens a harness consumed, nothing else.

Prices come from ``tools/pricing/price_card.json`` (USD per million input, output and
cache-read tokens). Every trial is priced on its own; nothing is shared between tasks.

Two costs are reported per trial:

- **no cache**: input tokens x input price + output tokens x output price. An upper bound.
- **with cache**: a simulated prompt cache inside the trial. The part of a prompt that is a
  prefix of an earlier prompt of the same trial is billed at the cache-read price, the rest
  at the input price. Generated tokens are not counted as cached: with thinking on, the
  reasoning is not sent back, so the next prompt diverges from the server's sequence where
  the previous prompt ended. No minimum prefix length, no expiry, no write fee (the card
  has none). A lower bound for a provider with prefix caching.

Where the token counts come from:

- Trials with ``agent/llm_calls.jsonl`` (written by ``tools/dar_cost_ledger.py``): the
  ledger. It has every call of every role, also replies cut off by an output limit, and the
  shared prefix of each prompt in characters.
- Older trials: the solver's requests in ``agent/episode-N/debug.json`` (exact messages
  and usage) and the sub-agent trajectories ``agent/trajectory.<role>-*.json`` (usage per
  step; a conversation only grows, so each prompt contains the previous one; the first
  prompt is compared with the first prompts of earlier sub-agent runs). Calls that failed
  or were cancelled left no usage in these logs and are missing from the cost.

A shared prefix measured in characters is converted to tokens exactly when it is a whole
earlier prompt (that prompt's token count) and in proportion to the characters otherwise.

Usage:
  inference_cost.py JOB_DIR [JOB_DIR ...] [--match REGEX] [--baseline JOB_DIR]
                    [--per-trial] [--json OUT]
  e.g. --match sara-numeric to compare jobs on the SARA numeric tasks only.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dar_cost_ledger import common_prefix_len, load_price_card, prompt_text  # noqa: E402


def _cached_tokens(
    prompt_tokens: int, prompt_chars: int, lcp_chars: int, earlier
) -> int:
    """Tokens of a prompt that an earlier prompt covers. ``earlier`` is (chars, tokens) of
    the prompt the prefix is shared with, or None."""
    if not prompt_tokens or not prompt_chars or lcp_chars <= 0:
        return 0
    if earlier and earlier[1] and lcp_chars >= earlier[0]:
        return min(prompt_tokens, earlier[1])
    return min(prompt_tokens, int(prompt_tokens * lcp_chars / prompt_chars))


def calls_from_ledger(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    by_n = {r["n"]: r for r in rows}
    calls = []
    for r in rows:
        e = by_n.get(r.get("lcp_call"))
        # A retried request (n_replies > 1) sent the same prompt more than once; its token
        # sums are compared with one prompt, so only one copy can be new.
        replies = max(1, r.get("n_replies") or 1)
        one_prompt = r["prompt_tokens"] // replies
        cached = _cached_tokens(
            one_prompt,
            r["prompt_chars"],
            r["lcp_chars"],
            (e["prompt_chars"], e["prompt_tokens"] // max(1, e.get("n_replies") or 1))
            if e
            else None,
        )
        cached += one_prompt * (replies - 1) if r["prompt_tokens"] else 0
        calls.append(
            {
                "role": r["role"],
                "input": r["prompt_tokens"],
                "cached": cached,
                "output": r["completion_tokens"],
                "finish": r["finish"],
                "seconds": r.get("seconds"),
            }
        )
    return calls


def _usage(debug: dict) -> dict | None:
    raw = debug.get("original_response")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            try:
                raw = ast.literal_eval(raw)
            except Exception:
                return None
    return (raw or {}).get("usage") if isinstance(raw, dict) else None


def calls_from_logs(agent_dir: Path) -> list[dict]:
    """Calls of a trial without a ledger; see the module docstring."""
    calls = []
    # Solver: exact prompts.
    prompts: list[tuple[str, int]] = []
    episodes = sorted(
        agent_dir.glob("episode-*/debug.json"),
        key=lambda p: int(p.parent.name.split("-")[1]),
    )
    for path in episodes:
        try:
            debug = json.loads(path.read_text())
        except Exception:
            continue
        usage = _usage(debug)
        if not usage:  # failed or cancelled request: no usage on record
            calls.append(
                {
                    "role": "solver",
                    "input": 0,
                    "cached": 0,
                    "output": 0,
                    "finish": "unknown",
                }
            )
            continue
        text = prompt_text(debug.get("messages") or [])
        best, earlier = 0, None
        for other, tokens in prompts:
            shared = common_prefix_len(text, other)
            if shared >= best and shared > 0:
                best, earlier = shared, (len(other), tokens)
        p_tok = usage.get("prompt_tokens") or 0
        prompts.append((text, p_tok))
        calls.append(
            {
                "role": "solver",
                "input": p_tok,
                "cached": _cached_tokens(p_tok, len(text), best, earlier),
                "output": usage.get("completion_tokens") or 0,
                "finish": "stop",
            }
        )
    # Sub-agents: usage per step, first prompt as text.
    runs = []
    for path in agent_dir.glob("trajectory.*.json"):
        try:
            traj = json.loads(path.read_text())
        except Exception:
            continue
        steps = traj.get("steps") or []
        if steps:
            runs.append((steps[0].get("timestamp") or "", path.name, steps))
    firsts: list[tuple[str, int]] = []
    for _, name, steps in sorted(runs):
        role = re.split(r"[-.]", name.split(".", 1)[1])[0]
        first = next((s for s in steps if s.get("source") == "user"), None)
        text = prompt_text(
            [{"role": "user", "content": (first or {}).get("message") or ""}]
        )
        previous = None
        for step in steps:
            m = step.get("metrics")
            if step.get("source") != "agent" or not m:
                continue
            p_tok = m.get("prompt_tokens") or 0
            if previous is None:
                best, earlier = 0, None
                for other, tokens in firsts:
                    shared = common_prefix_len(text, other)
                    if shared >= best and shared > 0:
                        best, earlier = shared, (len(other), tokens)
                cached = _cached_tokens(p_tok, len(text), best, earlier)
                firsts.append((text, p_tok))
            else:
                cached = min(p_tok, previous)
            previous = p_tok
            calls.append(
                {
                    "role": role,
                    "input": p_tok,
                    "cached": cached,
                    "output": m.get("completion_tokens") or 0,
                    "finish": "stop",
                }
            )
    return calls


def price_trial(trial_dir: Path, price: dict[str, float]) -> dict | None:
    result_path = trial_dir / "result.json"
    if not result_path.exists():
        return None
    result = json.loads(result_path.read_text())
    agent_dir = trial_dir / "agent"
    ledger = agent_dir / "llm_calls.jsonl"
    calls = calls_from_ledger(ledger) if ledger.exists() else calls_from_logs(agent_dir)
    roles: dict[str, dict] = {}
    for c in calls:
        r = roles.setdefault(
            c["role"], {"calls": 0, "input": 0, "cached": 0, "output": 0}
        )
        r["calls"] += 1
        for k in ("input", "cached", "output"):
            r[k] += c[k]
    for r in roles.values():
        r["usd_no_cache"] = r["input"] * price["input"] + r["output"] * price["output"]
        r["usd_cache"] = (
            (r["input"] - r["cached"]) * price["input"]
            + r["cached"] * price["cache_read"]
            + r["output"] * price["output"]
        )
    total = {
        k: sum(r[k] for r in roles.values())
        for k in ("calls", "input", "cached", "output", "usd_no_cache", "usd_cache")
    }
    agent_result = result.get("agent_result") or {}
    rewards = (result.get("verifier_result") or {}).get("rewards") or {}
    budget_path = agent_dir / "cost_budget.json"
    budget = json.loads(budget_path.read_text()) if budget_path.exists() else {}
    return {
        "trial": trial_dir.name,
        "task": result.get("task_name"),
        "reward": float(rewards.get("reward") or 0.0),
        "exception": (result.get("exception_info") or {}).get("exception_type"),
        "source": "ledger" if ledger.exists() else "logs",
        "budget_exhausted": bool(budget.get("budget_exhausted")),
        "cut_off_calls": sum(1 for c in calls if c["finish"] == "length"),
        "unknown_calls": sum(1 for c in calls if c["finish"] not in ("stop", "length")),
        "harbor_input": agent_result.get("n_input_tokens"),
        "harbor_output": agent_result.get("n_output_tokens"),
        "roles": roles,
        **total,
    }


def _q(values: list[float], q: float) -> float:
    values = sorted(values)
    return (
        values[min(len(values) - 1, int(q * len(values)))] if values else float("nan")
    )


def summarize(job_dir: Path, price: dict[str, float], match: str | None = None) -> dict:
    trials = [
        t
        for d in sorted(job_dir.iterdir())
        if d.is_dir()
        and (not match or re.search(match, d.name))
        and (t := price_trial(d, price))
    ]
    n = len(trials)
    roles: dict[str, dict] = {}
    for t in trials:
        for name, r in t["roles"].items():
            agg = roles.setdefault(name, {k: 0 for k in r})
            for k, v in r.items():
                agg[k] += v
    no_cache = [t["usd_no_cache"] for t in trials]
    cache = [t["usd_cache"] for t in trials]
    return {
        "job": str(job_dir),
        "n_trials": n,
        "accuracy": sum(t["reward"] for t in trials) / n if n else float("nan"),
        "n_correct": sum(t["reward"] for t in trials),
        "exceptions": sum(1 for t in trials if t["exception"]),
        "budget_exhausted": sum(1 for t in trials if t["budget_exhausted"]),
        "cut_off_calls": sum(t["cut_off_calls"] for t in trials),
        "unknown_calls": sum(t["unknown_calls"] for t in trials),
        "source": sorted({t["source"] for t in trials}),
        "totals_differ_from_harbor": sum(
            1
            for t in trials
            if t["harbor_input"] is not None
            and (t["harbor_input"], t["harbor_output"]) != (t["input"], t["output"])
        ),
        "mean": {
            k: sum(t[k] for t in trials) / n if n else float("nan")
            for k in ("calls", "input", "cached", "output", "usd_no_cache", "usd_cache")
        },
        "usd_no_cache": {
            "median": statistics.median(no_cache) if n else float("nan"),
            "p95": _q(no_cache, 0.95),
            "max": max(no_cache, default=float("nan")),
        },
        "usd_cache": {
            "median": statistics.median(cache) if n else float("nan"),
            "p95": _q(cache, 0.95),
            "max": max(cache, default=float("nan")),
        },
        "roles": roles,
        "trials": trials,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("jobs", nargs="+", type=Path, help="Harbor job directories")
    ap.add_argument(
        "--baseline",
        type=Path,
        help="job whose mean cost is the unit of the 'x base' columns (default: the first job)",
    )
    ap.add_argument(
        "--match",
        help="only trials whose directory name matches this regular expression",
    )
    ap.add_argument(
        "--price-card", type=Path, help="default: tools/pricing/price_card.json"
    )
    ap.add_argument(
        "--per-trial", action="store_true", help="also print one line per trial"
    )
    ap.add_argument("--json", type=Path, help="write the full result as JSON")
    args = ap.parse_args()

    price = load_price_card(args.price_card)
    print(
        "prices, USD per 1M tokens: input %.3g, output %.3g, cache read %.3g"
        % tuple(price[k] * 1e6 for k in ("input", "output", "cache_read"))
    )
    jobs = [summarize(j, price, args.match) for j in args.jobs]
    base = summarize(args.baseline, price, args.match) if args.baseline else jobs[0]
    b_nc, b_c = base["mean"]["usd_no_cache"], base["mean"]["usd_cache"]

    print("\nper task (means; cost in US cents)")
    head = f"{'job':34} {'n':>3} {'acc':>9} {'calls':>6} {'input':>8} {'cached':>8} {'output':>7} {'no cache':>9} {'x base':>6} {'cache':>7} {'x base':>6} {'p95':>6} {'max':>6}"
    print(head)
    for j in jobs:
        m = j["mean"]
        print(
            f"{Path(j['job']).name[:34]:34} {j['n_trials']:>3} "
            f"{j['n_correct']:>3.0f}/{j['n_trials']:<3} {m['calls']:>6.1f} {m['input']:>8.0f} {m['cached']:>8.0f} "
            f"{m['output']:>7.0f} {100 * m['usd_no_cache']:>9.2f} {m['usd_no_cache'] / b_nc:>6.2f} "
            f"{100 * m['usd_cache']:>7.2f} {m['usd_cache'] / b_c:>6.2f} "
            f"{100 * j['usd_no_cache']['p95']:>6.2f} {100 * j['usd_no_cache']['max']:>6.2f}"
        )
    print("p95 and max are of the no-cache cost per trial.")

    print("\nper role (means per task; share of the no-cache cost)")
    for j in jobs:
        print(Path(j["job"]).name)
        for name, r in sorted(
            j["roles"].items(), key=lambda kv: -kv[1]["usd_no_cache"]
        ):
            n = j["n_trials"]
            print(
                f"   {name:11} calls {r['calls'] / n:>5.1f}  input {r['input'] / n:>8.0f}  cached {r['cached'] / n:>8.0f}"
                f"  output {r['output'] / n:>7.0f}  no cache {100 * r['usd_no_cache'] / n:>5.2f}c"
                f" ({r['usd_no_cache'] / (j['mean']['usd_no_cache'] * n):>4.0%})  cache {100 * r['usd_cache'] / n:>5.2f}c"
            )
    print("\nnotes")
    for j in jobs:
        print(
            f"   {Path(j['job']).name}: source {'+'.join(j['source'])}; trials with an exception {j['exceptions']}; "
            f"stopped by the cost budget {j['budget_exhausted']}; cut-off replies {j['cut_off_calls']}; "
            f"calls without usage {j['unknown_calls']}; trials whose totals differ from result.json {j['totals_differ_from_harbor']}"
        )
    if args.per_trial:
        for j in jobs:
            print("\n" + Path(j["job"]).name)
            for t in sorted(j["trials"], key=lambda t: -t["usd_no_cache"]):
                print(
                    f"   {t['trial'][-9:]} reward {t['reward']:.0f} calls {t['calls']:>3} input {t['input']:>7} cached {t['cached']:>7} "
                    f"output {t['output']:>6} no cache {100 * t['usd_no_cache']:>5.2f}c cache {100 * t['usd_cache']:>5.2f}c"
                    f"{' budget' if t['budget_exhausted'] else ''}{' ' + t['exception'] if t['exception'] else ''}"
                )
    if args.json:
        args.json.write_text(
            json.dumps({"price_usd_per_token": price, "jobs": jobs}, indent=2)
        )
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
