#!/usr/bin/env python3
"""Grade the answer files of a BrowseComp-Plus job with a judge model (claude_docs/12 §3.4).

  judge.py --job-dir <data>/jobs/<job> [--model qwen3.5-35b-a3b] [--api-base http://127.0.0.1:9009/v1]

For every trial directory it reads verifier/answer.md, asks the judge with the grading
template of BrowseComp-Plus (scripts_evaluation/evaluate_run.py, the template that repository
uses with an open-weight judge; MIT licence), at temperature 0, and parses the verdict the way
that script does. A missing answer file is wrong and costs no call.

Writes into <job-dir>/judge/:
  judgments.jsonl   question, response, judge reply (PLAIN TEXT: stays outside the repository)
  scores.json       per query id: correct, confidence, citation numbers; and the summary
Standard library only; run with `python -I`.
"""
import argparse, json, math, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

GRADER_TEMPLATE = """
Judge whether the following [response] to [question] is correct or not based on the precise and unambiguous [correct_answer] below.

[question]: {question}

[response]: {response}

[correct_answer]: {correct_answer}

Your judgement must be in the format and criteria specified below:

extracted_final_answer: The final exact answer extracted from the [response]. 

[correct_answer]: Repeat the [correct_answer] given above.

reasoning: Explain why the extracted_final_answer is correct or incorrect based on [correct_answer], in the context of this [question]. You should judge whether the extracted_final_answer is semantically equivalent to [correct_answer], allowing the extracted_final_answer to be string variations of [correct_answer]. You should also allow the extracted_final_answer to be more precise or verbose than [correct_answer], as long as its additional details are correct. Do not comment on any background to the problem, do not attempt to solve the problem, do not argue for any answer different than [correct_answer], focus only on whether the answers are semantically equivalent.

correct: Answer 'yes' if extracted_final_answer matches the [correct_answer] given above, or is within a small margin of error for numerical problems. Answer 'no' otherwise, i.e. if there if there is any inconsistency, ambiguity, non-equivalency, or if the extracted answer is incorrect.


confidence: The extracted confidence score between 0|\\%| and 100|\\%| from [response]. Put 100 if there is no confidence score available.
""".strip()


def parse_judge(text: str) -> dict:
    out = {"extracted_final_answer": None, "correct": None, "confidence": None}
    for pat in (r"\*\*extracted_final_answer:\*\*\s*(.*?)(?=\n|$)", r"\*\*extracted_final_answer\*\*:\s*(.*?)(?=\n|$)",
                r"extracted_final_answer:\s*(.*?)(?=\n|$)"):
        m = re.search(pat, text, re.I | re.S)
        if m:
            out["extracted_final_answer"] = m.group(1).strip(); break
    for pat in (r"\*\*correct:\*\*\s*(yes|no)", r"\*\*correct\*\*:\s*(yes|no)", r"correct:\s*(yes|no)"):
        m = re.search(pat, text, re.I)
        if m:
            out["correct"] = m.group(1).lower() == "yes"; break
    for pat in (r"\*\*confidence:\*\*\s*(\d+(?:\.\d+)?)\s*%?", r"\*\*confidence\*\*:\s*(\d+(?:\.\d+)?)\s*%?",
                r"confidence:\s*(\d+(?:\.\d+)?)\s*%?"):
        m = re.search(pat, text, re.I)
        if m:
            out["confidence"] = min(float(m.group(1)), 100.0); break
    return out


def citations(response: str) -> list[str]:
    """Docids cited in square brackets, as BCP's evaluate_run.py extracts them."""
    ids: set[str] = set()
    for inner in re.findall(r"\[([^\[\]]*?)\]", response) + re.findall(r"【([^【】]*?)】", response):
        ids.update(re.findall(r"\d+", inner))
    return sorted(ids, key=int)


def load_qrels(path: Path) -> dict[str, set[str]]:
    rel: dict[str, set[str]] = {}
    if path.exists():
        for line in open(path):
            qid, _, docid, r = line.split()
            if int(r) > 0:
                rel.setdefault(qid, set()).add(docid)
    return rel


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def chat(api_base: str, key: str, model: str, prompt: str, max_tokens: int, thinking: bool) -> str:
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.0,
            "max_tokens": max_tokens, "chat_template_kwargs": {"enable_thinking": thinking}}
    req = urllib.request.Request(api_base.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=3600) as resp:
        return json.load(resp)["choices"][0]["message"].get("content") or ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-dir", required=True)
    ap.add_argument("--data", default=str(Path.home() / "data/browsecomp-plus"))
    ap.add_argument("--api-base", default="http://127.0.0.1:9009/v1")
    ap.add_argument("--api-key", default="token-abc123", help="the local vLLM server's fixed key")
    ap.add_argument("--model", default="qwen3.5-35b-a3b")
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    job = Path(a.job_dir); data = Path(a.data)
    truth = {}
    for line in open(data / "prepared/queries.jsonl", encoding="utf-8"):
        row = json.loads(line); truth[row["query_id"]] = row
    evidence = load_qrels(data / "qrels/qrel_evidence.txt"); gold = load_qrels(data / "qrels/qrel_golds.txt")

    trials = []
    for d in sorted(p for p in job.iterdir() if p.is_dir()):
        m = re.match(r"bcp-q(\d+)", d.name)
        if not m or not (d / "config.json").exists():
            continue
        ans = d / "verifier/answer.md"
        trials.append({"trial": d.name, "qid": m.group(1),
                       "response": ans.read_text(encoding="utf-8", errors="replace").strip() if ans.exists() else ""})
    if not trials:
        sys.exit(f"no trial directories in {job}")

    def grade(t: dict) -> dict:
        q = truth[t["qid"]]
        rec = {**t, "question": q["query"], "correct_answer": q["answer"], "judge_reply": "", "judge_calls": 0}
        if not t["response"]:
            return {**rec, "correct": False, "confidence": None, "extracted_final_answer": None, "status": "no_answer"}
        prompt = GRADER_TEMPLATE.format(question=q["query"], response=t["response"], correct_answer=q["answer"])
        parsed = {"correct": None}
        for thinking in (True, False):          # a reply without a verdict is asked again without thinking
            try:
                rec["judge_reply"] = chat(a.api_base, a.api_key, a.model, prompt, a.max_tokens, thinking)
            except Exception as exc:  # noqa: BLE001
                rec["judge_reply"] = f"[judge call failed: {exc}]"
            rec["judge_calls"] += 1
            parsed = parse_judge(rec["judge_reply"])
            if parsed["correct"] is not None:
                break
        status = "judged" if parsed["correct"] is not None else "judge_parse_error"
        return {**rec, **parsed, "correct": bool(parsed["correct"]), "status": status}

    with ThreadPoolExecutor(a.workers) as ex:
        graded = list(ex.map(grade, trials))

    out = job / "judge"; out.mkdir(exist_ok=True)
    with open(out / "judgments.jsonl", "w", encoding="utf-8") as fh:
        for g in graded:
            fh.write(json.dumps(g, ensure_ascii=False) + "\n")
    rows = []
    for g in graded:
        cited = set(citations(g["response"])); ev = evidence.get(g["qid"], set()); gd = gold.get(g["qid"], set())
        rows.append({"qid": g["qid"], "trial": g["trial"], "status": g["status"], "correct": g["correct"],
                     "confidence": g["confidence"], "n_cited": len(cited),
                     "cite_precision_evidence": len(cited & ev) / len(cited) if cited else None,
                     "cite_recall_evidence": len(cited & ev) / len(ev) if ev else None,
                     "cite_recall_gold": len(cited & gd) / len(gd) if gd else None})
    n = len(rows); k = sum(r["correct"] for r in rows); lo, hi = wilson(k, n)
    answered = [r for r in rows if r["status"] != "no_answer"]
    conf = [(r["confidence"], r["correct"]) for r in answered if r["confidence"] is not None]
    mean = lambda xs: (sum(xs) / len(xs)) if xs else None  # noqa: E731
    summary = {
        "job": job.name, "judge_model": a.model, "n": n, "correct": k,
        "accuracy_pct": round(100 * k / n, 2), "wilson95_pct": [round(100 * lo, 1), round(100 * hi, 1)],
        "no_answer": sum(r["status"] == "no_answer" for r in rows),
        "judge_parse_error": sum(r["status"] == "judge_parse_error" for r in rows),
        "mean_confidence_pct": round(mean([c for c, _ in conf]), 1) if conf else None,
        "accuracy_of_answered_pct": round(100 * mean([float(r["correct"]) for r in answered]), 2) if answered else None,
        "cite_precision_evidence": mean([r["cite_precision_evidence"] for r in rows if r["cite_precision_evidence"] is not None]),
        "cite_recall_evidence": mean([r["cite_recall_evidence"] or 0.0 for r in rows]),
        "cite_recall_gold": mean([r["cite_recall_gold"] or 0.0 for r in rows]),
    }
    (out / "scores.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
