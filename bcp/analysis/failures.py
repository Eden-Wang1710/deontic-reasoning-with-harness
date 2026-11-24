#!/usr/bin/env python3
"""The failures of a job, by how the trial ended and by what happened to the gold documents
(claude_docs/13 §3.7, §10), and the paired comparison of jobs on the same questions.

A gold document is a document of qrel_golds.txt. "Returned": a search of the trial returned
one. "Reached the solver": in the baseline the solver opened one with get_document; in the
BCP-harness the reader graded one strong or weak (at a search or at a read_document), so its
lines were on a card. Read from <job>/judge/scores.json, <job>/report.json and
<job>.tools.jsonl. Ids and counts only: no question or answer text is read or written.

  python3 -I bcp/analysis/failures.py clm-base-dev50 bcpsrvc-dev50 bcpsrvc2-dev50 [--json OUT]
"""
import argparse, collections, json
from math import comb
from pathlib import Path

DATA = Path.home() / "data/browsecomp-plus"


def qrels(path):
    rel = collections.defaultdict(set)
    for line in open(path):
        p = line.split()
        if int(p[3]) > 0:
            rel[p[0]].add(p[2])
    return rel


def mcnemar(b, c):
    n = b + c
    return 1.0 if n == 0 else min(1.0, 2 * sum(comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n)


def job_table(job, gold):
    rows = {str(r["qid"]): r for r in json.loads((DATA / "jobs" / job / "report.json").read_text())["rows"]}
    score = {str(r["qid"]): r for r in json.loads((DATA / "jobs" / job / "judge/scores.json").read_text())["rows"]}
    t = collections.defaultdict(lambda: dict(searches=0, returned=None, reached=None, gold_reads=0, verifies=0,
                                             gold_first={}))
    for line in open(DATA / "jobs" / f"{job}.tools.jsonl", encoding="utf-8"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        q, op = str(d.get("qid")), d.get("op")
        x = t[q]
        if op == "search":
            x["searches"] += 1
            if x["returned"] is None and gold[q] & set(map(str, d["docids"])):
                x["returned"] = x["searches"]
            if "strong" in d:                                  # BCP-harness: the reader graded the new documents
                passed = set(map(str, d["strong"])) | set(map(str, d["weak"]))
                for g in gold[q] & set(map(str, d.get("new", []))):
                    x["gold_first"].setdefault(g, "strong" if g in set(map(str, d["strong"])) else
                                               "weak" if g in passed else "none")
                if x["reached"] is None and gold[q] & passed:
                    x["reached"] = x["searches"]
        elif op == "get_document" and str(d.get("docid")) in gold[q]:
            if x["reached"] is None:
                x["reached"] = x["searches"]
        elif op == "read" and str(d.get("docid")) in gold[q]:
            x["gold_reads"] += 1
            if x["reached"] is None and d.get("match") in ("strong", "weak"):
                x["reached"] = x["searches"]
        elif op == "verify":
            x["verifies"] += 1
    out = {}
    for q, r in rows.items():
        x = t[q]
        out[q] = dict(correct=bool(score[q]["correct"]), end=r["end"], answer_file=bool(r["answer_file"]),
                      total_pflops=r.get("total_pflops", r.get("pflops")), **x)
    return out


def summary(job, tab):
    wrong = {q: x for q, x in tab.items() if not x["correct"]}
    res = {"job": job, "n": len(tab), "correct": len(tab) - len(wrong), "wrong": len(wrong),
           "ended": dict(collections.Counter(x["end"] for x in tab.values())),
           "correct_by_end": dict(collections.Counter(x["end"] for x in tab.values() if x["correct"])),
           "wrong_by_end": dict(collections.Counter(
               f"{x['end']}/{'wrong answer' if x['answer_file'] else 'no answer'}" for x in wrong.values())),
           "pflops_by_end": {e: round(sum(x["total_pflops"] for x in tab.values() if x["end"] == e), 1)
                             for e in {x["end"] for x in tab.values()}}}
    cls = collections.defaultdict(list)
    for q, x in sorted(wrong.items(), key=lambda kv: int(kv[0])):
        if x["returned"] is None:
            k = "never returned"
        elif x["reached"] is None:
            k = "returned, did not reach the solver"
        else:
            k = "reached the solver, " + ("wrong answer" if x["answer_file"] else "no answer")
        cls[k].append(dict(qid=q, end=x["end"], searches=x["searches"], returned_at=x["returned"],
                           reached_at=x["reached"], gold_reads=x["gold_reads"], verifies=x["verifies"]))
    res["wrong_by_gold"] = {k: {"n": len(v), "mean_searches": round(sum(y["searches"] for y in v) / len(v), 1),
                                "trials": v} for k, v in cls.items()}
    first = collections.Counter(g for x in tab.values() for g in x["gold_first"].values())
    if first:
        n = sum(first.values())
        res["gold_first_reading"] = {**first, "documents": n,
                                     "passed_on_pct": round(100 * (first["strong"] + first["weak"]) / n, 1)}
    res["questions_with_gold_returned"] = sum(x["returned"] is not None for x in tab.values())
    res["questions_with_gold_reached"] = sum(x["reached"] is not None for x in tab.values())
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jobs", nargs="+")
    ap.add_argument("--json")
    a = ap.parse_args()
    gold = qrels(DATA / "qrels/qrel_golds.txt")
    tabs = {j: job_table(j, gold) for j in a.jobs}
    out = {"jobs": {j: summary(j, t) for j, t in tabs.items()}, "paired": {}}
    for i, j1 in enumerate(a.jobs):
        for j2 in a.jobs[i + 1:]:
            qs = sorted(set(tabs[j1]) & set(tabs[j2]), key=int)
            both = [q for q in qs if tabs[j1][q]["correct"] and tabs[j2][q]["correct"]]
            only1 = [q for q in qs if tabs[j1][q]["correct"] and not tabs[j2][q]["correct"]]
            only2 = [q for q in qs if not tabs[j1][q]["correct"] and tabs[j2][q]["correct"]]
            out["paired"][f"{j1} vs {j2}"] = dict(
                n=len(qs), both=len(both), neither=len(qs) - len(both) - len(only1) - len(only2),
                only_first=only1, only_second=only2, mcnemar_exact_p=round(mcnemar(len(only1), len(only2)), 4))
    print(json.dumps(out, indent=1))
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
