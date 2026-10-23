#!/usr/bin/env python3
"""Verbalised confidence for the prompt-based DeonticBench *direct* setting.

Companion of tools/verbalized_confidence.py (harness trajectories). Works on the stored
``source.json`` of a finished direct run (DeonticBench/outputs/<domain>/direct/<provider>/<model>/),
reusing its exact prompts. Two conditions:

  joint    the original direct prompt plus a request to end with ``Confidence: NN%``; the model
           solves the case and states its confidence in the same generation (new sample,
           re-graded). The direct analogue of the harness ``lastturn`` condition.
  posthoc  one of the stored generations is replayed as the assistant turn and a confidence
           question is appended, as in the harness ``incontext`` condition.

Grading: ``reward`` is DAR's exact rule (integer match after dropping decimals; label match for
binary), ``reward_pm1`` DeonticBench's +/-1 rule. Records are written per (case, generation) to
--out and are summarised with verbalized_confidence.summarise (same metrics).

Usage (repo root, vLLM server up):
  python tools/db_direct_confidence.py --condition joint --out outputs/verbalized_confidence/direct_joint
  python tools/db_direct_confidence.py --condition posthoc --out outputs/verbalized_confidence/direct_posthoc
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "DeonticBench" / "scripts"))
import bootstrap_outputs as bo  # noqa: E402
import verbalized_confidence as vc  # noqa: E402

SYSTEM = "You are a helpful assistant trained to conduct deontic reasoning"  # generate_e2e.py

JOINT_SUFFIX = """

After your final \\boxed{} answer, add one last line of the form
Confidence: <integer from 0 to 100>%
giving your estimate of how likely it is that your answer is exactly correct under the statutes and the case (the grader uses exact match)."""

ELICIT_POSTHOC = """Before this answer is graded, estimate how likely it is that it is exactly correct under the statutes and the case facts. The grader uses exact match: a dollar amount must match the gold integer exactly, and Entailment/Contradiction must match the gold label. Consider whether you may have misread or misapplied a provision, missed a cross-reference or an exception, misjudged filing status or a dependency, or made an arithmetic slip.

Reply in plain text. The first line must be exactly:
Confidence: <integer from 0 to 100>%
followed by at most three sentences naming the main risk to the answer."""

DOMAINS = {"sara_numeric": "sara-numeric", "sara_binary": "sara-binary"}


def grade(outcome, label, domain: str) -> tuple[float, float]:
    """(DAR exact rule, DeonticBench +/-1 rule) for a parsed outcome."""
    if bo._is_abstain(outcome):
        return 0.0, 0.0
    pm1 = 1.0 if bo._is_correct(outcome, label, domain) else 0.0
    if domain in bo.NUMERIC_DOMAINS:
        exact = 1.0 if int(float(str(outcome).split(".")[0])) == int(float(label)) else 0.0  # test.sh drops decimals
    else:
        exact = pm1
    return exact, pm1


def load_cases(outputs_dir: Path, provider: str, model: str) -> list[dict]:
    cases = []
    for domain, sub in DOMAINS.items():
        p = outputs_dir / domain / "direct" / provider / model / "source.json"
        for c in json.load(open(p)):
            cases.append({"domain": domain, "subdomain": sub, "id": c["id"], "label": c["label"],
                          "prompt": c["prompt"], "answers": c["answers"], "statuses": c["generation_statuses"]})
    return cases


def run_one(client, model, case, condition, gen_idx, out_dir, max_tokens, temperature):
    name = f"{case['domain']}__{case['id']}__{condition}{gen_idx}"
    out_path = os.path.join(out_dir, name + ".json")
    if os.path.exists(out_path):
        prev = json.load(open(out_path))
        prev["confidence"], prev["parse"] = vc.parse_confidence(prev.get("content", ""))
        if prev["confidence"] is not None or prev.get("attempts", 1) >= 3:
            return prev, True
        attempts = prev.get("attempts", 1) + 1
    else:
        prev, attempts = None, 1
    if condition == "joint":
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": case["prompt"] + JOINT_SUFFIX}]
    else:
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": case["prompt"]},
                    {"role": "assistant", "content": case["answers"][gen_idx]}, {"role": "user", "content": ELICIT_POSTHOC}]
    rec = {"trial": name, "task": case["id"], "subdomain": case["subdomain"], "domain": case["domain"],
           "expected": str(case["label"]), "harness": "direct", "condition": condition, "gen_idx": gen_idx,
           "think": True, "max_tokens": max_tokens, "temperature": temperature, "n_messages": len(messages), "attempts": attempts}
    if prev:
        rec["previous_unparsed_content"] = prev.get("content", "")
    try:
        t0 = time.time()
        r = client.chat.completions.create(model=model, messages=messages, max_tokens=max_tokens, temperature=temperature)
        m = r.choices[0].message
        rec.update(content=m.content or "", reasoning=getattr(m, "reasoning", None) or getattr(m, "reasoning_content", None) or "",
                   finish_reason=r.choices[0].finish_reason,
                   usage={"prompt_tokens": r.usage.prompt_tokens, "completion_tokens": r.usage.completion_tokens},
                   elapsed_s=round(time.time() - t0, 1))
        rec["confidence"], rec["parse"] = vc.parse_confidence(rec["content"])
        text = rec["content"] if condition == "joint" else case["answers"][gen_idx]
        outcome = bo._parse_direct_answer(text, case["domain"])
        rec["got"] = None if bo._is_abstain(outcome) else str(outcome)
        rec["reward"], rec["reward_pm1"] = grade(outcome, case["label"], case["domain"])
    except Exception as e:
        rec.update(error=f"{type(e).__name__}: {e}", confidence=None, parse="error", reward=0.0)
    with open(out_path, "w") as f:
        json.dump(rec, f, indent=1, ensure_ascii=False)
    return rec, False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--condition", choices=["joint", "posthoc"], required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--outputs-dir", default="DeonticBench/outputs")
    ap.add_argument("--provider", default="vllm")
    ap.add_argument("--model", default="qwen3.5-35b-a3b")
    ap.add_argument("--n", type=int, default=1, help="joint: generations per case")
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--max-tokens", type=int, default=16384)
    ap.add_argument("--base-url", default="http://127.0.0.1:9009/v1")
    ap.add_argument("--api-key", default=os.environ.get("VLLM_API_KEY", "token-abc123"))
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--limit", type=int, help="cases per domain")
    a = ap.parse_args()
    from openai import OpenAI

    client = OpenAI(base_url=a.base_url, api_key=a.api_key, timeout=3600)
    os.makedirs(a.out, exist_ok=True)
    cases = load_cases(Path(a.outputs_dir), a.provider, a.model)
    if a.limit:
        cases = [c for d in DOMAINS for c in [x for x in cases if x["domain"] == d][: a.limit]]
    jobs = []
    for c in cases:
        if a.condition == "joint":
            jobs += [(c, i) for i in range(a.n)]
        else:
            jobs += [(c, i) for i, s in enumerate(c["statuses"]) if s == "ok"]
    max_tokens = a.max_tokens if a.condition == "joint" else min(a.max_tokens, 8192)
    print(f"{len(jobs)} generations over {len(cases)} cases; condition={a.condition}", flush=True)
    done = 0
    with ThreadPoolExecutor(max_workers=a.concurrency) as ex:
        futs = [ex.submit(run_one, client, a.model, c, a.condition, i, a.out, max_tokens, a.temperature) for c, i in jobs]
        for fut in as_completed(futs):
            rec, cached = fut.result()
            done += 1
            print(f"[{done}/{len(jobs)}] {rec['trial'][-36:]:36s} R={rec.get('reward', 0):.0f} got={rec.get('got')} exp={rec['expected']} "
                  f"conf={rec.get('confidence')} ({rec.get('parse')}) tok={(rec.get('usage') or {}).get('completion_tokens')} "
                  f"{'cached' if cached else str(rec.get('elapsed_s')) + 's'} {rec.get('error', '')}", flush=True)
    print()
    print(vc.summarise(a.out))


if __name__ == "__main__":
    main()
