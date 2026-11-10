"""Reader-verifier-compactor harness: the reader-verifier agent plus a callable compactor.

Design: claude_docs/09_reader_verifier_harness.md, section 10. Version 2 of the harness whose
version 1 is tools/dar_terminus2_multiagent.py (reader + verifier).

The compactor is a third sub-agent the solver calls through the same top-level field:

    {"analysis": "...", "plan": "...", "commands": [], "subagent": {"name": "compactor"}}

It does not summarise. In its own context it selects (a) line ranges of the rule file, named
by the lines of an outline (sections and top-level subsections) that the harness puts in its
packet, and (b) numbers of solver steps; the harness widens each range to outline boundaries
and then rebuilds the solver's context as

    first message (template + skill + task, untouched)
    the response that led to the compaction
    one STATE PAGE: answer file, latest verifier report, the selected rule lines and the
    selected commands with their output (all copied verbatim by the harness)

so nothing in the rebuilt context was rewritten by a model except the one-line label and
reason of each range. (Free-text notes by the compactor exist behind compactor_notes_chars,
0 by default: in the replays the model put the verifier's claims and values into them.)
Files in the container are untouched, verifier rounds and reader calls
keep their counts, and the removed history is saved to a file in the container that the page
names.

When it runs:
- the solver calls it (any time; refused below a minimum context size);
- after a verifier report with a NO and rounds left, the decision instruction recommends it
  (compact_after_report=hint) or the harness runs it at once (=auto);
- above compactor_hint_tokens the harness says so once; above compactor_force_tokens it runs
  the compactor itself after a plain command step.

Everything else (reader, verifier, completion gate, protocol correction, logs) is inherited.
Extra logs under <trial>/agent/compactor/: call-N.json, call-N.packet.txt, call-N.page.txt,
call-N.history.txt (what was removed), call-N.context-before.json (the message list).

Run with:
  AGENT=t2rvc tools/run_dar_qwen35_35b.sh direct
"""

import asyncio
import json
import re
import shlex
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harbor.agents.terminus_2.terminus_2 import Command
from harbor.agents.terminus_2.tmux_session import TmuxSession
from harbor.llms.chat import Chat
from harbor.models.trajectories import (
    Observation,
    ObservationResult,
    Step,
    SubagentTrajectoryRef,
)

from dar_terminus2_multiagent import (
    COMPACTOR_HEADER,
    CONTEXT_NOTE_HEADER,
    SUBAGENT_MARKS,
    TOOLS_DIR,
    Terminus2ReaderVerifier,
    _sha,
)

COMPACTOR_SKILL_PATH = TOOLS_DIR / "skills" / "compactor_v1" / "SKILL.md"
SOLVER_V3_SKILL_PATH = TOOLS_DIR / "skills" / "solver_v3" / "SKILL.md"
COMPACTOR_TEMPLATE_PATH = TOOLS_DIR / "templates" / "terminus-json-multiagent-v3.txt"
CONTAINER_COMPACTOR_DIR = "/tmp/compactor"

COMPACTOR_CALL_RE = re.compile(r"=== COMPACTOR RESULT \(call (\d+) of this task\) ===")
REPORT_NO_RE = re.compile(r"^\d+\. [^\n:]+: NO\.", re.MULTILINE)

SUBAGENT_FORM_V3 = (
    '"subagent" is a top-level field of the JSON response, next to "commands"; it is never a '
    'keystroke and never a field inside a command object. Correct form: {"analysis": "...", '
    '"plan": "...", "commands": [], "subagent": {"name": "reader", "query": "..."}}; the '
    'verifier is "subagent": {"name": "verifier"} and the compactor "subagent": {"name": '
    '"compactor"}, both without a query.'
)

COMPACTOR_CLOSING = (
    "You have used all command steps. Reply now with exactly one JSON object whose "
    '"result" field is filled in: "rules" (each with "lines", "label", "why") and "steps"; '
    '"commands": [].'
)

# Lines of the rule file that start a section or a top-level subsection (column 0). The
# compactor names ranges by these lines and the harness widens a range to them.
OUTLINE_PATTERN = r"^(§|\([a-zA-Z0-9]+\) )"
OUTLINE_MAX_LINES = 300
# Outline headings that open a section (the rest are its subsections).
SECTION_PATTERN = r"^§"

# Views of the history given to the compactor inline (the full text is in the container).
STEP_VIEW_CHARS = 6000
HISTORY_VIEW_CHARS = 70000


# ------------------------------------------------------------------ pure helpers


def terminal_part(obs: str) -> str:
    """The terminal output of an observation, without any block the harness appended."""
    cut = len(obs)
    for mark in SUBAGENT_MARKS:
        pos = obs.find(mark)
        if pos != -1:
            cut = min(cut, pos)
    return obs[:cut].rstrip()


def report_has_no(report_text: str | None) -> bool:
    """True if a rendered verifier report has at least one rubric with status NO."""
    return bool(report_text and REPORT_NO_RE.search(report_text))


def strip_rule_text(text: str, rule_texts: list[tuple[str, str]]) -> str:
    """Replace verbatim rule-file excerpts (text, where) by a one-line placeholder."""
    for excerpt, where in rule_texts:
        if excerpt and len(excerpt) > 80:
            text = text.replace(
                excerpt, f"[rule text of {where} omitted in this view]"
            )
    return text


def clip_middle(text: str, limit: int, note: str = "") -> str:
    if len(text) <= limit:
        return text
    head = text[: limit * 2 // 3]
    tail = text[-(limit // 3) :]
    return (
        f"{head}\n[... {len(text) - len(head) - len(tail):,} characters omitted in this "
        f"view{note} ...]\n{tail}"
    )


def parse_keep_steps(raw: Any, valid: set[int], max_steps: int) -> list[dict[str, Any]]:
    """Normalise the compactor's "steps" field to [{"step": n, "why": str}], valid ones only."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raw = [raw]
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in raw:
        why = ""
        if isinstance(item, dict):
            n = item.get("step", item.get("n", item.get("number")))
            why = str(item.get("why") or item.get("what") or "").strip()
        else:
            n = item
        try:
            n = int(str(n).strip().lower().removeprefix("step").strip())
        except (TypeError, ValueError):
            continue
        if n in valid and n not in seen:
            seen.add(n)
            out.append({"step": n, "why": why})
        if len(out) >= max_steps:
            break
    return sorted(out, key=lambda d: d["step"])


def parse_notes(raw: Any, max_chars: int) -> dict[str, list[str]]:
    """Normalise the compactor's "notes" to {"set_aside": [...], "open": [...]} within a cap."""
    notes: dict[str, list[str]] = {"set_aside": [], "open": []}
    if isinstance(raw, str):
        raw = {"open": [raw]}
    if isinstance(raw, list):
        raw = {"open": raw}
    if not isinstance(raw, dict):
        return notes
    alias = {
        "set_aside": ("set_aside", "ruled_out", "discarded", "tried"),
        "open": ("open", "next", "todo", "pending"),
    }
    remaining = max_chars
    for key, names in alias.items():
        for name in names:
            val = raw.get(name)
            if val is None:
                continue
            if isinstance(val, str):
                val = [val]
            if not isinstance(val, list):
                continue
            for item in val:
                item = " ".join(str(item).split())
                if not item or remaining <= 0:
                    continue
                if len(item) > remaining:
                    item = item[:remaining].rstrip() + " [cut]"
                remaining -= len(item)
                notes[key].append(item)
    return notes


def parse_outline(grep_output: str) -> list[tuple[int, str]]:
    """(line number, heading) pairs from ``grep -n`` output, in file order."""
    out: list[tuple[int, str]] = []
    for line in grep_output.splitlines():
        num, sep, text = line.partition(":")
        if sep and num.strip().isdigit():
            out.append((int(num), text.strip()))
    return out


def snap_ranges(
    raw: Any, starts: list[int], n_lines: int | None, default_file: str
) -> tuple[Any, list[dict[str, str]]]:
    """Widen each selected range of the rule file to outline boundaries: back to the nearest
    outline line at or before its first line, forward to the line before the next outline
    line after its last line. A provision is then never cut at either end. Returns the new
    list and a log of the ranges that changed."""
    if not isinstance(raw, list) or not starts:
        return raw, []
    starts = sorted(starts)
    out, changed = [], []
    for ex in raw:
        m = (
            re.fullmatch(
                r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", str(ex.get("lines") or ex.get("range") or "")
            )
            if isinstance(ex, dict)
            else None
        )
        if not m or str(ex.get("file") or default_file).strip() != default_file:
            out.append(ex)
            continue
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if b < a or (n_lines is not None and a > n_lines):
            out.append(ex)  # reported as invalid by the extraction step
            continue
        new_a = max((x for x in starts if x <= a), default=a)
        after = [x for x in starts if x > b]
        new_b = after[0] - 1 if after else (n_lines if n_lines is not None else b)
        new = f"{new_a}-{new_b}"
        if (new_a, new_b) != (a, b):
            changed.append({"selected": f"{a}-{b}", "inserted": new})
        ex = dict(ex)
        ex.pop("range", None)
        ex["lines"] = new
        out.append(ex)
    return out, changed


def _span(ex: Any) -> tuple[int, int] | None:
    m = (
        re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", str(ex.get("lines") or ""))
        if isinstance(ex, dict)
        else None
    )
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2) or m.group(1))
    return (a, b) if a <= b else None


def section_spans(
    outline: list[tuple[int, str]], n_lines: int | None, section_pattern: str
) -> list[tuple[str, int, int]]:
    """(token, first line, last line) of every top-level section of the outline; the token
    is the first word of its heading without trailing punctuation, e.g. "§63"."""
    tops = [(n, text) for n, text in outline if re.search(section_pattern, text)]
    spans = []
    for i, (n, text) in enumerate(tops):
        token = text.split()[0].rstrip(".,:;") if text.split() else ""
        end = tops[i + 1][0] - 1 if i + 1 < len(tops) else (n_lines or n)
        if token:
            spans.append((token, n, end))
    return spans


def restore_cited(
    selected: Any,
    answer: str,
    spans: list[tuple[str, int, int]],
    in_context: list[dict[str, Any]],
    default_file: str,
) -> tuple[Any, list[dict[str, str]]]:
    """Coverage guard. For every section whose token appears in the answer file and from
    which the selection holds no line, append the ranges of that section the solver has in
    its context now (clipped to the section). Nothing is added for a section the solver
    never received. Returns the new selection and a log."""
    if not isinstance(selected, list):
        selected = []
    have = [
        sp
        for ex in selected
        if str((ex or {}).get("file") or default_file).strip() == default_file
        for sp in [_span(ex)]
        if sp
    ]
    out, log = list(selected), []
    for token, first, last in spans:
        if not re.search(r"(?<![\w§])" + re.escape(token) + r"(?!\w)", answer):
            continue
        if any(a <= last and b >= first for a, b in have):
            continue
        added = []
        for r in in_context:
            sp = _span(r)
            if (
                sp is None
                or str(r.get("file") or default_file).strip() != default_file
                or sp[0] > last
                or sp[1] < first
            ):
                continue
            a, b = max(sp[0], first), min(sp[1], last)
            if any(x <= a and b <= y for x, y in added):
                continue
            added.append((a, b))
        for a, b in added:
            out.append(
                {
                    "lines": f"{a}-{b}",
                    "label": f"{token} (as you had it)",
                    "why": f"restored by the harness: the answer file cites {token} and "
                    "the compactor's selection had no text from it",
                }
            )
        log.append({"section": token, "restored": [f"{a}-{b}" for a, b in added]})
    return out, log


def dedupe_ranges(raw: Any, default_file: str) -> Any:
    """Drop a selected range that lies inside an earlier selected range of the same file
    (the reader's list often names a provision twice). Anything not parseable is passed on
    for the extraction step to report."""
    if not isinstance(raw, list):
        return raw
    seen: list[tuple[str, int, int]] = []
    out = []
    for ex in raw:
        if isinstance(ex, dict):
            m = re.fullmatch(
                r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", str(ex.get("lines") or ex.get("range") or "")
            )
            if m:
                file = str(ex.get("file") or default_file).strip()
                a, b = int(m.group(1)), int(m.group(2) or m.group(1))
                if any(f == file and a >= x and b <= y for f, x, y in seen):
                    continue
                seen.append((file, a, b))
        out.append(ex)
    return out


def rebuild_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """First message and last response of a chat history, or None if there is nothing between
    them or the list is not shaped as expected (user first, assistant last)."""
    if (
        len(messages) < 4
        or messages[0].get("role") != "user"
        or messages[-1].get("role") != "assistant"
    ):
        return None
    return [messages[0], messages[-1]]


def render_state_page(
    *,
    call_no: int,
    history_path: str,
    state_lines: list[str],
    answer_path: str,
    answer: str,
    report_text: str | None,
    rule_path: str,
    excerpts: list[dict[str, Any]],
    kept_steps: list[dict[str, Any]],
    step_output: str,
    notes: dict[str, list[str]],
    footer: str,
) -> str:
    """Assemble the page that replaces the solver's history. Everything except the notes is
    text the harness copied; the function only lays it out."""
    lines = [
        f"{COMPACTOR_HEADER} (call {call_no} of this task) ===",
        "Your context was rebuilt at this point: the messages between the task description "
        "and this one were removed and this page replaces them. Nothing in the container "
        "changed; the answer file, the files you wrote and the rule file are as you left "
        f"them. The removed history is saved at {history_path} (steps headed "
        "'--- step N ---'); use grep -n / sed -n on it only for a detail missing below.",
        "",
        "--- STATE ---",
        *state_lines,
        "",
        f"--- ANSWER FILE ({answer_path} as it stands) ---",
        answer.strip() or "(no answer file)",
        "",
        "--- LATEST VERIFIER REPORT (verbatim) ---",
        (report_text or "").strip() or "No verifier report yet.",
        "",
        f"--- RULE TEXT KEPT (verbatim lines of {rule_path}, copied by the harness) ---",
    ]
    inserted = [e for e in excerpts if e.get("inserted")]
    for e in inserted:
        title = f"[{e['index']}] lines {e['lines']} of {e['file']}"
        if e.get("label"):
            title += f" · {e['label']}"
        lines.append(title)
        if e.get("why"):
            lines.append(f"    why: {e['why']}")
        lines.append(e["text"])
        lines.append("")
    if not inserted:
        lines.append("None.")
        lines.append("")
    skipped = [
        e for e in excerpts if not e.get("inserted") and "budget" in e.get("reason", "")
    ]
    if skipped:
        lines.append(
            "Selected but not inserted (character budget exhausted; ask the reader):"
        )
        for e in skipped:
            lines.append(
                f"- lines {e['lines']} of {e['file']}"
                + (f" · {e['label']}" if e.get("label") else "")
            )
        lines.append("")
    lines.append(
        "--- COMMANDS KEPT (terminal output of earlier steps, verbatim from your history) ---"
    )
    if kept_steps:
        for s in kept_steps:
            lines.append(f"[step {s['step']}]" + (f" {s['why']}" if s.get("why") else ""))
            if not s["output"]:
                # The terminal output echoes the commands; list them only if it is empty.
                lines.extend(f"$ {c}" for c in s["cmds"])
            lines.append(s["output"] or "(no output)")
            lines.append("")
    else:
        lines.append("None.")
        lines.append("")
    if step_output.strip():
        lines.append("--- OUTPUT OF THE COMMANDS YOU JUST RAN ---")
        lines.append(step_output.rstrip())
        lines.append("")
    if notes.get("set_aside") or notes.get("open"):
        lines.append(
            "--- NOTES (written by the compactor from your history; not checked) ---"
        )
        if notes.get("set_aside"):
            lines.append("Tried and set aside:")
            lines.extend(f"- {n}" for n in notes["set_aside"])
        if notes.get("open"):
            lines.append("Open:")
            lines.extend(f"- {n}" for n in notes["open"])
        lines.append("")
    lines.append("--- NEXT ---")
    lines.append(footer)
    return "\n".join(lines)


# ------------------------------------------------------------------------ agent


class Terminus2ReaderVerifierCompactor(Terminus2ReaderVerifier):
    """Reader-verifier agent whose solver can also have its context rebuilt by a compactor."""

    SUBAGENT_NAMES = ("reader", "verifier", "compactor")
    SUBAGENT_HELP = '"reader" (with a "query"), "verifier" and "compactor" (no arguments)'
    SUBAGENT_FORM = SUBAGENT_FORM_V3

    def __init__(
        self,
        *args,
        compactor_skill_path: str | None = None,
        compactor_max_steps: int | str = 1,
        compactor_max_chars: int | str = 14000,
        compactor_keep_steps: int | str = 4,
        compactor_step_chars: int | str = 2500,
        compactor_notes_chars: int | str = 0,
        compactor_timeout_sec: int | str = 180,
        compactor_thinking: bool | str = True,
        outline_pattern: str = OUTLINE_PATTERN,
        section_pattern: str = SECTION_PATTERN,
        compactor_fallback: bool | str = True,
        compact_after_report: str = "hint",
        compactor_min_tokens: int | str = 8000,
        compactor_hint_tokens: int | str = 16000,
        compactor_force_tokens: int | str = 30000,
        max_compactions: int | str = 3,
        solver_skill_path: str | None = None,
        derivation_template: str | None = None,
        **kwargs,
    ):
        super().__init__(
            *args,
            solver_skill_path=solver_skill_path or str(SOLVER_V3_SKILL_PATH),
            derivation_template=derivation_template or str(COMPACTOR_TEMPLATE_PATH),
            **kwargs,
        )
        if compact_after_report not in ("off", "hint", "auto"):
            raise ValueError(
                f"compact_after_report must be off, hint or auto, got {compact_after_report!r}"
            )
        self._compact_after_report = compact_after_report
        self._compactor_skill_path = (
            Path(compactor_skill_path) if compactor_skill_path else COMPACTOR_SKILL_PATH
        )
        self._compactor_skill_text = self._compactor_skill_path.read_text()
        self._compactor_max_steps = int(compactor_max_steps)
        self._compactor_max_chars = int(compactor_max_chars)
        self._compactor_keep_steps = int(compactor_keep_steps)
        self._compactor_step_chars = int(compactor_step_chars)
        self._compactor_notes_chars = int(compactor_notes_chars)
        self._outline_pattern = outline_pattern
        self._section_pattern = section_pattern
        # After a timed-out call with thinking, try once more without it (a short reply).
        self._compactor_fallback = str(compactor_fallback).lower() not in (
            "false",
            "0",
            "no",
            "off",
        )
        # Wall-clock limit of one compactor call; when it is hit the context is left as it is.
        self._compactor_timeout_sec = float(compactor_timeout_sec)
        # False asks the server's chat template for a reply without a thinking block
        # (vLLM: chat_template_kwargs.enable_thinking); the solver, the reader and the
        # verifier are not affected.
        self._compactor_thinking = str(compactor_thinking).lower() not in (
            "false",
            "0",
            "no",
            "off",
        )
        self._compactor_min_tokens = int(compactor_min_tokens)
        self._compactor_hint_tokens = int(compactor_hint_tokens)
        self._compactor_force_tokens = int(compactor_force_tokens)
        self._max_compactions = int(max_compactions)

        self._solver_chat: Chat | None = None
        self._last_prompt_tokens = 0  # prompt size of the solver's latest request
        self._compactor_calls: list[dict[str, Any]] = []  # every attempt, with outcome
        self._compactor_skips: list[dict[str, Any]] = []
        self._compactions = 0  # attempts that rebuilt the context
        self._context_start_step = 1  # first solver step still in the solver's context
        self._kept_entries: list[dict[str, Any]] = []  # rule ranges on the latest page
        self._reader_floor = 0  # index into _reader_returned at the latest compaction
        self._hinted = False  # context note shown since the latest compaction
        self._suppress_hint = False
        self._pending_compaction_record: dict[str, Any] | None = None
        self._page_built = False  # a state page was produced in the current step
        self._page_head = ""
        self._outline: list[tuple[int, str]] | None = None
        self._decision_after_compaction = False

    @staticmethod
    def name() -> str:
        return "terminus-2-reader-verifier-compactor"

    # ------------------------------------------------------------------ hooks

    async def _handle_llm_interaction(
        self,
        chat: Chat,
        prompt: str,
        logging_paths,
        original_instruction: str = "",
        session: TmuxSession | None = None,
    ):
        self._solver_chat = chat
        before = chat.total_input_tokens
        n_decisions = len(self._decisions)
        out = await super()._handle_llm_interaction(
            chat, prompt, logging_paths, original_instruction, session
        )
        delta = chat.total_input_tokens - before
        if delta > 0:
            self._last_prompt_tokens = delta
            rec = self._pending_compaction_record
            if rec is not None:
                # First solver request after a compaction: the size of the rebuilt context.
                rec["solver_prompt_tokens_after"] = delta
                self._write_compactor_record(rec)
                self._pending_compaction_record = None
        if len(self._decisions) > n_decisions and self._decision_after_compaction:
            self._decisions[-1]["after_compaction"] = True
            self._decision_after_compaction = False
        return out

    async def _execute_commands(
        self, commands: list[Command], session: TmuxSession
    ) -> tuple[bool, str]:
        self._page_built = False
        timeout_occurred, output = await super()._execute_commands(commands, session)
        if self._page_built:
            # The page carries the output of this step's commands (if any) in its own
            # section; the raw terminal text in front of it would be the only thing in the
            # rebuilt context outside the page.
            pos = output.find(self._page_head)
            if pos > 0:
                output = output[pos:]
        return timeout_occurred, output

    async def _dispatch_subagent(
        self,
        session: TmuxSession,
        sub: dict[str, Any],
        commands: list[Command],
        terminal_output: str,
    ) -> str:
        if sub["name"] == "compactor":
            report_pending = self._awaiting_decision or (
                bool(self._decisions)
                and self._decisions[-1].get("subagent") == "compactor"
                and self._decisions[-1].get("episode_step_id")
                == len(self._trajectory_steps) + 1
            )
            block = await self._compactor_call(
                session, commands, terminal_output, trigger="solver"
            )
            if report_pending and self._previous_report_text:
                # The solver compacted in response to a report; log what it does next too.
                self._awaiting_decision = True
                self._decision_after_compaction = True
            return block or ""
        block = await super()._dispatch_subagent(
            session, sub, commands, terminal_output
        )
        if sub["name"] == "verifier":
            rounds_left = self._max_verify_rounds - self._verify_round
            if (
                self._compact_after_report == "auto"
                and rounds_left > 0
                and self._verified_hash == _sha(self._previous_answer or "")
                and report_has_no(self._previous_report_text)
            ):
                page = await self._compactor_call(
                    session, commands, terminal_output, trigger="auto_after_report"
                )
                if page:
                    self._decision_after_compaction = True
                    return page
            return block
        # Reader result: the context grew by the block.
        note = self._context_note(extra_chars=len(block))
        return f"{block}\n\n{note}" if note else block

    async def _after_commands(
        self, session: TmuxSession, commands: list[Command], terminal_output: str
    ) -> str | None:
        est = self._estimated_tokens(len(terminal_output))
        if est >= self._compactor_force_tokens:
            page = await self._compactor_call(
                session, commands, terminal_output, trigger="auto_threshold"
            )
            if page:
                return page
        return self._context_note(extra_chars=len(terminal_output))

    def _decision_instruction(self, rounds_left: int, verified: bool = True) -> str:
        text = super()._decision_instruction(rounds_left, verified)
        if (
            self._suppress_hint
            or self._compact_after_report != "hint"
            or rounds_left <= 0
            or not verified
            or not report_has_no(self._previous_report_text)
            or self._skip_reason() is not None
        ):
            return text
        return (
            f"{text}\n"
            f"Recommended before you work on a NO: call the compactor (\"subagent\": "
            f'{{"name": "compactor"}}, empty "commands"). Your context holds about '
            f"{self._estimated_tokens():,} tokens of rule text and earlier steps; the "
            "compactor rebuilds it as the task plus one page with the answer file, this "
            "report, the rule text that the answer file cites and the report names "
            "(verbatim) and the commands whose results you used. It changes no file and "
            "uses no verification round."
        )

    # ------------------------------------------------------------ context size

    def _estimated_tokens(self, extra_chars: int = 0) -> int:
        """Size of the solver's next prompt: its latest prompt plus text added since (4
        characters per token; a readout for hints and thresholds, not an exact count)."""
        return self._last_prompt_tokens + extra_chars // 4

    def _skip_reason(self, extra_chars: int = 0) -> str | None:
        if self._solver_chat is None:
            return "the solver's context is not available"
        if self._compactions >= self._max_compactions:
            return (
                f"the limit of {self._max_compactions} compactions per task is reached."
            )
        est = self._estimated_tokens(extra_chars)
        if est < self._compactor_min_tokens:
            return (
                f"your context holds about {est:,} tokens, below the "
                f"{self._compactor_min_tokens:,} at which a rebuild pays."
            )
        return None

    def _context_note(self, extra_chars: int = 0) -> str | None:
        """One-time note (per compaction epoch) that the context has grown large."""
        est = self._estimated_tokens(extra_chars)
        if (
            self._hinted
            or est < self._compactor_hint_tokens
            or self._compactions >= self._max_compactions
        ):
            return None
        self._hinted = True
        return (
            f"{CONTEXT_NOTE_HEADER}\n"
            f"Your context holds about {est:,} tokens. If part of it is rule text or output "
            'you no longer use, "subagent": {"name": "compactor"} rebuilds it as the task '
            "plus one page with your answer file, the latest verifier report, the rule text "
            "you rely on (verbatim) and the commands whose results you used. Not before your "
            "answer file exists, and not in the response that calls the verifier."
        )

    # ---------------------------------------------------------------- reader

    def _context_rule_lines(self) -> list[str]:
        """Rule ranges currently in the solver's context, one line per source."""
        lines: list[str] = []
        if self._compactions:
            kept = "; ".join(
                f"lines {r['lines']}" + (f" ({r['label']})" if r.get("label") else "")
                for r in self._kept_entries
            )
            lines.append(
                f"kept at compaction {self._compactions}: {kept or 'nothing'} (the "
                "solver's context was rebuilt there; rule text returned before it and not "
                "listed here is no longer in the solver's context)"
            )
        lines.extend(self._reader_earlier_lines(since=self._reader_floor))
        return lines

    def _build_reader_packet(self, query: str, call_no: int) -> str:
        if not self._compactions:
            return super()._build_reader_packet(query, call_no)
        packet = super()._build_reader_packet(query, call_no)
        head, sep, rest = packet.partition("=== RETURNED EARLIER IN THIS TASK ===\n")
        _, sep2, tail = rest.partition("\n\n=== BUDGET ===")
        if not sep or not sep2:
            return packet
        return (
            head
            + "=== IN THE SOLVER'S CONTEXT NOW ===\n"
            + "\n".join(self._context_rule_lines())
            + "\n\n=== BUDGET ==="
            + tail
        )

    # ------------------------------------------------------------- compactor

    def _solver_steps(
        self, commands: list[Command], terminal_output: str
    ) -> list[dict[str, Any]]:
        """Every solver step as {n, message, cmds, obs}; the step being executed comes last."""
        steps: list[dict[str, Any]] = []
        n = 0
        for step in self._trajectory_steps:
            if step.source != "agent":
                continue
            n += 1
            cmds = [
                tc.arguments.get("keystrokes", "").rstrip()
                for tc in (step.tool_calls or [])
                if tc.function_name == "bash_command" and isinstance(tc.arguments, dict)
            ]
            obs = ""
            if step.observation and step.observation.results:
                obs = "\n".join(
                    r.content for r in step.observation.results if r.content
                )
            steps.append(
                {"n": n, "message": step.message or "", "cmds": cmds, "obs": obs}
            )
        n += 1
        parts = []
        if self._current_analysis:
            parts.append(f"Analysis: {self._current_analysis}")
        if self._current_plan:
            parts.append(f"Plan: {self._current_plan}")
        steps.append(
            {
                "n": n,
                "message": "\n".join(parts),
                "cmds": [c.keystrokes.rstrip() for c in commands if c.keystrokes.strip()],
                "obs": terminal_output,
                "current": True,
            }
        )
        return steps

    @staticmethod
    def _format_step(step: dict[str, Any], obs: str | None = None) -> str:
        lines = [f"--- step {step['n']} ---"]
        if step["message"]:
            lines.append(step["message"])
        if step["cmds"]:
            lines.append("[commands]")
            lines.extend(f"$ {c}" for c in step["cmds"])
        obs = step["obs"] if obs is None else obs
        if obs.strip():
            lines.append("[observation]")
            lines.append(obs.rstrip())
        if step.get("current"):
            lines.append("[the context is rebuilt after this step]")
        return "\n".join(lines)

    def _rule_texts(self) -> list[tuple[str, str]]:
        """(text, where) for every rule-file excerpt the harness inserted so far."""
        texts: list[tuple[str, str]] = []
        for r in self._reader_calls:
            for e in r.get("excerpts", []):
                if e.get("inserted") and e.get("text"):
                    texts.append(
                        (e["text"], f"lines {e['lines']} (reader call {r['call']})")
                    )
        for r in self._compactor_calls:
            for e in r.get("excerpts", []):
                if e.get("inserted") and e.get("text"):
                    texts.append(
                        (e["text"], f"lines {e['lines']} (kept at compaction)")
                    )
        # Longest first, so that an excerpt containing another is replaced as a whole.
        return sorted(texts, key=lambda t: -len(t[0]))

    def _history_view(
        self, window: list[dict[str, Any]], history_path: str
    ) -> str:
        """The solver's history for the compactor's packet: rule text replaced by
        placeholders, long outputs clipped, oldest observations dropped above a total cap."""
        rule_texts = self._rule_texts()
        note = f"; full text in {history_path}"
        views = [
            clip_middle(strip_rule_text(s["obs"], rule_texts), STEP_VIEW_CHARS, note)
            for s in window
        ]
        total = sum(len(v) for v in views)
        for i in range(len(views)):
            if total <= HISTORY_VIEW_CHARS:
                break
            total -= len(views[i])
            views[i] = f"[observation omitted in this view{note}]"
            total += len(views[i])
        return "\n".join(self._format_step(s, v) for s, v in zip(window, views))

    def _build_compactor_packet(
        self,
        *,
        call_no: int,
        answer: str,
        window: list[dict[str, Any]],
        history_path: str,
        section_map: str,
        trigger: str,
    ) -> str:
        report = (self._previous_report_text or "").strip()
        if report:
            changed = (
                "unchanged since this report"
                if self._verified_hash == _sha(answer)
                else "changed since this report"
            )
            report = f"(answer file {changed})\n{report}"
        in_context = [
            f"reader {line}" if line.startswith("call ") else line
            for line in self._context_rule_lines()
        ]
        first, last = window[0]["n"], window[-1]["n"]
        parts = [
            "=== TASK ===",
            self._original_instruction.strip(),
            "",
            f"=== ANSWER FILE (contents of {self._answer_path}) ===",
            answer,
            "",
            "=== LATEST VERIFIER REPORT ===",
            report or "none yet",
            "",
            "=== RULE TEXT IN THE SOLVER'S CONTEXT ===",
            "\n".join(in_context) if in_context else "none",
            "",
        ]
        if section_map.strip():
            parts += [
                f"=== RULE FILE OUTLINE (line:heading of every section and top-level "
                f"subsection of {self._rule_path}) ===",
                section_map.rstrip(),
                "",
            ]
        parts += [
            f"=== SOLVER HISTORY (steps {first}-{last}; rule text omitted in this view) ===",
            f"The same steps with every output and sub-agent result in full are at "
            f"{history_path}.",
            self._history_view(window, history_path),
            "",
            f"=== COMPACTION {call_no} of this task"
            + {
                "solver": " (called by the solver)",
                "auto_after_report": " (run by the harness after the verifier report)",
                "auto_threshold": " (run by the harness: the context is large)",
            }.get(trigger, "")
            + " ===",
            "=== BUDGET ===",
            f"{self._compactor_max_steps} command step(s), {self._compactor_max_chars:,} "
            f"characters of rule text in total, at most {self._compactor_keep_steps} steps "
            f"(each kept up to {self._compactor_step_chars:,} characters of output)"
            + (
                f", {self._compactor_notes_chars:,} characters of notes"
                if self._compactor_notes_chars
                else ""
            )
            + f'. Ranges without a "file" refer to {self._rule_path}.',
        ]
        return "\n".join(parts)

    def _page_state_lines(self, answer: str) -> list[str]:
        if answer.strip() in ("", "(no answer file)"):
            file_state = "not written yet"
        elif self._verified_hash is None:
            file_state = "not yet checked by the verifier"
        elif self._verified_hash == _sha(answer):
            file_state = "unchanged since the verifier checked it"
        else:
            file_state = "changed since the last verifier report"
        return [
            f"Verification rounds used: {self._verify_round} of {self._max_verify_rounds}. "
            f"Reader calls so far: {len(self._reader_calls)}. Compactions: "
            f"{self._compactions + 1} of {self._max_compactions}.",
            f"Answer file: {file_state}.",
        ]

    def _page_footer(self, answer: str) -> str:
        where = (
            "Rule text for what you do next: look first under RULE TEXT KEPT above; ask the "
            "reader only for a provision that is not on this page."
        )
        if self._verify_round == 0 or not self._previous_report_text:
            return (
                "Continue the task from this state. " + where + " Have the verifier check "
                "the answer file before you finish."
            )
        rounds_left = self._max_verify_rounds - self._verify_round
        verified = self._verified_hash == _sha(answer)
        self._suppress_hint = True
        try:
            decision = self._decision_instruction(rounds_left, verified=verified)
        finally:
            self._suppress_hint = False
        if rounds_left > 0 and not verified and self._verified_hash is not None:
            # The file changed after the report: the parent's "no report" wording does not fit.
            decision = (
                f"{rounds_left} verification round(s) remain. The answer file changed after "
                "the report above, so call the verifier again before finishing."
            )
        return f"{decision}\n{where}"

    def _compactor_llm_kwargs(self, thinking: bool) -> dict | None:
        if thinking:
            return None
        kw = dict(self._llm_call_kwargs)
        body = dict(kw.get("extra_body") or {})
        body["chat_template_kwargs"] = {
            **(body.get("chat_template_kwargs") or {}),
            "enable_thinking": False,
        }
        kw["extra_body"] = body
        return kw

    async def _rule_outline(self, environment) -> list[tuple[int, str]]:
        """Section and top-level subsection headings of the rule file with line numbers
        (empty if the file has no line matching the outline pattern)."""
        if self._outline is None:
            try:
                res = await environment.exec(
                    f"grep -n -E {shlex.quote(self._outline_pattern)} "
                    f"{shlex.quote(self._rule_path)} | head -{OUTLINE_MAX_LINES + 1}",
                    timeout_sec=20,
                )
                outline = parse_outline(res.stdout or "")
            except Exception as exc:
                self.logger.error(f"compactor: outline failed: {exc!r}")
                outline = []
            # An outline cut off by the line cap would make the last range run to the end of
            # the file; do without one then.
            self._outline = outline if len(outline) <= OUTLINE_MAX_LINES else []
        return self._outline

    async def _compactor_call(
        self,
        session: TmuxSession,
        commands: list[Command],
        terminal_output: str,
        trigger: str,
    ) -> str | None:
        """Run the compactor and rebuild the solver's context. Returns the state page, a
        short "not run" block for a solver call that was refused or failed, or None for a
        harness-triggered attempt that did not rebuild anything."""
        environment = session.environment
        by_solver = trigger == "solver"

        def refused(reason: str, **extra: Any) -> str | None:
            self._compactor_skips.append(
                {
                    "episode_step_id": len(self._trajectory_steps) + 1,
                    "trigger": trigger,
                    "reason": reason,
                    **extra,
                }
            )
            if not by_solver:
                return None
            return (
                f"{COMPACTOR_HEADER} ===\nNot run: {reason} Your context is unchanged; "
                "continue from where you are."
            )

        skip = self._skip_reason(len(terminal_output))
        if skip is not None:
            return refused(skip)
        chat = self._solver_chat
        assert chat is not None
        new_messages = rebuild_messages(chat.messages)
        if new_messages is None:
            return refused("there is no history to remove yet.")

        call_no = len(self._compactor_calls) + 1
        started = time.time()
        tokens_before = self._estimated_tokens(len(terminal_output))
        answer = await self._read_answer(environment)
        steps = self._solver_steps(commands, terminal_output)
        window = [s for s in steps if s["n"] >= self._context_start_step]
        current = steps[-1]

        out_dir = self.logs_dir / "compactor"
        out_dir.mkdir(parents=True, exist_ok=True)
        history_path = f"{CONTAINER_COMPACTOR_DIR}/history-{call_no}.txt"
        history_local = out_dir / f"call-{call_no}.history.txt"
        history_local.write_text("\n".join(self._format_step(s) for s in window) + "\n")
        try:
            await environment.exec(
                f"mkdir -p {shlex.quote(CONTAINER_COMPACTOR_DIR)}", timeout_sec=10
            )
            await environment.upload_file(history_local, history_path)
        except Exception as exc:
            self.logger.error(f"compactor: history upload failed: {exc!r}")
            return refused("the history could not be saved to the container.")
        outline = await self._rule_outline(environment)
        n_rule_lines = await self._line_count(environment, self._rule_path)
        section_map = "\n".join(f"{n}:{text[:90]}" for n, text in outline)
        if section_map and n_rule_lines:
            section_map += f"\n(last line of the file: {n_rule_lines})"
        packet = self._build_compactor_packet(
            call_no=call_no,
            answer=answer,
            window=window,
            history_path=history_path,
            section_map=section_map,
            trigger=trigger,
        )
        (out_dir / f"call-{call_no}.packet.txt").write_text(packet)

        record: dict[str, Any] = {
            "call": call_no,
            "trigger": trigger,
            "episode_step_id": len(self._trajectory_steps) + 1,
            "solver_step": current["n"],
            "window_first_step": window[0]["n"],
            "after_round": self._verify_round,
            "solver_prompt_tokens_before": tokens_before,
            "messages_before": len(chat.messages),
            "packet_chars": len(packet),
            "rebuilt": False,
        }
        self._compactor_calls.append(record)
        modes = [self._compactor_thinking]
        if self._compactor_thinking and self._compactor_fallback:
            modes.append(False)
        record["attempts"] = []
        try:
            for i, thinking in enumerate(modes):
                t_attempt = time.time()
                try:
                    run = await asyncio.wait_for(
                        self._run_tool_subagent(
                            environment,
                            role="compactor",
                            skill_text=self._compactor_skill_text,
                            packet=packet,
                            max_steps=self._compactor_max_steps,
                            closing=COMPACTOR_CLOSING,
                            llm_kwargs=self._compactor_llm_kwargs(thinking),
                        ),
                        timeout=self._compactor_timeout_sec,
                    )
                except (asyncio.TimeoutError, TimeoutError):
                    record["attempts"].append(
                        {
                            "thinking": thinking,
                            "outcome": "timeout",
                            "elapsed_sec": time.time() - t_attempt,
                        }
                    )
                    if i + 1 == len(modes):
                        raise
                    continue
                record["attempts"].append(
                    {
                        "thinking": thinking,
                        "outcome": "replied",
                        "elapsed_sec": time.time() - t_attempt,
                    }
                )
                break
        except Exception as exc:  # keep the trial alive; CancelledError passes
            timed_out = isinstance(exc, (asyncio.TimeoutError, TimeoutError))
            self.logger.error(f"compactor call {call_no} failed: {exc!r}")
            record.update(
                {
                    "error": repr(exc),
                    "timed_out": timed_out,
                    "elapsed_sec": time.time() - started,
                }
            )
            self._write_compactor_record(record)
            return refused(
                "the compactor did not finish within its time limit."
                if timed_out
                else f"the compactor could not complete ({type(exc).__name__}).",
                call=call_no,
            )
        result = run["result"]
        rchat = run["chat"]
        traj_name = f"trajectory.compactor-call-{call_no}.json"
        self._save_subagent_trajectory(
            run["session_id"],
            run["steps"],
            traj_name,
            rchat,
            agent_name="terminus-2-reader-verifier-compactor/compactor",
            extra={
                "parent_session_id": self._session_id,
                "compactor_call": call_no,
                "skill_path": str(self._compactor_skill_path),
            },
        )
        record.update(
            {
                "result": result,
                "steps_used": run["steps_used"],
                "parse_failures": run["parse_failures"],
                "context_exceeded": run["context_exceeded"],
                "commands": run["commands"],
                "prompt_tokens": rchat.total_input_tokens,
                "completion_tokens": rchat.total_output_tokens,
                "n_llm_calls": run["n_calls"],
                "trajectory_file": traj_name,
            }
        )

        result = result if isinstance(result, dict) else {}
        raw_rules = (
            result.get("rules") or result.get("keep_rules") or result.get("excerpts")
        )
        kept_entries: list[dict[str, Any]] = []
        raw_rules, widened = snap_ranges(
            raw_rules, [n for n, _ in outline], n_rule_lines, self._rule_path
        )
        record["widened"] = widened
        raw_rules, restored = restore_cited(
            raw_rules,
            answer,
            section_spans(outline, n_rule_lines, self._section_pattern),
            self._kept_entries + self._reader_returned[self._reader_floor :],
            self._rule_path,
        )
        record["restored"] = restored
        excerpts, extraction = await self._extract_excerpts(
            environment,
            {"excerpts": dedupe_ranges(raw_rules, self._rule_path)},
            call_no,
            max_chars=self._compactor_max_chars,
            record=kept_entries,
            budget_owner="compactor's",
        )
        record.update(
            {
                "excerpts": excerpts,
                "chars_inserted": extraction["chars"],
                "truncated": extraction["truncated"],
                "not_inserted": extraction["not_inserted"],
                "invalid": extraction["invalid"],
            }
        )
        had_rule_text = bool(self._context_rule_lines())
        if run["result"] is None or (had_rule_text and not kept_entries):
            reason = (
                "the compactor returned no result."
                if run["result"] is None
                else "the compactor selected no valid rule text, and a rebuild would have "
                "removed the rule text you hold."
            )
            record.update({"abort_reason": reason, "elapsed_sec": time.time() - started})
            self._write_compactor_record(record)
            self._append_compactor_ref(run["session_id"], traj_name, call_no, "no rebuild")
            return refused(reason, call=call_no)

        # A step whose commands only write or show the answer file adds nothing: the file is
        # on the page.
        answer_dir = str(Path(self._answer_path).parent)
        valid_steps = {
            s["n"]
            for s in window
            if s["cmds"]
            and not s.get("current")
            and not all(answer_dir in c for c in s["cmds"])
        }
        by_n = {s["n"]: s for s in window}
        kept_steps = parse_keep_steps(
            result.get("steps") or result.get("keep_steps"),
            valid_steps,
            self._compactor_keep_steps,
        )
        note = f"; full output in {history_path}"
        for k in kept_steps:
            s = by_n[k["step"]]
            k["cmds"] = s["cmds"]
            k["output"] = clip_middle(
                terminal_part(s["obs"]), self._compactor_step_chars, note
            )
        notes = parse_notes(result.get("notes"), self._compactor_notes_chars)

        page = render_state_page(
            call_no=call_no,
            history_path=history_path,
            state_lines=self._page_state_lines(answer),
            answer_path=self._answer_path,
            answer=answer,
            report_text=self._previous_report_text,
            rule_path=self._rule_path,
            excerpts=excerpts,
            kept_steps=kept_steps,
            step_output=terminal_output if current["cmds"] else "",
            notes=notes,
            footer=self._page_footer(answer),
        )

        # Rebuild: first message, the response that led here; the page is the next prompt.
        (out_dir / f"call-{call_no}.context-before.json").write_text(
            json.dumps(chat.messages, indent=1)
        )
        chat._messages = new_messages
        chat.reset_response_chain()
        self._compactions += 1
        self._page_built = True
        self._page_head = page[:200]
        self._context_start_step = current["n"]
        self._kept_entries = kept_entries
        self._reader_floor = len(self._reader_returned)
        self._hinted = False

        (out_dir / f"call-{call_no}.page.txt").write_text(page)
        record.update(
            {
                "rebuilt": True,
                "kept_rules": [
                    {k: v for k, v in e.items() if k != "text"} for e in excerpts
                ],
                "kept_steps": [
                    {"step": k["step"], "why": k["why"], "chars": len(k["output"])}
                    for k in kept_steps
                ],
                "notes": notes,
                "page_chars": len(page),
                "messages_after": len(new_messages),
                "elapsed_sec": time.time() - started,
            }
        )
        self._write_compactor_record(record)
        self._pending_compaction_record = record
        ranges = ", ".join(e["lines"] for e in excerpts if e.get("inserted"))
        self._append_compactor_ref(
            run["session_id"], traj_name, call_no, f"rule lines kept {ranges or 'none'}"
        )
        return page

    def _append_compactor_ref(
        self, session_id: str, traj_name: str, call_no: int, summary: str
    ) -> None:
        self._pending_subagent_steps.append(
            Step(
                step_id=len(self._trajectory_steps) + 1,  # re-assigned when appended
                timestamp=datetime.now(timezone.utc).isoformat(),
                source="system",
                message=(
                    f"Compactor call {call_no}: {summary}. If the context was rebuilt, the "
                    "state page is part of the previous observation and replaces the "
                    "solver's history from here on."
                ),
                observation=Observation(
                    results=[
                        ObservationResult(
                            subagent_trajectory_ref=[
                                SubagentTrajectoryRef(
                                    session_id=session_id,
                                    trajectory_path=traj_name,
                                    extra={
                                        "summary": f"Compactor call {call_no}: {summary}"
                                    },
                                )
                            ]
                        )
                    ]
                ),
            )
        )

    def _write_compactor_record(self, record: dict[str, Any]) -> None:
        out_dir = self.logs_dir / "compactor"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            slim = {
                k: (
                    [{kk: vv for kk, vv in e.items() if kk != "text"} for e in v]
                    if k == "excerpts"
                    else v
                )
                for k, v in record.items()
            }
            (out_dir / f"call-{record['call']}.json").write_text(
                json.dumps(slim, indent=2, default=str)
            )
        except Exception as exc:
            self.logger.error(f"Failed to write the compactor record: {exc}")

    # ------------------------------------------------- verifier packet builders

    def _extra_flags(self, obs: str) -> list[str]:
        flags = []
        for m in COMPACTOR_CALL_RE.finditer(obs):
            n = int(m.group(1))
            rec = next((r for r in self._compactor_calls if r["call"] == n), {})
            ranges = ", ".join(
                e["lines"] for e in rec.get("excerpts", []) if e.get("inserted")
            )
            flags.append(
                f"compactor call {n}: solver context rebuilt, rule lines kept "
                f"{ranges or 'none'}"
            )
        if CONTEXT_NOTE_HEADER in obs:
            flags.append("context-size note")
        return flags

    def _index_intro_extra(self) -> str:
        return (
            " A step marked 'compactor call N' ends with a block headed "
            f"'{COMPACTOR_HEADER} (call N of this task) ===': at that step the solver's "
            "context was rebuilt, and from then on the solver saw only the task and that "
            "block (its answer file, your latest report, rule-file lines kept verbatim under "
            "'[k] lines a-b of <file>', commands kept verbatim) plus the later steps. "
            "Rule text delivered before that step and not kept in the block is no longer in "
            "the solver's context, but it did reach the solver."
        )

    # ---------------------------------------------------------------- summary

    def _write_summary(self) -> None:
        super()._write_summary()
        path = self.logs_dir / "rubric_verifier" / "summary.json"
        try:
            data = json.loads(path.read_text()) if path.exists() else {}
            calls = self._compactor_calls
            data.update(
                {
                    "harness": "reader-verifier-compactor-v1",
                    "compactor_skill_path": str(self._compactor_skill_path),
                    "compact_after_report": self._compact_after_report,
                    "compactor_max_steps": self._compactor_max_steps,
                    "compactor_max_chars": self._compactor_max_chars,
                    "compactor_keep_steps": self._compactor_keep_steps,
                    "compactor_timeout_sec": self._compactor_timeout_sec,
                    "compactor_thinking": self._compactor_thinking,
                    "compactor_fallback": self._compactor_fallback,
                    "compactor_min_tokens": self._compactor_min_tokens,
                    "compactor_hint_tokens": self._compactor_hint_tokens,
                    "compactor_force_tokens": self._compactor_force_tokens,
                    "max_compactions": self._max_compactions,
                    "n_compactor_calls": len(calls),
                    "n_compactions": self._compactions,
                    "compactor_calls": [
                        {
                            k: v
                            for k, v in r.items()
                            if k not in ("commands", "result", "excerpts")
                        }
                        for r in calls
                    ],
                    "compactor_skips": self._compactor_skips,
                    "compactor_prompt_tokens": sum(
                        r.get("prompt_tokens", 0) for r in calls
                    ),
                    "compactor_completion_tokens": sum(
                        r.get("completion_tokens", 0) for r in calls
                    ),
                    "compactor_seconds": sum(r.get("elapsed_sec", 0) for r in calls),
                }
            )
            data["verifier_prompt_tokens_note"] = (
                "verifier_prompt_tokens / verifier_completion_tokens cover all sub-agents; "
                "reader_* and compactor_* fields give the reader's and the compactor's share"
            )
            path.write_text(json.dumps(data, indent=2, default=str))
        except Exception as exc:
            self.logger.error(f"Failed to extend the summary: {exc}")
