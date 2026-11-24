"""Reader of the BCP-harness v0.2 (claude_docs/12 §13): it sees the beginning of a document
and decides itself whether to read on, how much and which part.

v0.1's reader got up to 8,192 tokens of every new document; 87% of its input went to
documents it then graded `none` (claude_docs/13 §3.5, §4). Here the first prompt holds the
first `--first-tokens` (512) tokens and the number of lines of the document. The reader
replies with its result, or with one action:

    OPEN: <first>-<last>     these lines, at most --open-tokens per action
    FIND: <phrase>; <phrase> every line that contains one of the phrases, with the line
                             before and after, at most --find-tokens

at most `--reader-actions` (3) per document. Each action is a further turn of the same
conversation, so the model server reuses the prefix it has already processed. The harness
does what it did in v0.1: it numbers lines, looks lines up, copies the lines the reader
points to and counts tokens. The text search of FIND is a lookup (substring, case ignored);
what to look for and whether a line matters is the reader's decision.

Everything else (search, the cards, read_document, verifier, gate) is roles_v01.
"""
from __future__ import annotations

import json, re, time, urllib.request
from dataclasses import dataclass, field

from roles_v01 import Card, Roles, doc_title, numbered

ACTION = re.compile(r"^\s*\**\s*(OPEN|FIND)\s*\**\s*:\s*(.+?)\s*$", re.I | re.M)


@dataclass
class Card2(Card):
    seen: list[tuple[int, int]] = field(default_factory=list)   # line ranges the reader was shown
    actions: list[str] = field(default_factory=list)


def merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def ranges_text(ranges: list[tuple[int, int]]) -> str:
    return ", ".join(f"{a}-{b}" if b > a else f"{a}" for a, b in ranges)


class Roles2(Roles):
    # ---------------------------------------------------------------- one call
    def turn(self, qid: str, messages: list[dict], extra: dict) -> tuple[str, dict]:
        """One reader call on a conversation, without thinking. Returns (reply, usage)."""
        body = {"model": self.args.llm_model, "temperature": self.args.role_temperature, "top_p": 0.95,
                "max_tokens": self.args.reader_max_tokens, "chat_template_kwargs": {"enable_thinking": False},
                "messages": messages}
        req = urllib.request.Request(self.args.llm_base.rstrip("/") + "/chat/completions",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.args.llm_key}"})
        t0 = time.time(); usage: dict = {}; err = None; content = ""
        try:
            with urllib.request.urlopen(req, timeout=self.args.llm_timeout) as resp:
                data = json.load(resp)
            content = data["choices"][0]["message"].get("content") or ""
            usage = data.get("usage") or {}
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
        self.log({"op": "llm", "role": "reader", "qid": qid, "thinking": False,
                  "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
                  "seconds": round(time.time() - t0, 1), "ok": bool(content) and err is None, "error": err, **extra})
        return content, usage

    # ------------------------------------------------------------- the actions
    def do_open(self, lines: list[str], arg: str, seen: list[tuple[int, int]]) -> tuple[str, list[tuple[int, int]]]:
        m = re.match(r"\s*(\d+)\s*(?:-|to|–)?\s*(\d+)?", arg)
        if not m:
            return "OPEN needs line numbers, for example `OPEN: 40-120`.", []
        a = int(m.group(1)); b = int(m.group(2)) if m.group(2) else a + 60
        a, b = max(1, min(a, b)), min(len(lines), max(a, b))
        if a > len(lines):
            return f"The document has only {len(lines)} lines.", []
        if any(s <= a and b <= e for s, e in seen):
            return f"Lines {a}-{b} were already shown above.", []
        end = min(b, self.window_end(lines, a, self.args.open_tokens))
        note = f" (the range was cut at line {end}: one action shows at most this much)" if end < b else ""
        return f"Lines {a}-{end} of {len(lines)}{note}:\n{numbered(lines, a, end)}", [(a, end)]

    def do_find(self, lines: list[str], arg: str) -> tuple[str, list[tuple[int, int]]]:
        phrases = [p.strip().strip("\"'`").casefold() for p in re.split(r"[;|]", arg)]
        phrases = [p for p in phrases if len(p) >= 2][:6]
        if not phrases:
            return "FIND needs a word or phrase, for example `FIND: Lagos; 1957`.", []
        hits = [i for i, line in enumerate(lines, 1) if any(p in line.casefold() for p in phrases)]
        what = "; ".join(f'"{p}"' for p in phrases)
        if not hits:
            return f"No line of the document contains {what}.", []
        ranges = merge([(max(1, i - 1), min(len(lines), i + 1)) for i in hits])
        shown, used, n_hits = [], 0, 0
        for a, b in ranges:
            with self.retriever.text_lock:
                cost = sum(len(x) + 4 for x in self.retriever.text_tok(lines[a - 1:b], add_special_tokens=False)["input_ids"])
            if shown and used + cost > self.args.find_tokens:
                break
            shown.append((a, b)); used += cost
            n_hits += sum(1 for i in hits if a <= i <= b)
        rest = (f" Shown: the first {n_hits}; the others are at lines "
                f"{ranges_text([(i, i) for i in hits if i > shown[-1][1]][:30])}." if n_hits < len(hits) else "")
        body = "\n...\n".join(numbered(lines, a, b) for a, b in shown)
        return f"{len(hits)} line(s) contain {what}.{rest}\n{body}", shown

    # ------------------------------------------------------------------ reader
    def read_one(self, qid: str, docid: str, state: str, request: str, start: int = 1) -> Card2:
        lines = self.lines(docid) or []
        card = Card2(docid=docid, title=doc_title(lines), n_lines=len(lines), read_from=start)
        if not lines or start > len(lines):
            card.read_to = len(lines)
            return card
        end = self.window_end(lines, start, self.args.first_tokens)
        seen = [(start, end)]
        part = "the beginning" if start == 1 else f"from line {start}"
        rest = (f"{len(lines) - end} more lines are not shown" if end < len(lines) and start == 1
                else "the whole document is shown" if start == 1 and end >= len(lines)
                else "the other lines are not shown")
        user = (f"# Question\n{self.questions.get(qid, '(unknown question)')}\n\n"
                f"# State table of the solver\n{state.strip() or '(empty)'}\n\n"
                f"# Request\n{request}\n\n"
                f"# Document {docid}: {len(lines)} lines in total. Shown: lines {start}-{end} ({part}); {rest}.\n"
                f"{numbered(lines, start, end)}")
        messages = [{"role": "system", "content": self.reader_skill}, {"role": "user", "content": user}]
        cached, tokens, graded = 0, 0, False
        found: list[tuple[str, list[tuple[int, int, str]]]] = []      # per reply: grade, its valid LINES
        for turn in range(self.args.reader_actions + 1):
            reply, usage = self.turn(qid, messages, {"docid": docid, "turn": turn, "cached_prompt_tokens": cached})
            tokens += (usage.get("prompt_tokens") or 0) - cached + (usage.get("completion_tokens") or 0)
            # The next call of this conversation repeats this prompt and this reply as its prefix.
            cached = (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)
            m = re.search(r"MATCH:\s*\**\s*(strong|weak|none)", reply, re.I)
            if m:
                graded = True
                if m.group(1).lower() != "none":
                    rows = self.valid_rows(reply, seen)
                    if rows:
                        found.append((m.group(1).lower(), rows))
            act = ACTION.search(reply)
            if not act or turn == self.args.reader_actions:
                break
            kind, arg = act.group(1).upper(), act.group(2)
            text, new = self.do_open(lines, arg, seen) if kind == "OPEN" else self.do_find(lines, arg)
            seen = merge(seen + new); card.actions.append(kind)
            left = self.args.reader_actions - turn - 1
            tail = (f"Reply in the same form: SCAN, MATCH, LINES for useful lines you have newly found, and an "
                    f"action only if you need one ({left} left)." if left else
                    "No actions are left for this document. Reply with SCAN, MATCH and LINES for useful lines "
                    "you have newly found, without an action.")
            messages += [{"role": "assistant", "content": reply}, {"role": "user", "content": f"{text}\n\n{tail}"}]
        card.seen = seen; card.read_from = seen[0][0]; card.read_to = seen[-1][1]
        card.failed = not graded
        # The result of the document: the highest grade a reply gave together with lines, and the
        # lines of all replies, the latest first, within the limits of one card. A later reply
        # does not take back what an earlier one found (as read_document keeps earlier excerpts).
        if found:
            card.match = "strong" if any(g == "strong" for g, _ in found) else "weak"
            total = 0
            for _, rows in reversed(found):
                for a, b, label in rows:
                    if any(a <= y and x <= b for x, y, _ in card.excerpts):
                        continue
                    b = min(b, a + self.args.excerpt_lines - total - 1)
                    if b < a or len(card.excerpts) >= self.args.excerpt_rows:
                        break
                    card.excerpts.append((a, b, label)); total += b - a + 1
            card.excerpts.sort()
            card.relevant = True
        self.log({"op": "reader_doc", "qid": qid, "docid": docid, "n_lines": len(lines), "from_line": start,
                  "actions": card.actions, "match": "failed" if card.failed else card.match,
                  "lines_seen": sum(b - a + 1 for a, b in seen), "tokens_cache_aware": tokens,
                  # characters every first prompt of this trial shares: skill, question, state table
                  "shared_prefix_chars": len(self.reader_skill) + user.index("# Request"),
                  "first_prompt_chars": len(self.reader_skill) + len(user)})
        return card

    def valid_rows(self, reply: str, seen: list[tuple[int, int]]) -> list[tuple[int, int, str]]:
        """The LINES rows of one reply. Only lines the reader was shown can be passed on: a
        range is cut where they end. The limits of one reply are the reader's, enforced."""
        shown = {i for a, b in seen for i in range(a, b + 1)}
        rows, total = [], 0
        for a, b, label in re.findall(r"LINES:\s*(\d+)\s*(?:-\s*(\d+))?\s*\|?\s*(.*)", reply):
            a = int(a); b = int(b) if b else a
            a, b = min(a, b), max(a, b)
            while a <= b and a not in shown:
                a += 1
            if a > b:
                continue
            e = a
            while e + 1 <= b and e + 1 in shown:
                e += 1
            b = min(e, a + self.args.excerpt_lines - total - 1)
            if b < a or len(rows) >= self.args.excerpt_rows:
                break
            rows.append((a, b, label.strip()[:160])); total += b - a + 1
        return rows

    def render_card(self, n: int | None, card: Card, show_text: bool, earlier: bool = False) -> str:
        text = super().render_card(n, card, show_text, earlier)
        seen = getattr(card, "seen", None)
        if seen and len(seen) > 1:          # say which parts were read, not only the first and last line
            text = text.replace(f"lines {card.read_from}-{card.read_to} of {card.n_lines}",
                                f"lines {ranges_text(seen)} of {card.n_lines}", 1)
        return text

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
            if old is None:
                t.cards[docid] = card
            elif card.relevant:                         # keep what earlier readings found
                done = {(a, b) for a, b, _ in card.excerpts}
                t.cards[docid] = Card2(**{**card.__dict__,
                                          "excerpts": card.excerpts + [e for e in old.excerpts if (e[0], e[1]) not in done],
                                          "seen": merge(card.seen + list(getattr(old, "seen", []))),
                                          "read_from": min(old.read_from, card.read_from),
                                          "read_to": max(old.read_to, card.read_to)})
        self.log({"op": "read", "qid": qid, "docid": docid, "found": True, "from_line": from_line,
                  "match": card.match, "what": what, "actions": card.actions})
        return self.render_card(None, card, True)
