"""Reader and verifier of the BCP-harness v0.1 (claude_docs/12 §5), run by the tools server.

The harness carries text between the roles and does not judge: it retrieves, numbers lines,
copies the lines a role points to, attaches the solver's state table to a role's prompt and
counts tokens. Whether something is relevant, whether a citation supports a claim and whether
an answer holds is decided by a model call.

  search   top 10; every document this question has not seen is read by the reader, one call
           per document, in parallel; a document seen before shows its stored result
  read     one reader call on one document, with the solver's own request
  verify   one verifier call, one round; a second call returns the stored report
  submit   the completion gate: a submit before a verifier round is refused, at most twice
  finalize end of the run: where the answer file came from (written by the sandbox command)

Imported by tools_server.py (`serve --roles`); needs the vLLM server for the role calls.
"""
from __future__ import annotations

import hashlib, json, re, threading, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

SUBMIT_MARKER = "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
WRAP = 300                       # characters per numbered line
CITE = re.compile(r"\[\s*(\d+)\s*:\s*(\d+)(?:\s*-\s*(\d+))?\s*\]")


def wrap_lines(text: str) -> list[str]:
    """The document as lines: its own lines, empty ones dropped, long ones cut at a space."""
    out: list[str] = []
    for raw in text.split("\n"):
        raw = raw.rstrip()
        if not raw.strip():
            continue
        while len(raw) > WRAP:
            cut = raw.rfind(" ", 0, WRAP)
            cut = cut if cut > WRAP // 2 else WRAP
            out.append(raw[:cut]); raw = raw[cut:].lstrip()
        if raw:
            out.append(raw)
    return out


def doc_title(lines: list[str]) -> str:
    for line in lines[:8]:
        if line.lower().startswith("title:"):
            return line[6:].strip()[:120]
    return (lines[0][:120] if lines else "(empty document)")


def numbered(lines: list[str], a: int, b: int) -> str:
    return "\n".join(f"{i}| {lines[i - 1]}" for i in range(a, b + 1))


@dataclass
class Card:
    """What the reader took from one document, kept for the rest of the trial."""
    docid: str
    title: str
    n_lines: int
    read_from: int = 1
    read_to: int = 0
    relevant: bool = False
    match: str = "none"          # the reader's own grade: strong, weak or none
    excerpts: list[tuple[int, int, str]] = field(default_factory=list)   # first, last, label
    failed: bool = False


@dataclass
class Trial:
    cards: dict[str, Card] = field(default_factory=dict)
    report: str | None = None
    refused: int = 0
    state_versions: set = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)


class Roles:
    def __init__(self, corpus, retriever, args, log):
        self.corpus, self.retriever, self.args, self.log = corpus, retriever, args, log
        skills = Path(__file__).resolve().parent / "skills"
        self.reader_skill = (skills / args.reader_skill / "SKILL.md").read_text(encoding="utf-8")
        self.verifier_skill = (skills / args.verifier_skill / "SKILL.md").read_text(encoding="utf-8")
        # Questions only. The answers are never loaded into this process.
        self.questions = {}
        for line in open(Path(args.queries), encoding="utf-8"):
            row = json.loads(line); self.questions[row["query_id"]] = row["query"]
        self.trials: dict[str, Trial] = {}
        self.trials_lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=args.reader_workers)
        self._lines: dict[str, list[str]] = {}

    # ------------------------------------------------------------------ helpers
    def trial(self, qid: str) -> Trial:
        with self.trials_lock:
            return self.trials.setdefault(qid, Trial())

    def lines(self, docid: str) -> list[str] | None:
        if docid not in self._lines:
            text = self.corpus.text(docid)
            if text is None:
                return None
            if len(self._lines) > 4000:
                self._lines.clear()
            self._lines[docid] = wrap_lines(text)
        return self._lines[docid]

    def window_end(self, lines: list[str], start: int, max_tokens: int) -> int:
        """Last line of the window that starts at `start` and holds at most max_tokens."""
        used, end = 0, start - 1
        i = start - 1
        while i < len(lines):
            chunk = lines[i:i + 400]
            with self.retriever.text_lock:
                ids = self.retriever.text_tok(chunk, add_special_tokens=False)["input_ids"]
            for n in ids:
                used += len(n) + 4           # the line number and the line break
                if used > max_tokens:
                    return max(end, start)   # always at least one line
                end += 1
            i += 400
        return end

    def note_state(self, qid: str, state: str, op: str) -> None:
        h = hashlib.sha1(state.encode("utf-8")).hexdigest()[:10]
        t = self.trial(qid)
        if h not in t.state_versions:
            t.state_versions.add(h)
            self.log({"op": "state_version", "qid": qid, "at": op, "sha": h, "chars": len(state),
                      "n_versions": len(t.state_versions)})

    def chat(self, role: str, qid: str, system: str, user: str, max_tokens: int, marker: str,
             extra: dict | None = None, thinking_first: bool = True) -> str:
        """One role call. With thinking, a reply without the marker (cut inside its thinking)
        is asked again without thinking, once. thinking_first=False: one call without thinking."""
        content = ""
        for thinking in ((True, False) if thinking_first else (False,)):
            body = {"model": self.args.llm_model, "temperature": self.args.role_temperature, "top_p": 0.95,
                    "max_tokens": max_tokens, "chat_template_kwargs": {"enable_thinking": thinking},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            req = urllib.request.Request(self.args.llm_base.rstrip("/") + "/chat/completions",
                                         data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json",
                                                  "Authorization": f"Bearer {self.args.llm_key}"})
            t0 = time.time(); usage = {}; err = None
            try:
                with urllib.request.urlopen(req, timeout=self.args.llm_timeout) as resp:
                    data = json.load(resp)
                content = data["choices"][0]["message"].get("content") or ""
                usage = data.get("usage") or {}
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"; content = ""
            self.log({"op": "llm", "role": role, "qid": qid, "thinking": thinking,
                      "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
                      "system_chars": len(system), "user_chars": len(user), "seconds": round(time.time() - t0, 1),
                      "ok": marker in content, "error": err, **(extra or {})})
            if marker in content:
                break
        return content

    # ------------------------------------------------------------------- reader
    def read_one(self, qid: str, docid: str, state: str, request: str, start: int = 1) -> Card:
        lines = self.lines(docid) or []
        card = Card(docid=docid, title=doc_title(lines), n_lines=len(lines), read_from=start)
        if not lines or start > len(lines):
            card.read_to = len(lines)
            return card
        end = self.window_end(lines, start, self.args.doc_tokens)
        card.read_to = end
        user = (f"# Question\n{self.questions.get(qid, '(unknown question)')}\n\n"
                f"# State table of the solver\n{state.strip() or '(empty)'}\n\n"
                f"# Request\n{request}\n\n"
                f"# Document {docid} (lines {start}-{end} of {len(lines)})\n{numbered(lines, start, end)}")
        reply = self.chat("reader", qid, self.reader_skill, user, self.args.reader_max_tokens, "MATCH:",
                          {"docid": docid, "doc_lines": end - start + 1}, thinking_first=self.args.reader_thinking)
        m = re.search(r"MATCH:\s*\**\s*(strong|weak|none)", reply, re.I)
        if not m:
            card.failed = True
            return card
        card.match = m.group(1).lower()
        if card.match != "none":
            total = 0
            for a, b, label in re.findall(r"LINES:\s*(\d+)\s*(?:-\s*(\d+))?\s*\|?\s*(.*)", reply):
                a = int(a); b = int(b) if b else a
                a, b = max(start, min(a, b)), min(end, max(a, b))
                if b < a:
                    continue
                b = min(b, a + self.args.excerpt_lines - total - 1)     # the reader's limits, enforced
                if b < a or len(card.excerpts) >= self.args.excerpt_rows:
                    break
                card.excerpts.append((a, b, label.strip()[:160])); total += b - a + 1
            card.relevant = bool(card.excerpts)
            if not card.relevant:
                card.match = "none"
        return card

    def render_card(self, n: int | None, card: Card, show_text: bool, earlier: bool = False) -> str:
        head = f"[{n}] " if n is not None else ""
        where = f"read{' earlier:' if earlier else ''} lines {card.read_from}-{card.read_to} of {card.n_lines}"
        if card.failed:
            return f"{head}docid {card.docid} | {card.title} | {where} | the reader gave no usable reply"
        if not card.relevant:
            return f"{head}docid {card.docid} | {card.title} | {where} | nothing relevant"
        lines = self.lines(card.docid) or []
        out = [f"{head}docid {card.docid} | {card.title} | {where} | match: {card.match}"]
        for a, b, label in card.excerpts:
            out.append(f"  lines {a}-{b}: {label}")
            if show_text:
                out.append("\n".join("  " + row for row in numbered(lines, a, b).split("\n")))
        return "\n".join(out)

    def search(self, qid: str, query: str, state: str) -> str:
        self.note_state(qid, state, "search")
        t = self.trial(qid)
        hits = self.retriever.search([query], self.args.k)[0]
        new = [d for d, _ in hits if d not in t.cards]
        request = f"The solver searched for: {query}"
        for card in self.pool.map(lambda d: self.read_one(qid, d, state, request), new):
            with t.lock:
                t.cards.setdefault(card.docid, card)
        # A document shows the text of its lines once, in the search that had it read (strong
        # matches first, while the budget of the result lasts). When a later search returns it
        # again, it shows its line ranges and labels only: the smoke run repeated near-identical
        # searches, and repeating the text filled the solver's context with copies.
        budget = self.args.result_tokens * 4          # characters
        shown: dict[str, str] = {}
        ranges_only = False
        for d in sorted(new, key=lambda d: {"strong": 0, "weak": 1}.get(t.cards[d].match, 2)):
            full = self.render_card(None, t.cards[d], True)
            if len(full) <= budget:
                shown[d] = full; budget -= len(full)
            else:
                shown[d] = self.render_card(None, t.cards[d], False); ranges_only = True
        for d, _ in hits:
            if d not in shown:
                shown[d] = self.render_card(None, t.cards[d], False, earlier=True)
                ranges_only = ranges_only or t.cards[d].relevant
        parts = [f"Top {len(hits)} results for: {query}",
                 f"Read now: {len(new)} document(s). Read earlier in this task: {len(hits) - len(new)}."]
        for n, (d, _) in enumerate(hits, 1):
            parts.append(f"[{n}] " + shown[d])
        if ranges_only:
            parts.append('Where only line ranges are shown, the text was shown before or did not fit: '
                         'read_document <docid> "<what to look for>" shows it.')
        self.log({"op": "search", "qid": qid, "query": query, "docids": [d for d, _ in hits],
                  "scores": [round(s, 4) for _, s in hits], "new": new,
                  "strong": [d for d, _ in hits if t.cards[d].match == "strong"],
                  "weak": [d for d, _ in hits if t.cards[d].match == "weak"]})
        return "\n".join(parts)

    def read(self, qid: str, docid: str, what: str, from_line: int, state: str) -> str:
        self.note_state(qid, state, "read")
        docid = docid.strip().strip("[]")
        if self.lines(docid) is None:
            self.log({"op": "read", "qid": qid, "docid": docid, "found": False})
            return f"read_document: no document with docid {docid!r}."
        request = f"The solver asks you to read this document again and look for: {what}"
        card = self.read_one(qid, docid, state, request, max(1, from_line))
        t = self.trial(qid)
        with t.lock:
            old = t.cards.get(docid)
            if old is not None and card.relevant:       # keep what earlier readings found
                seen = {(a, b) for a, b, _ in card.excerpts}
                card_all = Card(**{**card.__dict__, "excerpts": card.excerpts + [e for e in old.excerpts if (e[0], e[1]) not in seen]})
                card_all.read_from = min(old.read_from, card.read_from); card_all.read_to = max(old.read_to, card.read_to)
                t.cards[docid] = card_all
            elif old is None:
                t.cards[docid] = card
        self.log({"op": "read", "qid": qid, "docid": docid, "found": True, "from_line": from_line,
                  "match": card.match, "what": what})
        return self.render_card(None, card, True)

    # ----------------------------------------------------------------- verifier
    def cited_lines(self, text: str) -> str:
        """The lines cited as [docid:a-b], with a few lines around them. Looked up, not judged."""
        blocks, seen, used = [], set(), 0
        for docid, a, b in CITE.findall(text):
            a = int(a); b = int(b) if b else a
            key = (docid, a, b)
            if key in seen:
                continue
            seen.add(key)
            lines = self.lines(docid)
            if lines is None:
                blocks.append(f"[{docid}:{a}-{b}] no document with this docid"); continue
            if a > len(lines) or b < a:
                blocks.append(f"[{docid}:{a}-{b}] the document has {len(lines)} lines; no such lines"); continue
            lo, hi = max(1, a - self.args.cite_context), min(len(lines), min(b, a + 40) + self.args.cite_context)
            block = f"[{docid}:{a}-{b}] document \"{doc_title(lines)}\", lines {lo}-{hi}:\n{numbered(lines, lo, hi)}"
            if used + len(block) > self.args.verifier_source_tokens * 4:
                blocks.append(f"[{docid}:{a}-{b}] not shown: the cited text exceeds what the verifier can be given"); continue
            used += len(block); blocks.append(block)
        return "\n\n".join(blocks) if blocks else "(no line is cited as [docid:lines])"

    def verify(self, qid: str, answer: str, state: str) -> str:
        self.note_state(qid, state, "verify")
        t = self.trial(qid)
        if not answer.strip():
            return "verify: /app/answer.md is missing or empty. Write your answer file first, then run verify."
        with t.lock:
            if t.report is not None:
                self.log({"op": "verify", "qid": qid, "stored": True})
                return "This answer round was already verified. The report was:\n\n" + t.report
        user = (f"# Question\n{self.questions.get(qid, '(unknown question)')}\n\n"
                f"# Answer file of the solver\n{answer.strip()}\n\n"
                f"# State table of the solver\n{state.strip() or '(empty)'}\n\n"
                f"# The cited lines\n{self.cited_lines(state + chr(10) + answer)}")
        reply = self.chat("verifier", qid, self.verifier_skill, user, self.args.verifier_max_tokens, "VERDICT:")
        report = reply.strip()[: self.args.report_tokens * 4] or "The verifier gave no usable reply."
        m = re.search(r"VERDICT:\s*\**\s*(ACCEPT|REVISE)", reply, re.I)
        with t.lock:
            t.report = report
        self.log({"op": "verify", "qid": qid, "stored": False, "verdict": m.group(1).upper() if m else None,
                  "answer_sha": hashlib.sha1(answer.encode()).hexdigest()[:10]})
        return "Verifier report (one round; read each row against the cited lines, then correct the answer file or keep it, and submit):\n\n" + report

    # --------------------------------------------------------- gate and the end
    def submit(self, qid: str, answer: str, state: str) -> str:
        self.note_state(qid, state, "submit")
        t = self.trial(qid)
        with t.lock:
            verified = t.report is not None
            if verified or t.refused >= self.args.max_unverified_claims:
                self.log({"op": "submit", "qid": qid, "accepted": True, "verified": verified,
                          "answer_file": bool(answer.strip())})
                return SUBMIT_MARKER
            t.refused += 1; k = t.refused
        self.log({"op": "submit", "qid": qid, "accepted": False, "attempt": k})
        return (f"Not submitted: the answer has not been through the verifier. Write your answer to "
                f"/app/answer.md, run `verify`, then submit again. (Refusal {k} of "
                f"{self.args.max_unverified_claims}; after that a submit is accepted without a verifier round.)")

    def finalize(self, qid: str, source: str, state: str) -> str:
        self.note_state(qid, state, "finalize")
        t = self.trial(qid)
        self.log({"op": "finalize", "qid": qid, "answer_source": source, "verified": t.report is not None,
                  "state_versions": len(t.state_versions), "documents_read": len(t.cards),
                  "documents_strong": sum(c.match == "strong" for c in t.cards.values()),
                  "documents_weak": sum(c.match == "weak" for c in t.cards.values())})
        return "ok"
