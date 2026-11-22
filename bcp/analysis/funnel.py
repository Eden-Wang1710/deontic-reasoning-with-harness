#!/usr/bin/env python3
"""Where an answer is lost or won: retrieved -> shown to the solver -> answered -> correct
(claude_docs/13 §3), for one or two BrowseComp-Plus jobs on the same questions.

For every trial:
- retrieved: the reference answer, as a string, is in a document that a search returned
  (in512: within its first 2,100 characters, about the 512 tokens of a baseline snippet);
- shown: the string is in the output of a command, taken from the per-turn context snapshots
  (so output that was later rolled back or edited away still counts);
- the size of what each kind of command returned to the solver;
- the context editing of the trial (agent/usage.json).
String matching is crude: a reference answer that the documents paraphrase is never "retrieved".
The output holds question ids, counts and booleans only; no question or answer text.

Usage: funnel.py JOB [JOB2] [--json OUT]
"""
import argparse, collections, glob, json, mmap, re, statistics as st
from pathlib import Path

DATA = Path.home() / "data/browsecomp-plus"
JOBS = DATA / "jobs"
norm = lambda s: re.sub(r"\s+", " ", s).casefold().strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jobs", nargs="+"); ap.add_argument("--json")
    a = ap.parse_args()
    off = json.loads((DATA / "prepared/corpus_offsets.json").read_text())
    f = open(DATA / "prepared/corpus.txt", "rb"); mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    doc = lambda d: mm[off[d][0]:off[d][0] + off[d][1]].decode("utf-8", "replace")
    answer = {}
    for line in open(DATA / "prepared/queries.jsonl"):
        x = json.loads(line); answer[str(x["query_id"])] = norm(str(x["answer"]))
    gold = collections.defaultdict(set)
    for line in open(DATA / "qrels/qrel_golds.txt"):
        p = line.split(); gold[p[0]].add(p[2])

    def run(job):
        rows = {r["qid"]: r for r in json.loads((JOBS / job / "report.json").read_text())["rows"]}
        searches = collections.defaultdict(list)
        for line in open(JOBS / f"{job}.tools.jsonl"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("op") == "search":
                searches[str(d["qid"])].append([str(x) for x in d["docids"]])
        out, sizes = {}, collections.defaultdict(list)
        for q, r in rows.items():
            d = glob.glob(str(JOBS / job / f"bcp-q{q}__*"))[0]
            cmd, tool, order = {}, {}, []
            for snap in sorted(glob.glob(d + "/agent/context_snapshots/turn-*.json")):
                for m in json.loads(Path(snap).read_text())["messages"]:
                    if m.get("role") == "assistant" and m.get("tool_calls"):
                        for tc in m["tool_calls"]:
                            try:
                                cmd[tc["id"]] = json.loads(tc["function"]["arguments"]).get("command", "")
                            except Exception:
                                cmd[tc["id"]] = ""
                    if m.get("role") == "tool" and m.get("tool_call_id") not in tool:
                        tool[m["tool_call_id"]] = m.get("content") or ""; order.append(m["tool_call_id"])
            for k in order:
                c = cmd.get(k, "").strip()
                kind = ("search" if c.startswith("search") else "document" if c.startswith(("get_document", "read_document"))
                        else "verify" if c.startswith("verify") else "other")
                sizes[kind].append(len(tool[k]))
            texts = [tool[k] for k in order]
            docs = list(dict.fromkeys(x for s in searches[q] for x in s))
            u = json.loads(Path(d, "agent/usage.json").read_text())
            g = gold[q]
            out[q] = dict(
                correct=bool(r["correct"]), answered=bool(r["answer_file"]), end=r["end"], lm_calls=r["lm_calls"],
                searches=len(searches[q]), retrieved=any(answer[q] in norm(doc(x)) for x in docs),
                in512=any(answer[q] in norm(doc(x)[:2100]) for x in docs),
                shown=any(answer[q] in norm(t) for t in texts),
                first_gold_at_search=next((i + 1 for i, s in enumerate(searches[q]) if g & set(s)), None),
                gold_recall_19_searches=round(len(g & {x for s in searches[q][:19] for x in s}) / max(1, len(g)), 3),
                retries_on_limit=u["n_retry_on_limit"], turns_rolled_back=u["n_turns_rolled_back"],
                context_edits=u["n_ctx_syncs_real"], edits_rejected=u["n_ctx_rejected"])
        return out, {k: dict(n=len(v), mean_chars=round(st.mean(v)), median_chars=round(st.median(v))) for k, v in sizes.items()}

    result = {}
    for job in a.jobs:
        R, sizes = run(job)
        c = lambda k: sum(1 for x in R.values() if x[k])
        lim = [x for x in R.values() if x["end"] == "context_limit"]
        summary = dict(
            n=len(R), retrieved=c("retrieved"), in512=c("in512"), shown=c("shown"), answered=c("answered"), correct=c("correct"),
            gold_recall_19_searches=round(st.mean(x["gold_recall_19_searches"] for x in R.values()), 3),
            median_first_gold_at_search=st.median([x["first_gold_at_search"] for x in R.values() if x["first_gold_at_search"]]),
            no_gold_retrieved=sum(1 for x in R.values() if not x["first_gold_at_search"]),
            context_limit=dict(n=len(lim), all_retries_used=sum(x["retries_on_limit"] >= 6 for x in lim),
                               no_edit=sum(x["context_edits"] == 0 for x in lim), edits=sum(x["context_edits"] for x in lim)),
            command_output=sizes)
        print(f"== {job}: {json.dumps(summary)}")
        result[job] = dict(summary=summary, rows=R)
    if len(a.jobs) == 2:
        B, V = (result[j]["rows"] for j in a.jobs)
        gained = [q for q in B if V[q]["correct"] and not B[q]["correct"]]
        cls = {}
        for q in gained:
            b, v = B[q], V[q]
            cls[q] = ("shown_in_first_job_but_no_answer" if b["shown"] else "deep_in_document" if v["shown"] and not v["in512"]
                      else "retrieved_only_by_second_job" if v["retrieved"] and not b["retrieved"] else "no_string_match")
        result["gained_by_second_job"] = cls
        print("gained:", dict(collections.Counter(cls.values())), "| first job ended:", dict(collections.Counter(B[q]["end"] for q in gained)))
    if a.json:
        Path(a.json).write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
