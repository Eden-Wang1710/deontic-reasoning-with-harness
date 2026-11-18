#!/usr/bin/env python3
"""Inference cost of a DeonticBench prompt-based `direct` run, from its stored token counts.

An estimate under the cost protocol of claude_docs/09 §10.12 (price card, every task priced
on its own), for comparison with the harness runs. Nothing is generated again.

`generate_e2e.py` sends one request per case with `n` generations and stores two totals per
case in `source.json`: `prompt_tokens` (the prompt, counted once by the server) and
`generated_tokens` (all `n` completions together, thinking included). Output tokens per
generation are therefore not known; this script prices **one generation per task** with the
case's mean output:

  no cache    prompt x input price + (generated / n) x output price
  with cache  the same: a task has one call, so no prompt is a prefix of an earlier one

and, as a second figure, all `n` generations of a task (e.g. for a majority vote): `n` calls
with the same prompt, the first at the input price and the others at the cache-read price in
the with-cache figure.

Usage (from the repo root):
    python tools/db_direct_cost.py --model qwen3.5-35b-a3b [--provider vllm]
        [--domains sara_numeric sara_binary] [--price-card CARD] [--json OUT]
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dar_cost_ledger import load_price_card  # noqa: E402


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolation percentile, as numpy's default."""
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def summary(values: list[float]) -> dict[str, float]:
    return {
        "mean": statistics.mean(values),
        "median": statistics.median(values),
        "p95": percentile(values, 0.95),
        "max": max(values),
    }


def domain_cost(path: Path, price: dict[str, float]) -> dict:
    cases = json.loads(path.read_text())
    rows = []
    for case in cases:
        n = case["generation_attempted"]
        prompt = case["prompt_tokens"]
        out_all = case["generated_tokens"]
        out_one = out_all / n
        rows.append(
            {
                "id": case["id"],
                "generations": n,
                "prompt_tokens": prompt,
                "output_tokens_per_generation": out_one,
                "usd_one": prompt * price["input"] + out_one * price["output"],
                "usd_all_no_cache": n * prompt * price["input"] + out_all * price["output"],
                "usd_all_cache": prompt * price["input"]
                + (n - 1) * prompt * price["cache_read"]
                + out_all * price["output"],
            }
        )
    return {
        "source": str(path.relative_to(REPO)),
        "n_cases": len(rows),
        "generations_per_case": sorted({r["generations"] for r in rows}),
        "mean_prompt_tokens": statistics.mean(r["prompt_tokens"] for r in rows),
        "mean_output_tokens_per_generation": statistics.mean(
            r["output_tokens_per_generation"] for r in rows
        ),
        "usd_one_generation": summary([r["usd_one"] for r in rows]),
        "usd_all_generations_no_cache": summary([r["usd_all_no_cache"] for r in rows]),
        "usd_all_generations_cache": summary([r["usd_all_cache"] for r in rows]),
        "cases": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model", required=True)
    ap.add_argument("--provider", default="vllm")
    ap.add_argument("--domains", nargs="+", default=["sara_numeric", "sara_binary"])
    ap.add_argument("--price-card", default=None)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    price = load_price_card(args.price_card)
    out = {"price_usd_per_token": price, "domains": {}}
    print("cents per task; one generation = prompt + mean output of the case's generations")
    for domain in args.domains:
        path = REPO / "DeonticBench" / "outputs" / domain / "direct" / args.provider / args.model / "source.json"
        d = domain_cost(path, price)
        out["domains"][domain] = d
        one, alln, allc = (
            d["usd_one_generation"],
            d["usd_all_generations_no_cache"],
            d["usd_all_generations_cache"],
        )
        print(
            f"{domain}: {d['n_cases']} cases, {d['generations_per_case']} generations, "
            f"prompt {d['mean_prompt_tokens']:.0f} tok, output {d['mean_output_tokens_per_generation']:.0f} tok/generation"
        )
        print(
            f"  one generation   mean {one['mean'] * 100:.2f}  median {one['median'] * 100:.2f}  "
            f"p95 {one['p95'] * 100:.2f}  max {one['max'] * 100:.2f}"
        )
        print(
            f"  all generations  mean {alln['mean'] * 100:.2f} no cache, {allc['mean'] * 100:.2f} with cache"
        )
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
