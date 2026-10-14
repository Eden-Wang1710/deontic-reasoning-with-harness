"""DeonticBench data statistics: case counts, labels, field lengths and real prompt lengths per mode.

Usage:  python tools/deonticbench_stats.py DeonticBench/
Needs:  tiktoken plus DeonticBench requirements (openai, httpx, pyyaml, tqdm, tenacity).
Output: a text report on stdout and db_stats.json next to this script. Token counts use o200k_base.
"""
import json, os, statistics as st, sys
from collections import Counter
ROOT = sys.argv[1]
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.argv = ["x"]  # keep argparse quiet on import
import tiktoken
import generate_e2e as g
from prompts.dataset_prompts import (compose_prompt_source_legal_ir, compose_prompt_source_uscis,
    compose_prompt_standalone_prolog_legal_ir, compose_prompt_standalone_prolog_uscis,
    compose_prompt_prolog_legal_ir, compose_prompt_prolog_uscis)
from prompts.case_exemplars import EXAMPLE_LEGAL_IR_ONE_SHOT, EXAMPLE_USCIS_ONE_SHOT
enc = tiktoken.get_encoding("o200k_base")
tok = lambda s: len(enc.encode(s, disallowed_special=()))

DOMAINS = {  # dataset name -> (data dir, statutes dir)
    "sara_numeric": ("sara_numeric", "sara"), "sara_binary": ("sara_binary", "sara"),
    "airline": ("airline", "airline"), "housing": ("housing", None), "uscis": ("uscis-aao", None)}
COMPOSERS = {
    "sara_numeric": dict(direct=g.compose_prompt_source, zeroshot=g.compose_prompt_standalone_prolog_sara, fewshot=g.compose_prompt_prolog_sara),
    "sara_binary":  dict(direct=g.compose_prompt_source_binary, zeroshot=g.compose_prompt_standalone_prolog_binary, fewshot=g.compose_prompt_prolog_binary),
    "airline":      dict(direct=g.compose_prompt_source, zeroshot=g.compose_prompt_standalone_prolog_airline, fewshot=g.compose_prompt_prolog_airline),
    "housing":      dict(direct=compose_prompt_source_legal_ir, zeroshot=compose_prompt_standalone_prolog_legal_ir,
                         fewshot=lambda s, c: compose_prompt_prolog_legal_ir(s, c, exemplars=EXAMPLE_LEGAL_IR_ONE_SHOT)),
    "uscis":        dict(direct=compose_prompt_source_uscis, zeroshot=compose_prompt_standalone_prolog_uscis,
                         fewshot=lambda s, c: compose_prompt_prolog_uscis(s, c, exemplars=EXAMPLE_USCIS_ONE_SHOT)),
}
def summ(xs):
    return dict(n=len(xs), mean=round(st.mean(xs)), median=round(st.median(xs)), min=min(xs), max=max(xs))

out = {}
print("## Statute files (shared context)")
for sd in ["sara", "airline"]:
    p = os.path.join(ROOT, "statutes", sd); blob = g.load_statutes(p)
    files = sorted(os.listdir(p))
    print(f"- statutes/{sd}: {len(files)} files, {len(blob)} chars, {tok(blob)} tokens (o200k)")
    out[f"statutes/{sd}"] = dict(files=files, chars=len(blob), tokens=tok(blob))

for ds, (ddir, sdir) in DOMAINS.items():
    for split in ["smoke", "hard", "whole"]:
        path = os.path.join(ROOT, "data", ddir, f"{split}.json")
        if not os.path.exists(path): continue
        raw = json.load(open(path))
        cases = g.load_cases_from_json(path, ds)
        statutes = g.load_statutes(os.path.join(ROOT, "statutes", sdir)) if sdir else ""
        if ds == "airline":
            pool = g.load_airline_exemplar_pool(path)
            for c in cases: c["exemplar"] = g.retrieve_airline_exemplar(pool, c["text"])
        rec = dict(path=os.path.relpath(path, ROOT), n=len(raw), labels=dict(Counter(str(r["label"]) for r in raw).most_common(6)))
        rec["fields_tokens"] = {k: summ([tok(str(r.get(k, ""))) for r in raw]) for k in ["text", "question", "statutes", "reference_prolog"] if k in raw[0]}
        rec["prompt_tokens"] = {m: summ([tok(f(statutes, c)) for c in cases]) for m, f in COMPOSERS[ds].items()}
        rec["ids_unique"] = len({r["id"] for r in raw})
        out[f"{ds}/{split}"] = rec
        print(f"\n## {ds}/{split}: {rec['n']} cases ({rec['ids_unique']} unique ids) at {rec['path']}")
        print("  labels:", rec["labels"])
        for k, v in rec["fields_tokens"].items(): print(f"  {k:17s} tokens: {v}")
        for m, v in rec["prompt_tokens"].items(): print(f"  prompt[{m:8s}] tokens: {v}")
json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "db_stats.json"), "w"), indent=1)
