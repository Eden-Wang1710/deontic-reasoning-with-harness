"""Score a DeonticBench prompt-based `direct` run under both papers' grading rules.

DeonticBench's own `scripts/bootstrap_outputs.py` reports accuracy with a ±$1 tolerance on
numeric answers and abstentions counted wrong. DAR's `test.sh` grades numeric answers by exact
integer match, and the DAR paper reports SARA-Binary as macro-F1. To compare a prompt-based run
with the harness runs in claude_docs/07 the same generations have to be scored both ways; this
script reuses DeonticBench's answer parser and label loader and adds the DAR rules.

For every domain it reports, over 1,000 bootstrap replicates that draw one generation per case
(DeonticBench's procedure), mean and 95 % CI of:
  exact      accuracy, exact integer match (DAR rule; binary: label match)
  pm1        accuracy with ±1 tolerance (DeonticBench rule; identical to exact on binary)
  abstain    fraction of unparsable / failed generations
  macro_f1   (binary only) with abstentions mapped to the wrong class, as the DeonticBench
             paper does, and macro_f1_strict with abstentions as a separate wrong class, as
             DAR's evaluate_deonticbench.py does
plus the pooled rate over all generations and the per-case pass@n.

Usage (from the repo root):
    python tools/db_direct_score.py --model qwen3.5-35b-a3b [--provider vllm] [--domains sara_numeric sara_binary]
"""

import argparse
import json
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "DeonticBench" / "scripts"))
import bootstrap_outputs as bo  # noqa: E402  (DeonticBench's parser, label loader, rules)

BIN_NAME = {1: "Entailment", 0: "Contradiction"}


def exact(outcome, label, domain):
    if bo._is_abstain(outcome):
        return False
    try:
        return int(round(float(outcome))) == int(float(label))
    except (TypeError, ValueError):
        return False


def macro_f1(pairs):
    """pairs = (expected, got) strings; DAR's definition (unweighted mean of per-class F1)."""
    classes = sorted({e for e, _ in pairs})
    f1s = []
    for c in classes:
        tp = sum(1 for e, g in pairs if e == c and g == c)
        fp = sum(1 for e, g in pairs if e != c and g == c)
        fn = sum(1 for e, g in pairs if e == c and g != c)
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * p * r / (p + r) if p + r else 0.0)
    return sum(f1s) / len(f1s) if f1s else 0.0


def binary_pairs(draw, strict):
    pairs = []
    for label, outcome in draw:
        exp = BIN_NAME[int(label)]
        if bo._is_abstain(outcome):
            got = "__ABSTAIN__" if strict else BIN_NAME[1 - int(label)]
        else:
            got = BIN_NAME[int(round(float(outcome)))]
        pairs.append((exp, got))
    return pairs


def replicate_stats(draw, domain):
    n = len(draw)
    s = {
        "exact": sum(exact(o, l, domain) for l, o in draw) / n,
        "pm1": sum(bo._is_correct(o, l, domain) for l, o in draw) / n,
        "abstain": sum(bo._is_abstain(o) for _, o in draw) / n,
    }
    if domain == "sara_binary":
        s["macro_f1"] = macro_f1(binary_pairs(draw, strict=False))
        s["macro_f1_strict"] = macro_f1(binary_pairs(draw, strict=True))
    return s


def score(path, domain, reps=1000, seed=0):
    cases = bo.parse_source_json(str(path), domain)
    items = [(c["label"], c["outcomes"]) for c in cases.values() if c["outcomes"]]
    rng = random.Random(seed)
    reps_stats = [replicate_stats([(l, rng.choice(o)) for l, o in items], domain) for _ in range(reps)]
    out = {"n_cases": len(items), "generations_per_case": sorted({len(o) for _, o in items})}
    for k in reps_stats[0]:
        vals = sorted(r[k] for r in reps_stats)
        out[k] = {"mean": sum(vals) / len(vals), "ci95": (vals[int(0.025 * reps)], vals[int(0.975 * reps) - 1])}
    pooled = [(l, o) for l, outs in items for o in outs]
    out["pooled"] = replicate_stats(pooled, domain)
    out["pass_at_n_exact"] = sum(any(exact(o, l, domain) for o in outs) for l, outs in items) / len(items)
    out["majority_exact"] = sum(
        exact(max(set(map(str, outs)), key=list(map(str, outs)).count), l, domain) for l, outs in items
    ) / len(items)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--provider", default="vllm")
    ap.add_argument("--domains", nargs="+", default=["sara_numeric", "sara_binary"])
    ap.add_argument("--outputs-dir", default=str(REPO / "DeonticBench" / "outputs"))
    ap.add_argument("--json", help="write the full result here")
    a = ap.parse_args()
    results = {}
    print("| domain | cases × gens | exact (DAR rule) | ±1 (DeonticBench rule) | abstain | macro-F1 (abstain→wrong class) | macro-F1 strict | pooled exact | majority exact | pass@n exact |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for d in a.domains:
        p = Path(a.outputs_dir) / d / "direct" / a.provider / a.model / "source.json"
        if not p.exists():
            print(f"| {d} | missing: {p} |")
            continue
        r = results[d] = score(p, d)
        f = lambda k: f"{r[k]['mean']:.3f} [{r[k]['ci95'][0]:.3f}, {r[k]['ci95'][1]:.3f}]" if k in r else "—"
        print(f"| {d} | {r['n_cases']} × {r['generations_per_case']} | {f('exact')} | {f('pm1')} | {f('abstain')} | "
              f"{f('macro_f1')} | {f('macro_f1_strict')} | {r['pooled']['exact']:.3f} | {r['majority_exact']:.3f} | {r['pass_at_n_exact']:.3f} |")
    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
