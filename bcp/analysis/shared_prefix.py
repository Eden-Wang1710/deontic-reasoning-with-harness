#!/usr/bin/env python3
"""Compute of the roles when the reader calls of a trial reuse the prefix they share
(claude_docs/13 §10.1, rule 3), for finished BCP-harness jobs.

Every reader prompt of a trial starts with the same text: the reader skill, the question,
and the state table as long as the solver has not changed it. CLM's formula counts a prefix
that the solver's own earlier call has processed as free; this script applies the same
assumption to the reader. What is exact and what is estimated:
- skill + question: EXACT. The text is rebuilt (skill file, question) and tokenized with the
  model's tokenizer and chat template.
- state table: exact while it is the empty template; for a table the solver has written the
  log holds only its length, so its tokens are estimated from the characters (the call's own
  tokens-per-character ratio).
- later turns of one document's conversation (v0.2): exact, from the log.
- verifier: the retry without thinking repeats the prompt of the call before it.
A prefix counts as reused under one of two assumptions:
- "order": some earlier reader call of the trial had it (a replay in order, as CLM does);
- "finished": such a call had ended before this one started (the calls of one search run in
  parallel, and a prefix that is still being computed cannot be reused).
The question text is read to count its tokens; only counts are written.

Run with the vLLM environment's python (needs transformers and the 9B tokenizer):
  python -I bcp/analysis/shared_prefix.py bcpsrvc-dev50 bcpsrvc2-dev50 [--json OUT]
"""
import argparse, bisect, collections, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "skills/bcp_solver_v1"))
DATA = Path.home() / "data/browsecomp-plus"
K = 2 * 6.9196e9 / 1e15                      # PFLOPs per token, Qwen3.5-9B (CLM's formula)
SENT = "\x00CUT\x00"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jobs", nargs="+")
    ap.add_argument("--tokenizer", default=str(Path.home() / "models/Qwen3.5-9B"))
    ap.add_argument("--json")
    a = ap.parse_args()
    from transformers import AutoTokenizer
    from _bcp import STATE_TEMPLATE
    tok = AutoTokenizer.from_pretrained(a.tokenizer)

    def ntok(system, user_prefix):
        s = tok.apply_chat_template([{"role": "system", "content": system}, {"role": "user", "content": user_prefix + SENT}],
                                    tokenize=False, add_generation_prompt=False)
        return len(tok(s[:s.index(SENT)], add_special_tokens=False).input_ids)

    question = {}
    for line in open(DATA / "prepared/queries.jsonl", encoding="utf-8"):
        x = json.loads(line); question[str(x["query_id"])] = x["query"]

    out = {}
    for job in a.jobs:
        rows = [json.loads(l) for l in open(DATA / "jobs" / f"{job}.tools.jsonl", encoding="utf-8") if l.lstrip().startswith("{")]
        v2 = any(r.get("op") == "reader_doc" for r in rows)
        skill = (HERE.parent / "skills" / ("reader_open_bcp_v2" if v2 else "reader_excerpt_bcp_v1") / "SKILL.md").read_text(encoding="utf-8")
        report = {str(r["qid"]): r for r in json.loads((DATA / "jobs" / job / "report.json").read_text())["rows"]}
        calls, states, chars_of = collections.defaultdict(list), collections.defaultdict(list), {}
        pending = collections.defaultdict(list)
        for r in rows:
            q = str(r.get("qid"))
            if r.get("op") == "llm":
                calls[q].append(r)
                if r.get("role") == "reader" and r.get("turn") == 0:
                    pending[(q, r["docid"])].append(r)
            elif r.get("op") == "reader_doc" and pending[(q, r["docid"])]:
                pending[(q, r["docid"])].pop(0)["first_prompt_chars"] = r["first_prompt_chars"]
            elif r.get("op") == "state_version":
                states[q].append((r["t"], r["sha"], r["chars"]))
        template_sha = collections.Counter(s[0][1] for s in map(sorted, states.values()) if s).most_common(1)[0][0]

        per = {}
        for q, cs in calls.items():
            base = ntok(skill, f"# Question\n{question[q]}\n\n# State table of the solver\n")
            with_template = ntok(skill, f"# Question\n{question[q]}\n\n# State table of the solver\n{STATE_TEMPLATE.strip()}\n\n# Request\n")
            sv = sorted(states[q]); ts = [x[0] for x in sv]
            x = dict(reader_prompt=0, reader_completion=0, conversation=0, verifier_prompt=0, verifier_completion=0,
                     verifier_cached=0, order_exact=0, order_state=0, finished_exact=0, finished_state=0, reader_calls=0)
            readers = sorted((c for c in cs if c["role"] == "reader"), key=lambda c: c["t"] - c["seconds"])
            ended = []                                           # (end time, sha) of the first turns so far
            prev = None
            for c in sorted(cs, key=lambda c: c["t"]):
                if c["role"] == "verifier":
                    p = c["prompt_tokens"] or 0
                    x["verifier_prompt"] += p; x["verifier_completion"] += c["completion_tokens"] or 0
                    x["verifier_cached"] += p if prev == (c.get("system_chars"), c.get("user_chars")) else 0
                    prev = (c.get("system_chars"), c.get("user_chars"))
            for c in readers:
                p = c["prompt_tokens"] or 0
                x["reader_calls"] += 1; x["reader_prompt"] += p; x["reader_completion"] += c["completion_tokens"] or 0
                if c.get("turn"):                                # v0.2: a later turn of a document
                    x["conversation"] += c.get("cached_prompt_tokens") or 0
                    continue
                start = c["t"] - c["seconds"]
                i = bisect.bisect_right(ts, c["t"]) - 1
                sha, chars = (sv[i][1], sv[i][2]) if i >= 0 else (template_sha, len(STATE_TEMPLATE.strip()))
                if sha == template_sha:
                    state_exact, state_est = with_template - base, 0
                else:
                    n_chars = c.get("first_prompt_chars") or (c.get("system_chars", 0) + c.get("user_chars", 0))
                    state_exact, state_est = 0, int(chars * p / max(1, n_chars))
                for name, before in (("order", ended), ("finished", [e for e in ended if e[0] <= start])):
                    if before:
                        same = any(s == sha for _, s in before)
                        x[f"{name}_exact"] += min(p, base + (state_exact if same else 0))
                        x[f"{name}_state"] += state_est if same else 0
                ended.append((c["t"], sha))
            per[q] = x

        n = len(report)
        m = {k: sum(x[k] for x in per.values()) / n for k in next(iter(per.values()))}
        solver = sum(r.get("pflops", 0) for r in report.values()) / n
        ver = K * (m["verifier_prompt"] - m["verifier_cached"] + m["verifier_completion"])
        rd = lambda cached: K * (m["reader_prompt"] - cached + m["reader_completion"])  # noqa: E731
        res = {"questions": n, "mean_per_question": {k: round(v, 1) for k, v in m.items()},
               "pflops_per_question": {
                   "solver": round(solver, 3), "verifier": round(ver, 3),
                   "reader_every_prompt_token": round(rd(0), 3),
                   "reader_conversation_prefix_once": round(rd(m["conversation"]), 3)}}
        for name in ("order", "finished"):
            lo = rd(m["conversation"] + m[f"{name}_exact"]); est = rd(m["conversation"] + m[f"{name}_exact"] + m[f"{name}_state"])
            res["pflops_per_question"][f"reader_shared_prefix_{name}_exact_part_only"] = round(lo, 3)
            res["pflops_per_question"][f"reader_shared_prefix_{name}_with_estimated_state"] = round(est, 3)
            res["pflops_per_question"][f"total_shared_prefix_{name}_exact_part_only"] = round(solver + ver + lo, 3)
            res["pflops_per_question"][f"total_shared_prefix_{name}_with_estimated_state"] = round(solver + ver + est, 3)
        res["pflops_per_question"]["total_every_prompt_token"] = round(solver + K * (m["verifier_prompt"] + m["verifier_completion"]) + rd(0), 3)
        res["pflops_per_question"]["total_conversation_prefix_once"] = round(solver + K * (m["verifier_prompt"] + m["verifier_completion"]) + rd(m["conversation"]), 3)
        out[job] = res
    print(json.dumps(out, indent=1))
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
