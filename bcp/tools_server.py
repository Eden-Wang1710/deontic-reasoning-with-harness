#!/usr/bin/env python3
"""Tools server for BrowseComp-Plus (claude_docs/12 §3.2): one host process that holds the
pre-built Qwen3-Embedding-8B index, the embedding model and the corpus.

  serve   HTTP server for the sandbox commands `search` and `get_document`
  eval    acceptance test: retrieve with the full question as the query and print
          Recall@5/100/1000 and nDCG@10 against a qrels file (BCP paper, Table 2)

Retrieval is exact inner-product search over the 100,195 normalised document vectors
(the same result as BCP's FaissFlatSearcher), done with torch on the GPU. Queries are
encoded as BCP's searcher does: task prefix + query, last-token pooling, normalised.

Runs in the vLLM environment (torch, transformers, fastapi). Run with `python -I`.
Every served call is appended to a JSONL log that the agent cannot edit.
"""
import argparse, json, mmap, os, threading, time
from pathlib import Path

import numpy as np
import torch

TASK_PREFIX = "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:"
DATA = Path(os.environ.get("BCP_DATA", str(Path.home() / "data/browsecomp-plus")))
EMBED = os.environ.get("BCP_EMBED_MODEL", str(Path.home() / "models/Qwen3-Embedding-8B"))


class Corpus:
    def __init__(self, prepared: Path):
        self.offsets = json.loads((prepared / "corpus_offsets.json").read_text())
        self._fh = open(prepared / "corpus.txt", "rb")
        self._mm = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)

    def text(self, docid: str):
        e = self.offsets.get(str(docid))
        if e is None:
            return None
        return self._mm[e[0]: e[0] + e[1]].decode("utf-8")

    def url(self, docid: str) -> str:
        e = self.offsets.get(str(docid))
        return e[2] if e else ""


class Retriever:
    def __init__(self, prepared: Path, embed_path: str, query_max_len: int = 8192):
        from transformers import AutoModel, AutoTokenizer
        self.docids = json.loads((prepared / "index_docids.json").read_text())
        self.index = torch.from_numpy(np.load(prepared / "index.npy")).to("cuda")
        self.tok = AutoTokenizer.from_pretrained(embed_path, padding_side="left")
        # A fast tokenizer is not safe to call from two threads (the call changes its truncation
        # settings: "RuntimeError: Already borrowed"). Queries are encoded with `tok` under
        # `lock`; text is measured with a second instance, `text_tok`, under `text_lock`.
        self.text_tok = AutoTokenizer.from_pretrained(embed_path)
        self.text_lock = threading.Lock()
        self.model = AutoModel.from_pretrained(embed_path, torch_dtype=torch.float16).to("cuda").eval()
        self.query_max_len = query_max_len
        self.lock = threading.Lock()

    @torch.no_grad()
    def encode(self, queries: list[str]) -> torch.Tensor:
        batch = self.tok([TASK_PREFIX + q for q in queries], padding=True, truncation=True,
                         max_length=self.query_max_len, return_tensors="pt").to("cuda")   # caller holds self.lock
        hidden = self.model(**batch).last_hidden_state
        reps = hidden[:, -1]                       # left padding: the last position is the last token
        return torch.nn.functional.normalize(reps.float(), p=2, dim=-1)

    def search(self, queries: list[str], k: int):
        with self.lock:
            q = self.encode(queries)
            scores, idx = torch.topk(q @ self.index.T, k, dim=-1)
        scores, idx = scores.cpu().tolist(), idx.cpu().tolist()
        return [[(self.docids[i], s) for i, s in zip(row_i, row_s)] for row_i, row_s in zip(idx, scores)]

    def truncate(self, text: str, max_tokens: int):
        """First max_tokens tokens of text. Returns (text, was_truncated, n_tokens_shown)."""
        head = text[: max_tokens * 24]             # a token is far shorter than 24 characters on average
        with self.text_lock:
            enc = self.text_tok(head, add_special_tokens=False, return_offsets_mapping=True)
        ids = enc["input_ids"]
        if len(ids) <= max_tokens and len(head) == len(text):
            return text, False, len(ids)
        if len(ids) <= max_tokens:                 # the character cut came first
            return head, True, len(ids)
        end = enc["offset_mapping"][max_tokens - 1][1]
        return head[:end], True, max_tokens


# ----------------------------------------------------------------------------- eval
def run_eval(args) -> None:
    prepared = DATA / "prepared"
    queries = [json.loads(l) for l in open(prepared / "queries.jsonl", encoding="utf-8")]
    rel: dict[str, set[str]] = {}
    for line in open(args.qrels):
        qid, _, docid, r = line.split()
        if int(r) > 0:
            rel.setdefault(qid, set()).add(docid)
    r = Retriever(prepared, EMBED, query_max_len=args.query_max_len)
    t0 = time.time()
    ranked = {}
    for i in range(0, len(queries), args.batch):
        chunk = queries[i:i + args.batch]
        for q, hits in zip(chunk, r.search([c["query"] for c in chunk], 1000)):
            ranked[q["query_id"]] = [d for d, _ in hits]
    dt = time.time() - t0
    out = {}
    for k in (5, 100, 1000):
        out[f"recall@{k}"] = float(np.mean([len(set(ranked[q][:k]) & rel[q]) / len(rel[q]) for q in rel]))
    ndcg = []
    for q in rel:
        dcg = sum(1.0 / np.log2(i + 2) for i, d in enumerate(ranked[q][:10]) if d in rel[q])
        idcg = sum(1.0 / np.log2(i + 2) for i in range(min(len(rel[q]), 10)))
        ndcg.append(dcg / idcg)
    out["ndcg@10"] = float(np.mean(ndcg))
    print(json.dumps({"qrels": args.qrels, "n_queries": len(rel), "seconds": round(dt, 1),
                      **{k: round(100 * v, 2) for k, v in out.items()}}))


# ---------------------------------------------------------------------------- serve
def run_serve(args) -> None:
    import uvicorn
    from fastapi import FastAPI
    from pydantic import BaseModel

    prepared = DATA / "prepared"
    corpus = Corpus(prepared)
    retriever = Retriever(prepared, EMBED)
    log_path = Path(args.log); log_path.parent.mkdir(parents=True, exist_ok=True)
    log_lock = threading.Lock()

    def log(rec: dict) -> None:
        rec["t"] = round(time.time(), 3)
        with log_lock, open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # The limits are the server's, not the caller's: a request carries only the query or the
    # docid, so a sandbox cannot ask for longer snippets or more results.
    class SearchReq(BaseModel):
        query: str
        qid: str = ""

    class DocReq(BaseModel):
        docid: str
        qid: str = ""

    app = FastAPI()

    @app.get("/health")
    def health():
        return {"ok": True, "documents": len(retriever.docids)}

    @app.post("/search")
    def search(req: SearchReq):
        query = req.query.strip()
        if not query:
            return {"text": "search: empty query. Usage: search \"<query>\""}
        k = args.k
        hits = retriever.search([query], k)[0]
        parts = [f"Top {k} results for: {query}"]
        for n, (docid, score) in enumerate(hits, 1):
            snippet, cut, _ = retriever.truncate(corpus.text(docid) or "", args.snippet_tokens)
            parts.append(f"--- [{n}] docid: {docid} | score: {score:.3f}\n{snippet}"
                         + (" ...[truncated]" if cut else ""))
        log({"op": "search", "qid": req.qid, "query": query, "docids": [d for d, _ in hits],
             "scores": [round(s, 4) for _, s in hits]})
        return {"text": "\n".join(parts)}

    @app.post("/get_document")
    def get_document(req: DocReq):
        docid = req.docid.strip().strip("[]")
        text = corpus.text(docid)
        if text is None:
            log({"op": "get_document", "qid": req.qid, "docid": docid, "found": False})
            return {"text": f"get_document: no document with docid {docid!r}."}
        shown, cut, n = retriever.truncate(text, args.doc_tokens)
        log({"op": "get_document", "qid": req.qid, "docid": docid, "found": True, "truncated": cut})
        tail = f"\n...[document truncated at {args.doc_tokens} tokens]" if cut else ""
        return {"text": f"docid: {docid}\nurl: {corpus.url(docid)}\n\n{shown}{tail}"}

    if args.roles:
        # BCP-harness v0.1 (claude_docs/12 §5): the reader and the verifier behind the sandbox
        # commands search, read, verify, and the completion gate.
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from roles_v01 import Roles
        args.queries = str(prepared / "queries.jsonl")
        roles = Roles(corpus, retriever, args, log)

        class RoleReq(BaseModel):
            qid: str = ""
            query: str = ""
            docid: str = ""
            what: str = ""
            from_line: int = 1
            state: str = ""
            answer: str = ""
            source: str = ""

        # search, read and verify wait for model calls and can take minutes. The sandbox reaches
        # this server through Harbor's egress proxy, which drops a connection that stays silent
        # that long, so the sandbox command starts a job and polls for its result.
        import uuid
        from concurrent.futures import ThreadPoolExecutor
        jobs: dict[str, dict] = {}
        jobs_lock = threading.Lock()
        workers = ThreadPoolExecutor(max_workers=64)

        def run_job(job_id: str, op: str, req: RoleReq) -> None:
            try:
                if op == "search":
                    text = (roles.search(req.qid, req.query.strip(), req.state) if req.query.strip()
                            else 'search: empty query. Usage: search "<query>"')
                elif op == "read":
                    text = roles.read(req.qid, req.docid, req.what.strip(), req.from_line, req.state)
                else:
                    text = roles.verify(req.qid, req.answer, req.state)
            except Exception as exc:  # noqa: BLE001
                text = f"{op}: the tools server failed ({type(exc).__name__}: {exc}). Try again."
                log({"op": "error", "qid": req.qid, "at": op, "error": f"{type(exc).__name__}: {exc}"})
            with jobs_lock:
                jobs[job_id].update(done=True, text=text)

        @app.post("/v01/start/{op}")
        def v01_start(op: str, req: RoleReq):
            if op not in ("search", "read", "verify"):
                return {"error": f"unknown operation {op}"}
            job_id = uuid.uuid4().hex
            with jobs_lock:
                for k in [k for k, v in jobs.items() if time.time() - v["t"] > 7200]:
                    del jobs[k]
                jobs[job_id] = {"done": False, "text": "", "t": time.time()}
            workers.submit(run_job, job_id, op, req)
            return {"id": job_id}

        @app.get("/v01/poll/{job_id}")
        def v01_poll(job_id: str):
            with jobs_lock:
                job = jobs.get(job_id)
                if job is None:
                    return {"done": True, "text": "the tools server has no such job. Try the command again."}
                return {"done": job["done"], "text": job["text"]}

        @app.post("/v01/submit")
        def v01_submit(req: RoleReq):
            return {"text": roles.submit(req.qid, req.answer, req.state)}

        @app.post("/v01/finalize")
        def v01_finalize(req: RoleReq):
            return {"text": roles.finalize(req.qid, req.source, req.state)}

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=9010)
    s.add_argument("--log", required=True, help="JSONL log of every call (per run)")
    s.add_argument("--k", type=int, default=10, help="documents per search (CLM paper, App. E)")
    s.add_argument("--snippet-tokens", type=int, default=512)
    s.add_argument("--doc-tokens", type=int, default=8192)
    s.add_argument("--roles", action="store_true", help="serve the v0.1 roles (reader, verifier, gate)")
    s.add_argument("--llm-base", default="http://127.0.0.1:9009/v1")
    s.add_argument("--llm-model", default="qwen3.5-9b")
    s.add_argument("--llm-key", default="token-abc123", help="the local vLLM server's fixed key")
    s.add_argument("--llm-timeout", type=int, default=1500)
    s.add_argument("--role-temperature", type=float, default=0.7)
    s.add_argument("--reader-skill", default="reader_excerpt_bcp_v1")
    s.add_argument("--verifier-skill", default="verifier_bcp_v1")
    s.add_argument("--reader-workers", type=int, default=32)
    s.add_argument("--reader-max-tokens", type=int, default=600)
    s.add_argument("--reader-thinking", action="store_true",
                   help="reader calls think first (off: 13 of 15 thinking calls ran into a 2,000-token limit, claude_docs/12 §12)")
    s.add_argument("--excerpt-rows", type=int, default=4, help="LINES rows taken from one reader reply")
    s.add_argument("--excerpt-lines", type=int, default=8, help="lines copied from one document")
    s.add_argument("--result-tokens", type=int, default=2500, help="excerpt text in one search result")
    s.add_argument("--verifier-max-tokens", type=int, default=4096)
    s.add_argument("--verifier-source-tokens", type=int, default=8000)
    s.add_argument("--cite-context", type=int, default=3, help="lines shown around a cited range")
    s.add_argument("--report-tokens", type=int, default=1500)
    s.add_argument("--max-unverified-claims", type=int, default=2)
    e = sub.add_parser("eval")
    e.add_argument("--qrels", default=str(DATA / "qrels/qrel_evidence.txt"))
    e.add_argument("--batch", type=int, default=16)
    e.add_argument("--query-max-len", type=int, default=8192)
    a = ap.parse_args()
    run_eval(a) if a.cmd == "eval" else run_serve(a)
