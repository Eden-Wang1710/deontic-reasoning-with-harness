#!/usr/bin/env python3
"""Offline check of a reader: the v0.1 reader (whole document) against the v0.2 reader (first
512 tokens, then OPEN/FIND) on the same (question, query, document) triples, without a solver
(claude_docs/12 §13.2).

The triples come from the searches the baseline made on questions outside dev150 (positions
150 and later of bcp/query_order.json), so the development questions are not touched. For
every sampled question: the first search that returned a gold document gives one gold
document and two documents of the same result that are neither gold nor evidence. The state
table is the empty template, as at the first search of a trial.

Reported per reader, for gold and for other documents: the grades, the tokens (every prompt
token counted; and with the prefix of a conversation counted once), the actions, and for
gold documents whether the reference answer string is in the lines passed on. Counts only:
no question or answer text is written.

Needs the 9B model server (bcp/servers.sh start <tag>). Run with the vLLM environment's python:
  python -I bcp/analysis/reader_check.py --questions 120 --json outputs/bcp/reader_check_v02.json
"""
import argparse, collections, json, random, re, sys, threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE.parent / "skills/bcp_solver_v1"))
DATA = Path.home() / "data/browsecomp-plus"
norm = lambda s: re.sub(r"\s+", " ", s).casefold().strip()


def qrels(path):
    rel = collections.defaultdict(set)
    for line in open(path):
        p = line.split()
        if int(p[3]) > 0:
            rel[p[0]].add(p[2])
    return rel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", type=int, default=120)
    ap.add_argument("--jobs", nargs="+", default=["clm-base-rest780", "clm-base-rest780b"])
    ap.add_argument("--from-position", type=int, default=150)
    ap.add_argument("--seed", type=int, default=20261123)
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--readers", nargs="+", default=["v01", "v02"])
    ap.add_argument("--json")
    a = ap.parse_args()

    from transformers import AutoTokenizer
    from tools_server import Corpus, EMBED
    from roles_v01 import Roles
    from roles_v02 import Roles2
    from _bcp import STATE_TEMPLATE

    order = json.loads((HERE.parent / "query_order.json").read_text())["order"]
    pool_q = set(order[a.from_position:])
    answer = {}
    for line in open(DATA / "prepared/queries.jsonl", encoding="utf-8"):
        x = json.loads(line); answer[str(x["query_id"])] = norm(str(x["answer"]))
    gold = qrels(DATA / "qrels/qrel_golds.txt"); evid = qrels(DATA / "qrels/qrel_evidence.txt")

    first = {}                                   # qid -> (query, docids) of the first search with a gold document
    for job in a.jobs:
        for line in open(DATA / "jobs" / f"{job}.tools.jsonl", encoding="utf-8"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            q = str(d.get("qid"))
            if d.get("op") == "search" and q in pool_q and q not in first and gold[q] & set(map(str, d["docids"])):
                first[q] = (d["query"], [str(x) for x in d["docids"]])
    rng = random.Random(a.seed)
    qids = sorted(first, key=int); rng.shuffle(qids); qids = qids[:a.questions]
    triples = []
    for q in qids:
        query, docs = first[q]
        g = [d for d in docs if d in gold[q]]
        other = [d for d in docs if d not in gold[q] and d not in evid[q]]
        rng.shuffle(other)
        triples.append((q, query, g[0], "gold"))
        triples += [(q, query, d, "other") for d in other[:2]]

    corpus = Corpus(DATA / "prepared")
    retriever = SimpleNamespace(text_tok=AutoTokenizer.from_pretrained(EMBED), text_lock=threading.Lock())
    base = dict(queries=str(DATA / "prepared/queries.jsonl"), verifier_skill="verifier_bcp_v1", reader_workers=1,
                llm_base="http://127.0.0.1:9009/v1", llm_model="qwen3.5-9b", llm_key="token-abc123", llm_timeout=1500,
                role_temperature=0.7, doc_tokens=8192, reader_max_tokens=600, reader_thinking=False,
                excerpt_rows=4, excerpt_lines=8, first_tokens=512, open_tokens=2000, find_tokens=800, reader_actions=3)
    logs = {r: [] for r in a.readers}
    lock = threading.Lock()

    def make(reader):
        def log(rec):
            with lock:
                logs[reader].append(rec)
        if reader == "v01":
            return Roles(corpus, retriever, SimpleNamespace(**base, reader_skill="reader_excerpt_bcp_v1"), log)
        return Roles2(corpus, retriever, SimpleNamespace(**base, reader_skill="reader_open_bcp_v2"), log)
    roles = {r: make(r) for r in a.readers}

    def one(job):
        reader, (q, query, docid, kind) = job
        card = roles[reader].read_one(q, docid, STATE_TEMPLATE, f"The solver searched for: {query}")
        lines = roles[reader].lines(docid) or []
        text = lambda rs: norm(" ".join(" ".join(lines[x - 1:y]) for x, y in rs))  # noqa: E731
        seen = getattr(card, "seen", None) or [(card.read_from, card.read_to)]
        return dict(reader=reader, qid=q, docid=docid, kind=kind, n_lines=len(lines),
                    match="failed" if card.failed else card.match, actions=list(getattr(card, "actions", [])),
                    lines_seen=sum(y - x + 1 for x, y in seen),
                    answer_in_doc=answer[q] in norm(" ".join(lines)), answer_seen=answer[q] in text(seen),
                    answer_passed=answer[q] in text([(x, y) for x, y, _ in card.excerpts]),
                    first_excerpt=min([x for x, _, _ in card.excerpts], default=None))

    with ThreadPoolExecutor(a.workers) as ex:
        rows = list(ex.map(one, [(r, t) for t in triples for r in a.readers]))

    out = {"questions": len(qids), "triples": len(triples), "readers": {}}
    for r in a.readers:
        calls = [x for x in logs[r] if x["op"] == "llm"]
        tok = collections.defaultdict(lambda: [0, 0, 0])       # docid-key -> prompt, cache-aware prompt, completion
        for x in calls:
            k = (x["qid"], x["docid"]); p = x.get("prompt_tokens") or 0
            tok[k][0] += p; tok[k][1] += p - (x.get("cached_prompt_tokens") or 0); tok[k][2] += x.get("completion_tokens") or 0
        res = {}
        for kind in ("gold", "other"):
            rs = [x for x in rows if x["reader"] == r and x["kind"] == kind]
            n = len(rs); t = [tok[(x["qid"], x["docid"])] for x in rs]
            with_ans = [x for x in rs if x["answer_in_doc"]]
            res[kind] = {
                "documents": n,
                "match": dict(collections.Counter(x["match"] for x in rs)),
                "passed_on_pct": round(100 * sum(x["match"] in ("strong", "weak") for x in rs) / n, 1),
                "prompt_tokens_per_doc": round(sum(x[0] for x in t) / n),
                "prompt_tokens_cache_aware_per_doc": round(sum(x[1] for x in t) / n),
                "completion_tokens_per_doc": round(sum(x[2] for x in t) / n),
                "calls_per_doc": round(sum(1 for x in calls if (x["qid"], x["docid"]) in {(y["qid"], y["docid"]) for y in rs}) / n, 2),
                "actions": dict(collections.Counter(" ".join(x["actions"]) or "(none)" for x in rs)),
                "share_of_lines_seen_pct": round(100 * sum(x["lines_seen"] for x in rs) / max(1, sum(x["n_lines"] for x in rs)), 1),
                "answer_string_in_document": len(with_ans),
                "answer_string_seen": sum(x["answer_seen"] for x in with_ans),
                "answer_string_passed_on": sum(x["answer_passed"] for x in with_ans),
            }
        res["failed_calls"] = sum(1 for x in calls if x.get("error"))
        out["readers"][r] = res
    if len(a.readers) == 2:
        by = {(x["reader"], x["qid"], x["docid"]): x for x in rows}
        r1, r2 = a.readers
        pairs = [(by[(r1, q, d)], by[(r2, q, d)]) for q, _, d, k in triples if k == "gold"]
        out["gold_agreement"] = dict(collections.Counter(f"{x['match']}->{y['match']}" for x, y in pairs))
    print(json.dumps(out, indent=1))
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
