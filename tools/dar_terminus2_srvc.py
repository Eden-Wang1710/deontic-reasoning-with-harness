"""terminus-2 solver-reader-verifier-compaction harness, v0.1 (SRVC).

Design: claude_docs/11_srvc_harness_design.md. Built on the solver-checks agent
(``Terminus2SolverChecks``); what changes is where each role's prompt comes from and what the
solver has to carry in its own context.

1. **Reader: a branch of the solver's conversation.** The rule document is not printed in the
   solver's first message (``solver_rule_inline_max_chars`` 0). Before the solver's first
   step the reader makes a *survey* call: the request is the solver's first message followed
   by the reader's instructions and the document with line numbers, so it shares its prefix
   with the solver's first request. The result (an outline of the top-level units with line
   ranges and a first reading of which ones the case engages, and the passages the case
   needs, copied verbatim by the harness) is appended to the solver's first message. Later
   reader calls are *branches*: a copy of the solver's messages plus one user message
   (instructions, document, query); the solver's chat is not touched. A range that is still
   in the solver's context is not inserted a second time, and a query that is only line
   ranges is answered by the harness without a model call.

2. **Compaction: a branch too, without thinking.** When the solver's context passes
   ``context_window`` tokens (32,000), or when the solver calls
   ``"subagent": {"name": "compaction"}``, the model is asked, on top of the solver's
   messages, for a short state page and the rule-document lines the remaining work needs.
   The harness then rebuilds the solver's messages: first message and first reply stay (the
   cached prefix), the steps after them are replaced by the page together with what the
   harness pins verbatim (the named lines, the answer file from disk, the verifier's
   report), and the latest ``compaction_keep_turns`` turns stay as they are.

3. **Verifier: independent, one round, a rubric table.** The rubrics are the checks of the
   solver-checks harness under their new name (``tools/skills/rubric_<name>_v1``), each in
   its own context with the task and the complete rule document. The verifier runs once;
   the solver decides on each finding by itself (no rebuttal, no second round) and then
   finishes. ``task_complete`` is accepted once the round has run.

4. **Thinking is not thrown away.** When a reply of a rubric or of the reader is cut at its
   output limit, the second request (without thinking) carries the reasoning of the cut
   reply as notes (``_cut_reply_notes``), instead of asking for the same work again.

5. **Files are written through a field.** ``"write_file": {"path", "content"}`` at the top
   level of a solver response is carried out by the harness before the commands of that
   response; the observation is one line. The answer file therefore passes through the
   solver's context once, as output, and is not printed back.

6. **Cost.** The budget is counted at the with-cache price (``cost_budget_basis`` cache).

What is specific to a benchmark is in the skills and in a few kwargs (``rule_path``,
``answer_path``, ``answer_headings``), not in this file.

Agent kwargs (``--ak key=value``), in addition to the solver-checks agent's: ``rubrics``
(rubric_section_table_v1,rubric_fact_table_v1,rubric_grounded_reasoning_v1),
``rubric_format_path``, ``rubric_max_output_tokens`` (8000), ``solver_rule_inline_max_chars``
(0), ``reader_survey`` (true), ``reader_survey_max_chars`` (20000), ``reader_inline_max_chars``
(60000; up to this size the document is printed in the reader's message, above it the reader
uses command steps), ``reader_direct_ranges`` (true), ``context_window`` (32000),
``compaction_keep_turns`` (2), ``compaction_min_tokens`` (half the window; below it a call by
the solver is declined), ``compaction_max_output_tokens`` (2000), ``compaction_min_saving``
(0.2), ``compaction_source_max_chars`` (12000), ``compaction_skill_path``, ``keep_thinking``
(true), ``keep_thinking_max_chars`` (40000). Defaults changed: ``max_verify_rounds`` 1,
``cost_budget_basis`` cache, solver skill v5, reader skill v2, template v5, report format v3.

Logs per trial, in addition to the solver-checks agent's: ``agent/reader/call-N.json`` (with
``kind`` survey | query | direct), ``agent/compaction/call-N.json`` with
``call-N.context-before.json`` / ``context-after.json`` / ``page.txt``,
``agent/write_file/NNN.txt`` (every file written through the field), and ``summary.json``
with ``"harness": "srvc-v0.1"``. Ledger roles: solver, reader, compaction,
``verifier.<rubric>``.

Usage (from ``harbor-deonticbench/``, with ``tools/`` on PYTHONPATH):

    harbor run --agent-import-path dar_terminus2_srvc:Terminus2SRVC ...

``tools/run_dar_qwen35_35b.sh`` with ``AGENT=t2srvc`` does this.
"""

from __future__ import annotations

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

from dar_terminus2_checks import (
    NO_ANSWER,
    RULE_SLOT,
    SKILLS_DIR,
    TOOLS_DIR,
    CheckSkill,
    Terminus2SolverChecks,
    _clip,
)
from dar_terminus2_multiagent import (
    GATE_HEADER,
    LINES_RE,
    READER_HEADER,
    VERIFIER_HEADER,
    _sha,
    reply_object,
)
from dar_terminus2_verifier import (
    SubagentClock,
    _extract_json_object,
    _falsy,
    _opt_tokens,
)

SOLVER_V5_SKILL_PATH = SKILLS_DIR / "solver_v5" / "SKILL.md"
READER_V2_SKILL_PATH = SKILLS_DIR / "reader_v2" / "SKILL.md"
COMPACTION_SKILL_PATH = SKILLS_DIR / "compaction_v1" / "SKILL.md"
RUBRIC_FORMAT_PATH = SKILLS_DIR / "rubric_report_v3" / "FORMAT.md"
SRVC_TEMPLATE_PATH = TOOLS_DIR / "templates" / "terminus-json-multiagent-v5.txt"
DEFAULT_RUBRICS = (
    "rubric_section_table_v1,rubric_fact_table_v1,rubric_grounded_reasoning_v1"
)

COMPACTION_HEADER = "=== COMPACTION RESULT"
REBUILT_HEADER = "=== CONTEXT REBUILT"
COMPACTION_ALIASES = ("compaction", "compact", "compactor", "compress")

SUBAGENT_FORM = (
    '"subagent" is a top-level field of the JSON response, next to "commands"; it is never a '
    'keystroke and never a field inside a command object. Correct form: {"analysis": "...", '
    '"plan": "...", "commands": [], "subagent": {"name": "reader", "query": "..."}}, or '
    '"subagent": {"name": "verifier"}, or "subagent": {"name": "compaction"}.'
)
WRITE_FORM = (
    '"write_file" is a top-level field of the JSON response, next to "commands": '
    '{"analysis": "...", "plan": "...", "commands": [], "write_file": {"path": '
    '"/absolute/path", "content": "the complete file as one JSON string"}}.'
)

# A branch call is one user message after the solver's messages (or, for the survey, after
# the solver's first message in the same user message). It has to say that the solver's
# reply format is suspended for one reply.
BRANCH_HEADER = (
    "=== HARNESS MESSAGE: {title} ===\n"
    "This message comes from the harness, not from the terminal. Everything above it is the "
    "solver's side of the task: its instructions and its steps so far. The response format "
    'described there does not apply to your next reply: do not send "plan", keystroke '
    'commands, "subagent", "write_file" or "task_complete". For this one reply you take the '
    "role described below. The exchange is not part of the solver's conversation, which "
    "continues from before this message.\n\n"
)
READER_CLOSING = (
    "For this reply you are the reader, not the solver: do not solve the task, and do not "
    'send "plan", "write_file", "subagent" or "task_complete". Reply now with exactly one '
    'JSON object of the form {"analysis": "...", "commands": [], "result": {...}}, whose '
    '"result" is filled in from the lines you have in front of you: "excerpts" (each with '
    '"lines", "label", "why"), "unresolved" and, on a survey call, "outline".'
)
COMPACTION_CLOSING = (
    "This reply is the state page, not a step of the task: do not send \"plan\", "
    '"commands", "write_file", "subagent" or "task_complete". Reply now with exactly one '
    'JSON object with the fields "page" (a string) and "keep_source" (a list), and nothing '
    "else."
)

# "lines 10-20", "10-20" or "line 14"; a bare number is not read as a line number.
_RANGE = r"(?:(?:lines?\s*)?\d+\s*(?:-|–|—|to)\s*\d+|lines?\s*\d+)"
DIRECT_QUERY_RE = re.compile(
    rf"^\s*{_RANGE}(?:\s*(?:,|;|and)\s*{_RANGE})*\s*\.?\s*$", re.IGNORECASE
)
_RANGE_PARTS_RE = re.compile(r"(\d+)(?:\s*(?:-|–|—|to)\s*(\d+))?", re.IGNORECASE)
# A query that begins with the word "line(s)" and ranges and goes on with other text, as in
# an outline row copied whole ("lines 95-179 · <unit>"): the ranges are what is asked for.
_DIRECT_HEAD_RE = re.compile(
    rf"^\s*lines?\s*\d+(?:\s*(?:-|–|—|to)\s*\d+)?(?:\s*(?:,|;|and)\s*(?:{_RANGE}|\d+))*"
    r"(?=$|[\s.,;:·])",
    re.IGNORECASE,
)
_OTHER_FILE_RE = re.compile(r"^\s*(?:of|in|from)\s+(?:the\s+)?(?:file\s+)?(/\S+)", re.IGNORECASE)


def direct_range_text(query: str, rule_path: str) -> str | None:
    """The line ranges of a query that asks for lines of the rule document by number, or
    None when the query needs the reader."""
    if DIRECT_QUERY_RE.match(query):
        return query
    head = _DIRECT_HEAD_RE.match(query)
    if not head:
        return None
    other = _OTHER_FILE_RE.match(query[head.end() :])
    if other and other.group(1).rstrip(".,;:)") != rule_path:
        return None
    return head.group(0)


def skill_body(text: str) -> str:
    """A skill file without its front matter."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].strip()
    return text.strip()


def message_text(message: dict[str, Any]) -> str:
    """Content of a chat message, with the arguments of its tool calls if it has any."""
    content = message.get("content")
    text = content if isinstance(content, str) else ""
    for call in message.get("tool_calls") or []:
        args = (call.get("function") or {}).get("arguments")
        text += "\n" + (args if isinstance(args, str) else json.dumps(args))
    return text


def split_turns(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]] | None:
    """The solver's messages as steps: a user message, the assistant's reply and, for a
    solver that calls tools, the tool messages that close its calls. None when the
    messages do not have that shape."""
    turns: list[list[dict[str, Any]]] = []
    for m in messages:
        if m.get("role") == "user":
            turns.append([m])
        elif not turns:
            return None
        else:
            turns[-1].append(m)
    for turn in turns:
        if len(turn) < 2 or turn[1].get("role") != "assistant":
            return None
        if any(m.get("role") != "tool" for m in turn[2:]):
            return None
        if not all(isinstance(m.get("content"), str) for m in turn[:2]):
            return None
    return turns


def json_diagnosis(text: str) -> str:
    """Where a reply that should be one JSON object stops being JSON, in one line."""
    start = text.find("{")
    if start < 0:
        return ""
    body = text[start:].strip()
    try:
        json.loads(body)
    except json.JSONDecodeError as exc:
        around = body[max(0, exc.pos - 60) : exc.pos + 20].replace("\n", "\\n")
        return (
            f"JSON decoder: {exc.msg} at line {exc.lineno}, column {exc.colno} of the "
            f"object, near: ...{around}... Check that every bracket and brace is closed by "
            "its own kind and that quotes and backslashes inside strings are escaped."
        )
    return ""


def merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sorted union of inclusive line ranges; ranges that touch are joined."""
    out: list[tuple[int, int]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def covered(a: int, b: int, merged: list[tuple[int, int]]) -> bool:
    return any(x <= a and b <= y for x, y in merged)


def parse_range(value: Any) -> tuple[int, int] | None:
    text = str(value or "").strip()
    if text.isdigit():
        text = f"{text}-{text}"
    m = LINES_RE.match(text)
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    return (a, b) if 1 <= a <= b else None


def ranges_text(ranges: list[tuple[int, int]]) -> str:
    return ", ".join(f"{a}-{b}" for a, b in ranges) if ranges else "none"


class Terminus2SRVC(Terminus2SolverChecks):
    """Solver-reader-verifier-compaction harness v0.1; see the module docstring."""

    SUBAGENT_NAMES = ("reader", "verifier", "compaction")
    SUBAGENT_HELP = (
        '"reader" (with a "query"), "verifier" (no arguments) and "compaction" (no '
        "arguments)"
    )
    SUBAGENT_FORM = SUBAGENT_FORM

    def __init__(
        self,
        *args,
        rubrics: str | list[str] | None = None,
        rubric_format_path: str | None = None,
        rubric_max_output_tokens: int | str | None = None,
        checks: str | list[str] | None = None,
        check_format_path: str | None = None,
        check_max_output_tokens: int | str | None = 8000,
        solver_skill_path: str | None = None,
        reader_skill_path: str | None = None,
        compaction_skill_path: str | None = None,
        derivation_template: str | None = None,
        max_verify_rounds: int | str = 1,
        cost_budget_basis: str = "cache",
        solver_rule_inline_max_chars: int | str = 0,
        reader_survey: bool | str = True,
        reader_survey_max_chars: int | str = 20000,
        reader_inline_max_chars: int | str = 60000,
        reader_direct_ranges: bool | str = True,
        context_window: int | str = 32000,
        compaction_keep_turns: int | str = 2,
        compaction_min_tokens: int | str | None = None,
        compaction_max_output_tokens: int | str | None = 2000,
        compaction_min_saving: float | str = 0.2,
        compaction_source_max_chars: int | str = 12000,
        keep_thinking: bool | str = True,
        keep_thinking_max_chars: int | str = 40000,
        **kwargs,
    ):
        super().__init__(
            *args,
            checks=rubrics or checks or DEFAULT_RUBRICS,
            check_format_path=rubric_format_path
            or check_format_path
            or str(RUBRIC_FORMAT_PATH),
            check_max_output_tokens=rubric_max_output_tokens
            if rubric_max_output_tokens is not None
            else check_max_output_tokens,
            solver_skill_path=solver_skill_path or str(SOLVER_V5_SKILL_PATH),
            reader_skill_path=reader_skill_path or str(READER_V2_SKILL_PATH),
            derivation_template=derivation_template or str(SRVC_TEMPLATE_PATH),
            max_verify_rounds=max_verify_rounds,
            cost_budget_basis=cost_budget_basis,
            **kwargs,
        )
        self._solver_rule_inline_max_chars = int(solver_rule_inline_max_chars)
        self._solver_rule_inline = False
        self._reader_survey = not _falsy(reader_survey)
        self._reader_survey_max_chars = int(reader_survey_max_chars)
        self._reader_inline_max_chars = int(reader_inline_max_chars)
        self._reader_direct_ranges = not _falsy(reader_direct_ranges)
        self._context_window = int(context_window)
        self._compaction_keep_turns = max(1, int(compaction_keep_turns))
        self._compaction_min_tokens = (
            int(compaction_min_tokens)
            if compaction_min_tokens not in (None, "", "none", "None")
            else self._context_window // 2
        )
        self._compaction_max_output_tokens = _opt_tokens(compaction_max_output_tokens)
        self._compaction_min_saving = float(compaction_min_saving)
        self._compaction_source_max_chars = int(compaction_source_max_chars)
        self._compaction_skill_path = (
            Path(compaction_skill_path)
            if compaction_skill_path
            else COMPACTION_SKILL_PATH
        )
        self._compaction_skill_text = self._compaction_skill_path.read_text()
        self._keep_thinking = not _falsy(keep_thinking)
        self._keep_thinking_max_chars = int(keep_thinking_max_chars)

        self._solver_chat: Chat | None = None
        self._last_prompt_tokens = 0  # prompt size of the solver's latest request
        self._last_reply_chars = 0
        # Rule-document lines put into the solver's context, each batch under the header of
        # the block that carries it; a batch counts while its header is in the messages.
        self._delivered: list[dict[str, Any]] = []
        self._survey: dict[str, Any] | None = None
        self._compactions: list[dict[str, Any]] = []
        self._compacted_this_step = False
        self._next_auto_at = 0  # no automatic compaction below this estimate
        self._pending_compaction_record: dict[str, Any] | None = None
        self._pending_writes: list[tuple[str, str]] = []
        self._write_notes: list[str] = []
        self._writes: list[dict[str, Any]] = []
        self._notes_handed_over: list[dict[str, Any]] = []
        self._notes_tail = ""  # the role's own closing line, set for the length of a branch
        self._n_json_diagnoses = 0

    @staticmethod
    def name() -> str:
        return "terminus-2-srvc"

    def _limit_output_length(self, output: str, max_bytes: int = 10000) -> str:
        if COMPACTION_HEADER in output:
            return output
        return super()._limit_output_length(output, max_bytes)

    # ---------------------------------------------------------- trial set-up

    def _reader_inline(self) -> bool:
        return (
            self._rule_text is not None
            and len(self._rule_text) <= self._reader_inline_max_chars
        )

    async def run(self, instruction, environment, context) -> None:
        self._task_instruction = instruction
        await self._load_rule(environment)
        self._solver_rule_inline = (
            self._rule_text is not None
            and len(self._rule_text) <= self._solver_rule_inline_max_chars
        )
        if self._solver_rule_inline:
            block = self._rule_block()
        else:
            size = (
                f" ({self._rule_line_count()} lines, {len(self._rule_text):,} characters)"
                if self._rule_text is not None
                else ""
            )
            block = (
                f"[The rule document {self._rule_path}{size} is not printed here. "
                + (
                    "A reader has surveyed it for this task: the result is at the end of "
                    "this message. "
                    if self._reader_survey and self._rule_text is not None
                    else ""
                )
                + "Get rule text through the reader sub-agent.]"
            )
        if RULE_SLOT in self._prompt_template:
            # The template is str.format-ed with the instruction later: double the braces.
            self._prompt_template = self._prompt_template.replace(
                RULE_SLOT, block.replace("{", "{{").replace("}", "}}")
            )
        else:  # a template without the slot: the note follows the task
            instruction = f"{instruction.rstrip()}\n\n{block}"
        # Terminus2SolverChecks.run would fill the slot with the whole document.
        await super(Terminus2SolverChecks, self).run(instruction, environment, context)

    async def _run_agent_loop(
        self,
        initial_prompt: str,
        chat: Chat,
        logging_dir: Path | None = None,
        original_instruction: str = "",
    ) -> None:
        self._original_instruction = original_instruction
        self._solver_chat = chat
        if (
            self._reader_survey
            and not self._solver_rule_inline
            and self._rule_text is not None
            and self._session is not None
        ):
            try:
                block = await self._reader_run(
                    self._session.environment,
                    kind="survey",
                    query="",
                    history=None,
                    prefix=initial_prompt.rstrip() + "\n\n",
                )
            except Exception as exc:  # keep the trial alive; CancelledError passes
                self.logger.error(f"Reader survey failed: {exc!r}")
                block = (
                    f"{READER_HEADER} (survey) ===\n"
                    "The survey of the rule document produced no result. Call the reader "
                    'with a "query" for the rule text you need.'
                )
            initial_prompt = f"{initial_prompt.rstrip()}\n\n{block}"
            if self._trajectory_steps:
                self._trajectory_steps[0].message = initial_prompt
        await super()._run_agent_loop(
            initial_prompt, chat, logging_dir, original_instruction
        )

    # ------------------------------------------------------------ step hooks

    async def _handle_llm_interaction(
        self,
        chat: Chat,
        prompt: str,
        logging_paths,
        original_instruction: str = "",
        session: TmuxSession | None = None,
    ):
        self._solver_chat = chat
        n_ledger = len(self._ledger)
        out = await super()._handle_llm_interaction(
            chat, prompt, logging_paths, original_instruction, session
        )
        commands, is_task_complete, feedback, analysis, plan, llm_response = out
        if feedback and "No valid JSON" in feedback:
            # The stock message does not say where the JSON breaks; the decoder does.
            where = json_diagnosis(llm_response.content or "")
            if where:
                feedback = f"{feedback}\n{where}"
                out = (commands, is_task_complete, feedback, analysis, plan, llm_response)
                self._n_json_diagnoses += 1
        entry = next(
            (e for e in reversed(self._ledger[n_ledger:]) if e["role"] == "solver"),
            None,
        )
        if entry and entry["prompt_tokens"]:
            self._last_prompt_tokens = entry["prompt_tokens"] // max(
                1, entry.get("n_replies") or 1
            )
            rec = self._pending_compaction_record
            if rec is not None:
                # First solver request after a compaction: the size of the rebuilt context.
                rec["solver_prompt_tokens_after"] = self._last_prompt_tokens
                self._write_compaction_record(rec)
                self._pending_compaction_record = None
        self._last_reply_chars = len(llm_response.content or "")
        self._compacted_this_step = False
        self._pending_writes, self._write_notes = [], []
        if not (feedback and "ERROR:" in feedback):
            self._parse_write_file(reply_object(llm_response.content))
        return out

    async def _execute_commands(
        self, commands: list[Command], session: TmuxSession
    ) -> tuple[bool, str]:
        # Files first: a command of the same response may read what was written, and the
        # verifier called in it reads the answer file.
        write_lines = await self._do_writes(session.environment)
        timeout_occurred, terminal_output = await super()._execute_commands(
            commands, session
        )
        if write_lines:
            note = "\n".join(write_lines)
            terminal_output = (
                f"{note}\n\n{terminal_output}" if terminal_output.strip() else note
            )
        accepted = self._episode_claim and self._pending_completion
        if (
            not accepted
            and not self._compacted_this_step
            and self._auto_compaction_due(len(terminal_output))
        ):
            try:
                note = await self._compaction_call(session, terminal_output, "auto")
            except Exception as exc:  # keep the trial alive; CancelledError passes
                self.logger.error(f"Compaction failed: {exc!r}")
                note = None
            if note:
                terminal_output = f"{terminal_output.rstrip()}\n\n{note}"
        return timeout_occurred, terminal_output

    # ------------------------------------------------------------ sub-agents

    def _normalise_subagent(self, sub: Any) -> dict[str, Any] | None:
        if isinstance(sub, str) and sub.strip().lower() in COMPACTION_ALIASES:
            sub = {"name": "compaction"}
        elif isinstance(sub, dict):
            name = str(sub.get("name") or sub.get("agent") or sub.get("tool") or "")
            if name.strip().lower() in COMPACTION_ALIASES:
                sub = {**sub, "name": "compaction"}
        # Terminus2SolverChecks adds the rebuttal and switches the reader off; neither here.
        out = super(Terminus2SolverChecks, self)._normalise_subagent(sub)
        if (
            out is not None
            and not out.get("error")
            and out["name"] == "reader"
            and self._solver_rule_inline
        ):
            return {
                "error": "The reader is not available in this task: the complete rule "
                "document is in your first message (the block headed === RULE DOCUMENT). "
                "Work from that text. No sub-agent was called."
            }
        return out

    async def _dispatch_subagent(
        self,
        session: TmuxSession,
        sub: dict[str, Any],
        commands: list[Command],
        terminal_output: str,
    ) -> str:
        self._pending_rebuttal = None
        if sub["name"] == "compaction":
            try:
                note = await self._compaction_call(session, terminal_output, "solver")
            except Exception as exc:  # keep the trial alive; CancelledError passes
                self.logger.error(f"Compaction failed: {exc!r}")
                note = None
            return note or f"{COMPACTION_HEADER} ===\nNo compaction was done."
        if sub["name"] == "reader":
            return await self._reader_call(session, sub["query"])
        return await self._verifier_call(session, commands, terminal_output)

    def _cut_reply_notes(self) -> str:
        """The reasoning of the reply that was just cut at its output limit, as notes for
        the request that follows it (sent without thinking)."""
        cut = self._last_cut_reply
        self._last_cut_reply = None
        if not self._keep_thinking or not cut:
            return ""
        reasoning = (cut.get("reasoning_content") or "").strip()
        content = (cut.get("content") or "").strip()
        if not reasoning and not content:
            return ""
        limit = self._keep_thinking_max_chars
        if len(reasoning) > limit:  # the later part is where the conclusions are
            reasoning = "[...earlier notes left out]\n" + reasoning[-limit:]
        parts = [
            "",
            "",
            "=== YOUR NOTES SO FAR ===",
            "Your previous reply to this message was cut off at the output limit before it "
            "was complete. What you had worked out is below, verbatim. Do not start over: "
            "take these notes as your work so far, settle what is still open in a few lines "
            "at most, and reply now with the final JSON object only.",
        ]
        if reasoning:
            parts += ["--- your reasoning ---", reasoning]
        if content:
            parts += ["--- your reply as far as it got (cut off) ---", content[-limit:]]
        parts.append("=== END OF NOTES ===")
        if self._notes_tail:  # a branch: say again whose reply this is
            parts.append(self._notes_tail)
        self._notes_handed_over.append(
            {
                "role": self._llm_role,
                "reasoning_chars": len(reasoning),
                "content_chars": len(content),
            }
        )
        return "\n".join(parts)

    # ---------------------------------------------------------------- reader

    def _ranges_in_context(
        self, messages: list[dict[str, Any]] | None = None
    ) -> list[tuple[int, int]]:
        """Rule-document lines whose text is in the solver's messages."""
        if messages is None:
            messages = self._solver_chat.messages if self._solver_chat else []
        texts = [m.get("content") for m in messages]
        texts = [t for t in texts if isinstance(t, str)]
        ranges: list[tuple[int, int]] = []
        for batch in self._delivered:
            if any(batch["mark"] in t for t in texts):
                ranges.extend(batch["ranges"])
        return merge_ranges(ranges)

    def _source_block(self) -> str:
        lines = (self._rule_text or "").splitlines()
        body = "\n".join(f"{i:>4}: {line}" for i, line in enumerate(lines, 1))
        return (
            f"=== SOURCE: {self._rule_path} ({len(lines)} lines; each line is printed as "
            '"<number>: <text>", the number is not part of the text) ===\n'
            f"{body}\n=== END OF SOURCE ==="
        )

    def _build_reader_branch_packet(
        self, kind: str, query: str, inline: bool, max_steps: int, max_chars: int
    ) -> str:
        parts: list[str] = []
        if inline:
            parts += [self._source_block(), ""]
        else:
            n = f" ({self._rule_line_count()} lines)" if self._rule_text else ""
            parts += [
                f"=== SOURCE: {self._rule_path}{n} ===",
                "The document is too long to print here. Read it with commands "
                f"(at most {max_steps} command steps).",
                "",
            ]
        if kind == "survey":
            parts += [
                "=== THIS CALL: SURVEY ===",
                "The solver has seen nothing of the document yet. Return the outline of "
                "all its top-level units and the passages this case needs.",
            ]
        else:
            have = self._ranges_in_context()
            parts += [
                "=== THIS CALL: QUERY ===",
                f"Query from the solver: {query}",
                "Lines already in the solver's context (leave them out): "
                + ranges_text(have),
            ]
        parts.append(
            f"Limit: {max_chars:,} characters of excerpts in total. Ranges without a "
            f'"file" refer to {self._rule_path}.'
            + (" No commands: reply with the result at once." if inline else "")
        )
        return "\n".join(parts)

    def _reader_result_problem(self, kind: str, result: dict) -> str | None:
        """What is wrong with the shape of a reader result, or None."""
        excerpts = result.get("excerpts")
        if isinstance(excerpts, dict):
            excerpts = [excerpts]
        if not isinstance(excerpts, list) or any(
            not isinstance(e, dict) for e in excerpts
        ):
            return '"excerpts" must be a list of objects with "lines", "label" and "why".'
        bad = [
            str(e.get("lines"))
            for e in excerpts
            if parse_range(e.get("lines") or e.get("range")) is None
        ]
        if bad:
            return (
                f'These "lines" values are not line ranges of the form "a-b": '
                f"{', '.join(bad[:5])}."
            )
        if kind != "survey":
            if not excerpts and not result.get("unresolved"):
                return (
                    '"excerpts" is empty and "unresolved" is empty: return the lines that '
                    'answer the query, or say in "unresolved" what could not be found.'
                )
            return None
        outline = result.get("outline")
        if not isinstance(outline, list) or not outline:
            return (
                'A survey needs "outline": one row for every top-level unit of the '
                'document, each {"unit", "lines", "engaged", "note"}.'
            )
        ranges = []
        for i, row in enumerate(outline, 1):
            r = parse_range(row.get("lines")) if isinstance(row, dict) else None
            if r is None or not str(row.get("unit") or "").strip():
                return (
                    f'Outline row {i} needs a "unit" and "lines" of the form "a-b" (the '
                    "range of the whole unit)."
                )
            ranges.append(r)
        n = self._rule_line_count()
        if n:
            merged = merge_ranges(ranges)
            gaps, at = [], 1
            for a, b in merged + [(n + 1, n + 1)]:
                if a > at:
                    gaps.append((at, min(a - 1, n)))
                at = max(at, b + 1)
            # Blank lines between two units belong to neither.
            text = (self._rule_text or "").splitlines()
            gaps = [
                (a, b)
                for a, b in gaps
                if b >= a and any(ln.strip() for ln in text[a - 1 : b])
            ]
            missing = sum(
                1 for a, b in gaps for ln in text[a - 1 : b] if ln.strip()
            )
            if missing > max(5, n // 50):
                return (
                    "The outline leaves lines "
                    + ranges_text(gaps[:8])
                    + " of the document in no unit. Every top-level unit needs a row, "
                    "and the ranges together cover the document."
                )
        return None

    @staticmethod
    def _clean_outline(result: dict | None) -> list[dict[str, str]]:
        rows = []
        for row in (result or {}).get("outline") or []:
            if not isinstance(row, dict):
                continue
            r = parse_range(row.get("lines"))
            if r is None:
                continue
            engaged = str(row.get("engaged") or "").strip().lower()
            if engaged in ("true", "y"):
                engaged = "yes"
            elif engaged in ("false", "n"):
                engaged = "no"
            elif engaged not in ("yes", "no"):
                engaged = "unclear"
            rows.append(
                {
                    "unit": _clip(row.get("unit"), 160),
                    "lines": f"{r[0]}-{r[1]}",
                    "engaged": engaged,
                    "note": _clip(row.get("note"), 240),
                }
            )
        return rows

    def _drop_delivered(self, result: dict) -> tuple[dict, list[str]]:
        """Take out the excerpts whose lines are all in the solver's context already."""
        have = self._ranges_in_context()
        raw = result.get("excerpts")
        if not have or not isinstance(raw, list):
            return result, []
        kept, dropped = [], []
        for e in raw:
            r = parse_range(e.get("lines") or e.get("range")) if isinstance(e, dict) else None
            own_file = isinstance(e, dict) and str(
                e.get("file") or self._rule_path
            ).strip() == self._rule_path
            if r is not None and own_file and covered(r[0], r[1], have):
                dropped.append(f"{r[0]}-{r[1]}")
            else:
                kept.append(e)
        return {**result, "excerpts": kept}, dropped

    async def _reader_call(self, session: TmuxSession, query: str) -> str:
        environment = session.environment
        if self._reader_direct_ranges and direct_range_text(query, self._rule_path):
            return await self._reader_run(
                environment, kind="direct", query=query, history=None, prefix=""
            )
        chat = self._solver_chat
        history = [dict(m) for m in chat.messages] if chat is not None else None
        return await self._reader_run(
            environment, kind="query", query=query, history=history, prefix=""
        )

    async def _reader_run(
        self,
        environment,
        *,
        kind: str,
        query: str,
        history: list[dict[str, Any]] | None,
        prefix: str,
    ) -> str:
        """One reader call: ``survey`` (before the solver's first step), ``query`` (a branch
        of the solver's conversation) or ``direct`` (line ranges, no model call)."""
        call_no = len(self._reader_calls) + 1
        started = time.time()
        max_chars = (
            self._reader_survey_max_chars if kind == "survey" else self._reader_max_chars
        )
        packet = ""
        run: dict[str, Any] | None = None
        clock: SubagentClock | None = None
        if kind == "direct":
            result: dict | None = {
                "excerpts": [
                    {"lines": f"{a}-{b or a}", "label": "", "why": ""}
                    for a, b in _RANGE_PARTS_RE.findall(
                        direct_range_text(query, self._rule_path) or query
                    )
                ],
                "unresolved": [],
            }
        else:
            inline = self._reader_inline()
            max_steps = (
                1
                if inline
                else max(self._reader_max_steps, 6)
                if kind == "survey"
                else self._reader_max_steps
            )
            packet = self._build_reader_branch_packet(
                kind, query, inline, max_steps, max_chars
            )
            clock = SubagentClock(
                self._reader_timeout_sec,
                self._reader_thinking,
                self._reader_fallback,
                max_output_tokens=self._reader_max_output_tokens,
            )
            self._notes_tail = READER_CLOSING
            try:
                run = await self._run_tool_subagent(
                    environment,
                    role="reader",
                    prefix=prefix,
                    skill_text=BRANCH_HEADER.format(title="READER CALL")
                    + skill_body(self._reader_skill_text),
                    packet=packet,
                    max_steps=max_steps,
                    closing=READER_CLOSING,
                    clock=clock,
                    validate=lambda r: self._reader_result_problem(kind, r),
                    lift=lambda reply: reply if "excerpts" in reply else None,
                    history=history,
                    allow_commands=not inline,
                    begin=READER_CLOSING
                    if inline
                    else "Begin. " + READER_CLOSING.split("Reply now")[0].strip()
                    + " Respond with exactly one JSON object in the reader's reply format "
                    "described above.",
                )
            finally:
                self._notes_tail = ""
            result = run["result"]

        outline = self._clean_outline(result) if kind == "survey" else []
        dropped: list[str] = []
        if isinstance(result, dict) and kind != "survey":
            result, dropped = self._drop_delivered(result)
        excerpts, extraction = await self._extract_excerpts(
            environment, result, call_no, max_chars=max_chars
        )
        header = (
            f"{READER_HEADER} (survey, call {call_no} of this task) ==="
            if kind == "survey"
            else f"{READER_HEADER} (call {call_no} of this task) ==="
        )
        block = self._render_reader_block(
            header,
            kind,
            query,
            result,
            outline,
            excerpts,
            extraction,
            dropped,
            call_no,
            run,
        )
        inserted: list[tuple[int, int]] = []
        for e in excerpts:
            r = parse_range(e.get("lines_shown") or e.get("lines"))
            if e.get("inserted") and e.get("file") == self._rule_path and r:
                inserted.append(r)
        if inserted:
            self._delivered.append({"mark": header, "ranges": inserted})
        elapsed = time.time() - started

        traj_name = None
        if run is not None:
            traj_name = f"trajectory.reader-call-{call_no}.json"
            self._save_subagent_trajectory(
                run["session_id"],
                run["steps"],
                traj_name,
                run["chat"],
                agent_name=f"{self.name()}/reader",
                extra={
                    "parent_session_id": self._session_id,
                    "reader_call": call_no,
                    "kind": kind,
                    "branch_of_messages": len(history) if history else 0,
                    "skill_path": str(self._reader_skill_path),
                },
            )
        record = {
            "call": call_no,
            "kind": kind,
            "query": query,
            "result": result,
            "outline": outline,
            "excerpts": excerpts,
            "dropped_in_context": dropped,
            "chars_inserted": extraction["chars"],
            "truncated": extraction["truncated"],
            "not_inserted": extraction["not_inserted"],
            "invalid": extraction["invalid"],
            "result_text": block,
            "branch_of_messages": len(history) if history else 0,
            "source_inline": self._reader_inline() if run is not None else None,
            "steps_used": run["steps_used"] if run else 0,
            "parse_failures": run["parse_failures"] if run else 0,
            "repairs": run["repairs"] if run else [],
            "context_exceeded": run["context_exceeded"] if run else False,
            "timed_out": run["timed_out"] if run else False,
            "limit_reached": clock.limit_reached if clock else None,
            "thinking_fallback": clock.fell_back if clock else False,
            "notes_chars": run["notes_chars"] if run else 0,
            "attempts": clock.attempts if clock else [],
            "commands": run["commands"] if run else [],
            "packet_chars": len(packet),
            "prompt_tokens": run["chat"].total_input_tokens if run else 0,
            "completion_tokens": run["chat"].total_output_tokens if run else 0,
            "n_llm_calls": run["n_calls"] if run else 0,
            "elapsed_sec": elapsed,
            "trajectory_file": traj_name,
        }
        self._reader_calls.append(record)
        if kind == "survey":
            self._survey = record
        out_dir = self.logs_dir / "reader"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"call-{call_no}.json").write_text(json.dumps(record, indent=2))
        if packet:
            (out_dir / f"call-{call_no}.packet.txt").write_text(packet)

        ranges = ranges_text(merge_ranges(inserted))
        self._reader_index[call_no] = ranges if inserted else "nothing inserted"
        if run is not None:
            self._pending_subagent_steps.append(
                Step(
                    step_id=len(self._trajectory_steps) + 1,  # re-assigned when appended
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    source="system",
                    message=(
                        f"Reader call {call_no} ({kind}, {run['n_calls']} model call(s), "
                        f"{elapsed:.0f} s"
                        + (
                            f"; {clock.describe_limit()}"
                            if clock and clock.limit_reached
                            else ""
                        )
                        + "); its result is part of the "
                        + ("first message." if kind == "survey" else "previous observation.")
                    ),
                    observation=Observation(
                        results=[
                            ObservationResult(
                                subagent_trajectory_ref=[
                                    SubagentTrajectoryRef(
                                        session_id=run["session_id"],
                                        trajectory_path=traj_name,
                                        extra={
                                            "summary": f"Reader call {call_no} ({kind}): "
                                            f"lines {ranges}"
                                        },
                                    )
                                ]
                            )
                        ]
                    ),
                )
            )
        return block

    def _render_reader_block(
        self,
        header: str,
        kind: str,
        query: str,
        result: dict | None,
        outline: list[dict[str, str]],
        excerpts: list[dict[str, Any]],
        extraction: dict[str, Any],
        dropped: list[str],
        call_no: int,
        run: dict[str, Any] | None,
    ) -> str:
        base = self._render_reader_result(
            call_no,
            query,
            result,
            excerpts,
            extraction,
            run["steps_used"] if run else 0,
            run["context_exceeded"] if run else False,
            timed_out=run["timed_out"] if run else False,
        ).split("\n")
        # base[0] is the stock header and base[1] the query line.
        body = base[2:]
        lines = [header]
        if kind == "survey":
            n = f" ({self._rule_line_count()} lines)" if self._rule_text else ""
            lines.append(
                f"The reader's survey of {self._rule_path}{n} for this task. The outline is "
                "a first reading, not a finding: you decide. Ask the reader for any text "
                "you need that is not below."
            )
            if outline:
                lines += ["", "Outline (every top-level unit, with its line range):"]
                lines += [
                    f"- lines {r['lines']} · {r['unit']} · engaged: {r['engaged']}"
                    + (f" · {r['note']}" if r["note"] else "")
                    for r in outline
                ]
                lines += ["", "Passages (verbatim, copied by the harness):"]
        else:
            lines.append(f"Query: {query}")
        lines += body
        if dropped:
            lines.append(
                "Not inserted again, because these lines are already in your context: "
                + ", ".join(dropped)
                + "."
            )
        elif kind == "query" and not excerpts:
            have = self._ranges_in_context()
            if have:
                lines.append(
                    "The reader leaves out lines that are already in your context: "
                    f"{ranges_text(have)} of {self._rule_path}."
                )
        return "\n".join(lines)

    # -------------------------------------------------------------- write_file

    def _parse_write_file(self, obj: dict[str, Any]) -> None:
        raw = obj.get("write_file")
        cmds = obj.get("commands")
        if any(isinstance(c, dict) and "write_file" in c for c in cmds or []):
            self._write_notes.append(
                '[write_file] NOT written: "write_file" was inside a command object, where '
                f"the harness does not read it. {WRITE_FORM}"
            )
        if raw in (None, "", {}, []):
            return
        for item in raw if isinstance(raw, list) else [raw]:
            if not isinstance(item, dict):
                self._write_notes.append(
                    f'[write_file] NOT written: the field must be an object. {WRITE_FORM}'
                )
                continue
            path = str(item.get("path") or item.get("file") or "").strip()
            content = item.get("content", item.get("text"))
            if not path.startswith("/"):
                self._write_notes.append(
                    f'[write_file] NOT written: "path" must be an absolute path, got '
                    f"{path!r}."
                )
            elif not isinstance(content, str):
                self._write_notes.append(
                    f'[write_file] {path} NOT written: "content" must be one JSON string '
                    "with the complete file."
                )
            else:
                self._pending_writes.append((path, content))

    async def _do_writes(self, environment) -> list[str]:
        """Carry out the ``write_file`` field of the current response; one line per file."""
        lines = list(self._write_notes)
        for path, content in self._pending_writes:
            index = len(self._writes) + 1
            text = content if content.endswith("\n") else content + "\n"
            local = self.logs_dir / "write_file" / f"{index:03d}.txt"
            record: dict[str, Any] = {
                "index": index,
                "episode_step_id": len(self._trajectory_steps) + 1,
                "path": path,
                "chars": len(text),
                "lines": text.count("\n"),
                "is_answer_file": path == self._answer_path,
                "ok": False,
            }
            try:
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_text(text)
                await environment.exec(
                    f"mkdir -p {shlex.quote(str(Path(path).parent))}", timeout_sec=10
                )
                await environment.upload_file(local, path)
                size = await environment.exec(
                    f"wc -c < {shlex.quote(path)}", timeout_sec=10
                )
                on_disk = (size.stdout or "").strip()
                record["ok"] = on_disk.isdigit() and int(on_disk) == len(
                    text.encode()
                )
                if not record["ok"]:
                    record["error"] = f"size on disk {on_disk!r}"
            except Exception as exc:
                record["error"] = repr(exc)
                self.logger.error(f"write_file {path} failed: {exc!r}")
            self._writes.append(record)
            if record["ok"]:
                last = next(
                    (ln for ln in reversed(text.splitlines()) if ln.strip()), ""
                )
                lines.append(
                    f"[write_file] {path} written by the harness: {record['lines']} "
                    f"lines, {record['chars']:,} characters; last line: {_clip(last, 200)}"
                )
            else:
                lines.append(
                    f"[write_file] {path} could NOT be written by the harness "
                    f"({record.get('error')}). Write it with a command instead."
                )
        self._pending_writes, self._write_notes = [], []
        return lines

    # ------------------------------------------------------------ completion

    async def _gate_claim(self, environment) -> None:
        """One verifier round: once it has run, a claim is accepted whatever the solver
        did with the report; before it, the solver is sent to the verifier."""
        answer = await self._read_answer(environment)
        h = _sha(answer)
        claim: dict[str, Any] = {
            "episode_step_id": len(self._trajectory_steps) + 1,
            "answer_hash": h[:16],
            "verified": h in self._passed_hashes,
            "rounds_run": self._verify_round,
        }
        if self._verify_round >= self._max_verify_rounds or self._verified_hash:
            if h in self._passed_hashes:
                reason = "verified"
            elif h != self._verified_hash:
                reason = "revised_after_report"
            else:
                reason = "unrevised_not_passed"
            claim["outcome"] = "accepted"
            self._finish_reason = reason
            self._pending_completion = True
        elif self._unverified_claims >= self._max_unverified_claims:
            claim["outcome"] = "accepted_unverified"
            self._finish_reason = "unverified_accepted"
            self._pending_completion = True
        else:
            self._unverified_claims += 1
            claim["outcome"] = "bounced"
            claim["reason"] = "the verifier has not run in this task"
            self._pending_completion = False
            self._gate_message = (
                f"{GATE_HEADER}\n"
                f"The verifier has not run in this task. Write the answer file "
                f"{self._answer_path} with all its sections, then respond with "
                '"subagent": {"name": "verifier"}, an empty "commands" array and no '
                f'"task_complete". (Claim {self._unverified_claims} of '
                f"{self._max_unverified_claims} that was not accepted; after that, "
                "task_complete is accepted as the file stands.)"
            )
        claim["finish_reason"] = (
            self._finish_reason if self._pending_completion else None
        )
        self._claims.append(claim)
        if self._pending_completion:
            self._final_answer = answer

    # -------------------------------------------------------------- verifier

    async def _verifier_call(
        self, session: TmuxSession, commands: list[Command], terminal_output: str
    ) -> str:
        self._pending_rebuttal = None
        if self._verify_round >= self._max_verify_rounds:
            self._n_free_calls += 1
            return (
                f"{VERIFIER_HEADER} ===\n"
                "The verifier has already run in this task. It runs once: the answer file "
                "is not checked again, whether or not it has changed. Its report was:\n"
                f"{self._previous_report_text}\n\n"
                f"{self._decision_instruction(0, passed=self._last_passed)}"
            )
        return await super()._verifier_call(session, commands, terminal_output)

    def _decision_instruction(
        self, rounds_left: int, verified: bool = True, passed: bool = True
    ) -> str:
        if passed:
            return (
                "Every rubric passed. The verifier does not run again. To finish: respond "
                'with "task_complete": true and an empty "commands" array.'
            )
        return (
            "The verifier runs once: this report is final and the answer file is not "
            "checked again. Decide on each finding yourself. Read the quoted rule text "
            "together with the conditions around it (ask the reader for the surrounding "
            "lines if they are not in front of you).\n"
            "- The finding holds: correct the answer file (recompute with python3 where "
            'numbers change, then send the complete new file in "write_file").\n'
            "- The finding does not hold: leave the file as it is.\n"
            "A rubric with NO REPORT produced nothing; nothing follows from it. A finding "
            "whose quote was not found in the rule document is a lead, not rule text. When "
            'you have decided on every finding, finish with "task_complete": true and an '
            'empty "commands" array.'
        )

    def _build_check_packet(
        self,
        skill: CheckSkill,
        index: int,
        answer: str,
        round_no: int,
        prev: dict[str, Any] | None,
        rebuttal: str | None,
        max_steps: int,
        trajectory_note: str,
    ) -> str:
        parts = [
            f"=== ANSWER FILE (contents of {self._answer_path}) ===",
            answer,
            "",
            "=== THIS CALL ===",
        ]
        if self._rule_inline:
            parts.append(
                "The task, the complete rule document and the answer file are printed "
                "above: do not run commands to read or search them again. Commands are only "
                f"for redoing arithmetic with python3 (at most {max_steps} command "
                "step(s)). Fill the table from the text above and report in your first reply "
                "if you can."
            )
        else:
            parts.append(
                f"Budget: {max_steps} command step(s). Report as soon as your table is filled."
            )
        if trajectory_note:
            parts.append(trajectory_note)
        return "\n".join(parts)

    def _render_round(
        self, round_no: int, outcomes: list[dict[str, Any]], passed: bool
    ) -> str:
        lines = [f"RUBRIC TABLE REPORT: {'PASSED' if passed else 'NOT PASSED'}"]
        for index, o in enumerate(outcomes, 1):
            status = {
                "PASS": "PASS",
                "FAIL": "FAIL",
                "UNSUPPORTED": "FAIL without a quote found in the rule document (leads "
                "only)",
                "NO_REPORT": f"NO REPORT ({o.get('reason') or 'no result'})",
            }[o["status"]]
            lines.append(f"[{index}] {o['label']}: {status}")
            lines.extend(self._findings_text(index, o.get("findings") or []))
            for note in (o.get("notes") or [])[:2]:
                lines.append(f"      Unverified note: {_clip(note, 300)}")
        return "\n".join(lines)

    # ------------------------------------------------------------ compaction

    def _context_estimate(self, extra_chars: int = 0) -> int:
        """Tokens of the solver's next request: the last prompt, the reply to it and the
        observation that follows (four characters to a token)."""
        return self._last_prompt_tokens + (self._last_reply_chars + extra_chars) // 4

    def _auto_compaction_due(self, extra_chars: int) -> bool:
        if self._context_window <= 0 or self._solver_chat is None:
            return False
        est = self._context_estimate(extra_chars)
        return est >= max(self._context_window, self._next_auto_at)

    def _write_compaction_record(self, rec: dict[str, Any]) -> None:
        out_dir = self.logs_dir / "compaction"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"call-{rec['call']}.json").write_text(json.dumps(rec, indent=2))
        except Exception as exc:
            self.logger.error(f"could not write the compaction record: {exc!r}")

    @staticmethod
    def _page_problem(result: dict) -> str | None:
        page = result.get("page")
        if not isinstance(page, str) or len(page.strip()) < 40:
            return '"page" must be a JSON string with the state page.'
        keep = result.get("keep_source")
        if keep is not None and not isinstance(keep, list):
            return '"keep_source" must be a list of {"lines": "a-b", "label": "..."}.'
        return None

    async def _compaction_call(
        self, session: TmuxSession, terminal_output: str, trigger: str
    ) -> str | None:
        """Rebuild the solver's messages around a state page. Returns the text for the
        solver's next message; None when an automatic attempt did nothing."""
        environment = session.environment
        chat = self._solver_chat
        self._compacted_this_step = True
        call_no = len(self._compactions) + 1
        est = self._context_estimate(len(terminal_output))
        keep = self._compaction_keep_turns
        started = time.time()
        rec: dict[str, Any] = {
            "call": call_no,
            "trigger": trigger,
            "episode_step_id": len(self._trajectory_steps) + 1,
            "context_window": self._context_window,
            "tokens_before_est": est,
            "solver_prompt_tokens_before": self._last_prompt_tokens,
            "applied": False,
        }
        header = f"{COMPACTION_HEADER} (call {call_no}) ==="

        def declined(outcome: str, why: str) -> str | None:
            rec.update({"outcome": outcome, "elapsed_sec": time.time() - started})
            self._compactions.append(rec)
            self._write_compaction_record(rec)
            # An automatic attempt is tried again only when the context has grown.
            self._next_auto_at = est + max(2000, self._context_window // 8)
            if trigger == "auto":
                return None
            return f"{header}\nNo compaction: {why} Your context is unchanged."

        messages = list(chat.messages) if chat is not None else []
        rec["messages_before"] = len(messages)
        turns = split_turns(messages)
        if turns is None or len(turns) < keep + 2:
            return declined(
                "nothing_to_remove",
                f"there are no steps to replace yet (the first message and the latest "
                f"{keep} steps always stay).",
            )
        if trigger == "solver" and est < self._compaction_min_tokens:
            return declined(
                "context_short",
                f"your context is about {est:,} tokens, and compaction is of use from "
                f"about {self._compaction_min_tokens:,}.",
            )

        n_removed = len(turns) - 1 - keep
        head = turns[0]
        removed = [m for turn in turns[1 : 1 + n_removed] for m in turn]
        kept = [m for turn in turns[1 + n_removed :] for m in turn]
        # The observation of this step follows the kept steps and is not a message yet.
        staying = head + kept + [{"role": "user", "content": terminal_output}]
        answer = await self._read_answer(environment)
        packet = (
            "=== THIS CALL ===\n"
            f"The conversation above has {len(turns)} steps. Your first message and "
            f"first reply stay, and so do your latest {keep} steps; the "
            f"{n_removed} step(s) between them are replaced by your page.\n"
            + (
                f"The answer file {self._answer_path} exists and is added by the harness.\n"
                if answer != NO_ANSWER
                else "There is no answer file yet.\n"
            )
            + (
                "The verifier has run; its report is added by the harness.\n"
                if self._verify_round
                else "The verifier has not run yet.\n"
            )
            + f"Rule-document lines that stay in your context anyway: "
            f"{ranges_text(self._ranges_in_context(staying))}."
        )
        clock = SubagentClock(
            None, False, False, max_output_tokens=self._compaction_max_output_tokens
        )
        run = await self._run_tool_subagent(
            environment,
            role="compaction",
            skill_text=BRANCH_HEADER.format(title="COMPACTION")
            + skill_body(self._compaction_skill_text),
            packet=packet,
            max_steps=1,
            closing=COMPACTION_CLOSING,
            clock=clock,
            validate=self._page_problem,
            lift=lambda reply: reply if isinstance(reply.get("page"), str) else None,
            history=[dict(m) for m in messages],
            allow_commands=False,
            begin=COMPACTION_CLOSING,
        )
        traj_name = f"trajectory.compaction-call-{call_no}.json"
        self._save_subagent_trajectory(
            run["session_id"],
            run["steps"],
            traj_name,
            run["chat"],
            agent_name=f"{self.name()}/compaction",
            extra={
                "parent_session_id": self._session_id,
                "compaction_call": call_no,
                "trigger": trigger,
                "skill_path": str(self._compaction_skill_path),
            },
        )
        rec.update(
            {
                "prompt_tokens": run["chat"].total_input_tokens,
                "completion_tokens": run["chat"].total_output_tokens,
                "n_llm_calls": run["n_calls"],
                "parse_failures": run["parse_failures"],
                "repairs": run["repairs"],
                "timed_out": run["timed_out"],
                "context_exceeded": run["context_exceeded"],
                "limit_reached": clock.limit_reached,
                "trajectory_file": traj_name,
            }
        )
        self._pending_subagent_steps.append(
            Step(
                step_id=len(self._trajectory_steps) + 1,  # re-assigned when appended
                timestamp=datetime.now(timezone.utc).isoformat(),
                source="system",
                message=f"Compaction call {call_no} ({trigger}).",
                observation=Observation(
                    results=[
                        ObservationResult(
                            subagent_trajectory_ref=[
                                SubagentTrajectoryRef(
                                    session_id=run["session_id"],
                                    trajectory_path=traj_name,
                                    extra={"summary": f"Compaction call {call_no}"},
                                )
                            ]
                        )
                    ]
                ),
            )
        )
        result = run["result"]
        if not isinstance(result, dict) or self._page_problem(result):
            return declined(
                "no_page",
                "no usable state page was produced"
                + (f" ({clock.describe_limit()})." if run["timed_out"] else "."),
            )
        page = result["page"].strip()

        # What the harness pins: named rule lines that would otherwise leave the context,
        # the answer file, the verifier's report.
        stay = self._ranges_in_context(staying)
        wanted = []
        for item in result.get("keep_source") or []:
            r = parse_range(item.get("lines")) if isinstance(item, dict) else None
            if r is not None and not covered(r[0], r[1], stay):
                wanted.append({"lines": f"{r[0]}-{r[1]}", "label": item.get("label")})
        pinned, extraction = await self._extract_excerpts(
            environment,
            {"excerpts": wanted},
            call_no,
            max_chars=self._compaction_source_max_chars,
            record=[],
            budget_owner="compaction's",
        )
        mark = f"{REBUILT_HEADER} (compaction {call_no}) ==="
        parts = [
            mark,
            f"The {n_removed} step(s) that followed your first reply were removed "
            "from this conversation to keep it short. The page below, which you wrote, "
            "stands in their place; the harness added the rule text you named, the answer "
            "file and the verifier's report as they are. Nothing in the environment "
            "changed.",
            "",
            "--- State page ---",
            page,
        ]
        pinned_ranges: list[tuple[int, int]] = []
        shown = [e for e in pinned if e.get("inserted")]
        if shown:
            parts += ["", "--- Rule text kept (verbatim) ---"]
            for e in shown:
                parts.append(
                    f"lines {e['lines']} of {e['file']}"
                    + (f" · {e['label']}" if e.get("label") else "")
                )
                parts.append(e["text"])
                r = parse_range(e.get("lines_shown") or e["lines"])
                if r and e["file"] == self._rule_path:
                    pinned_ranges.append(r)
        kept_text = "\n".join(message_text(m) for m in staying[len(head) :])
        if answer == NO_ANSWER:
            parts += ["", f"--- Answer file: {self._answer_path} does not exist yet ---"]
        elif answer in kept_text or any(
            # in a reply the file is the JSON string of "write_file"
            json.dumps(answer[:200], ensure_ascii=flag)[1:-1] in kept_text
            for flag in (True, False)
        ):
            parts += [
                "",
                f"--- Answer file: {self._answer_path} is as you wrote it in the steps "
                "below ---",
            ]
        else:
            parts += [
                "",
                f"--- Answer file ({self._answer_path}, as on disk now) ---",
                answer,
            ]
        if not self._verify_round:
            parts += ["", "--- Verifier: has not run yet ---"]
        elif VERIFIER_HEADER in kept_text:
            parts += ["", "--- Verifier: has run; its report is in the steps below ---"]
        else:
            parts += [
                "",
                "--- Verifier: has run (it runs once); its report ---",
                self._previous_report_text or "(no report text)",
            ]
        parts += ["", "=== END OF REBUILT CONTEXT; your latest steps follow ==="]
        block = "\n".join(parts)
        merged = dict(kept[0])
        merged["content"] = f"{block}\n\n{kept[0]['content']}"
        new_messages = head + [merged] + kept[1:]

        old_chars = sum(len(message_text(m)) for m in messages)
        new_chars = sum(len(message_text(m)) for m in new_messages)
        rec.update(
            {
                "page": page,
                "page_chars": len(page),
                "keep_source": result.get("keep_source"),
                "pinned": [{k: v for k, v in e.items() if k != "text"} for e in pinned],
                "pinned_chars": extraction["chars"],
                "steps_removed": n_removed,
                "chars_before": old_chars,
                "chars_after": new_chars,
                "messages_after": len(new_messages),
            }
        )
        if new_chars > (1 - self._compaction_min_saving) * old_chars:
            return declined(
                "not_smaller",
                "the rebuilt context would not be clearly shorter than the present one.",
            )

        out_dir = self.logs_dir / "compaction"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"call-{call_no}.context-before.json").write_text(
            json.dumps(messages, indent=2)
        )
        (out_dir / f"call-{call_no}.context-after.json").write_text(
            json.dumps(new_messages, indent=2)
        )
        (out_dir / f"call-{call_no}.page.txt").write_text(block)
        chat._messages = new_messages
        chat.reset_response_chain()
        self._delivered.append({"mark": mark, "ranges": pinned_ranges})
        est_after = int(est * new_chars / max(1, old_chars))
        self._next_auto_at = est_after + max(2000, self._context_window // 8)
        rec.update(
            {
                "applied": True,
                "outcome": "applied",
                "tokens_after_est": est_after,
                "elapsed_sec": time.time() - started,
            }
        )
        self._compactions.append(rec)
        self._write_compaction_record(rec)
        self._pending_compaction_record = rec
        why = (
            f"it had passed {self._context_window:,} tokens"
            if trigger == "auto"
            else "you asked for it"
        )
        return (
            f"{header}\n"
            f"Your context was rebuilt ({why}): the {n_removed} step(s) after your "
            f"first reply were replaced by a state page, placed before your latest {keep} "
            "steps. It holds your state, the rule text you named, your answer file and the "
            "verifier's report. Nothing in the environment changed. Continue the task."
        )

    # ---------------------------------------------------------------- helpers

    def _write_summary(self) -> None:
        super()._write_summary()
        path = self.logs_dir / "rubric_verifier" / "summary.json"
        try:
            data = json.loads(path.read_text()) if path.exists() else {}
            applied = [c for c in self._compactions if c.get("applied")]
            data.update(
                {
                    "harness": "srvc-v0.1",
                    "cost_budget_basis": self._cost_budget_basis,
                    "solver_rule_inline": self._solver_rule_inline,
                    "solver_rule_inline_max_chars": self._solver_rule_inline_max_chars,
                    "reader_survey": self._reader_survey,
                    "reader_survey_max_chars": self._reader_survey_max_chars,
                    "reader_inline_max_chars": self._reader_inline_max_chars,
                    "reader_kinds": [r.get("kind") for r in self._reader_calls],
                    "survey": (
                        {
                            "outline": self._survey["outline"],
                            "chars_inserted": self._survey["chars_inserted"],
                            "prompt_tokens": self._survey["prompt_tokens"],
                            "completion_tokens": self._survey["completion_tokens"],
                            "thinking_fallback": self._survey["thinking_fallback"],
                            "timed_out": self._survey["timed_out"],
                        }
                        if self._survey
                        else None
                    ),
                    "context_window": self._context_window,
                    "compaction_keep_turns": self._compaction_keep_turns,
                    "compaction_min_tokens": self._compaction_min_tokens,
                    "compaction_max_output_tokens": self._compaction_max_output_tokens,
                    "compaction_skill_path": str(self._compaction_skill_path),
                    "n_compaction_calls": len(self._compactions),
                    "n_compactions_applied": len(applied),
                    "compactions": [
                        {k: v for k, v in c.items() if k not in ("page", "pinned")}
                        for c in self._compactions
                    ],
                    "last_solver_prompt_tokens": self._last_prompt_tokens,
                    "n_json_diagnoses": self._n_json_diagnoses,
                    "n_write_file": len(self._writes),
                    "n_write_file_failed": sum(1 for w in self._writes if not w["ok"]),
                    "n_answer_file_writes": sum(
                        1 for w in self._writes if w["is_answer_file"] and w["ok"]
                    ),
                    "writes": self._writes,
                    "keep_thinking": self._keep_thinking,
                    "notes_handed_over": self._notes_handed_over,
                }
            )
            path.write_text(json.dumps(data, indent=2))
        except Exception as exc:
            self.logger.error(f"Failed to extend the summary: {exc}")
