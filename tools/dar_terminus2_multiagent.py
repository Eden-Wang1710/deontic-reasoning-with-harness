"""terminus-2 solver that calls a reader and a verifier sub-agent as tools (reader-verifier harness).

Design: claude_docs/09_reader_verifier_harness.md. This is version 1 of the reader-verifier-
compactor harness; there is no compactor here.

The solver is the run-3 solver-verifier agent (``Terminus2RubricVerifier``: t2fix base, index
packet, section-number answer record, no confidence field). Two things change:

1. **Sub-agents as tools.** The solver's JSON response may carry a top-level field, at the same
   level as ``task_complete``::

       "subagent": {"name": "reader", "query": "<what rule text you need>"}
       "subagent": {"name": "verifier"}

   Harbor's JSON parser ignores unknown top-level fields, so the stock parser and its auto-fixes
   are untouched; this subclass reads the field from the raw response. In one episode the
   commands run first (tmux, as always), then the sub-agent; the sub-agent's result is appended
   to that episode's terminal output as the next message to the solver. The **reader** runs in a
   fresh ``Chat`` with ``tools/skills/reader_v1/SKILL.md``, the task instruction, the solver's
   query and an index of what earlier reader calls returned; it may run ``reader_max_steps``
   command steps through ``environment.exec`` and returns line ranges of the rule file, which
   the harness extracts verbatim with ``sed -n`` (capped at ``reader_max_chars`` characters) and
   inserts into the result. The **verifier** is the run-3 verifier (``_run_verifier_round``) with
   skill v4: four rubrics (tool use, provision coverage, provision selection and application,
   grounded reasoning), aware that rule text reaches the solver through reader results.

2. **Completion gate.** ``task_complete`` is accepted only if the answer file's hash, taken after
   the episode's commands have run, equals the hash the verifier last saw; the trial then ends at
   once (no "Are you sure?" step). Otherwise the claim is bounced with a message asking for a
   verifier call; after ``max_unverified_claims`` bounces, or when no verifier rounds remain,
   the next claim is accepted and the finish reason is recorded. A response that calls a
   sub-agent and sets ``task_complete`` is treated as the call; the completion flag is ignored.

The harness holds no checking logic: it compares hashes, counts rounds and calls, extracts the
lines the reader names and renders results. Each role has a skill: the solver's
(``tools/skills/solver_v1/SKILL.md``, inserted into the stock terminus-2 template at ``{skill}``)
describes the two sub-agents, the finishing protocol and the answer-file format; the reader's and
the verifier's say what to select and what to check.

Agent kwargs (``--ak key=value``), in addition to the verifier agent's: ``solver_skill_path``
(tools/skills/solver_v1/SKILL.md), ``reader_skill_path`` (tools/skills/reader_v1/SKILL.md), ``reader_max_steps`` (3), ``reader_max_chars`` (16000),
``rule_path`` (/app/statute.txt, the default file for reader excerpts), ``max_unverified_claims``
(2). Defaults changed from the verifier agent: ``skill_path`` -> rubric_verifier_v4,
``derivation_record=true``, ``derivation_template`` -> terminus-json-multiagent-v1.txt,
``verifier_max_steps=4``, ``packet_mode=index``, ``report_confidence=false``.

Logs per trial: ``<trial>/agent/reader/call-N.json`` and ``call-N.packet.txt``,
``<trial>/agent/trajectory.reader-call-N.json``; the verifier's files as in run-3;
``<trial>/agent/rubric_verifier/summary.json`` extended with reader calls, claims and the
finish reason.

Usage (from ``harbor-deonticbench/``, with ``tools/`` on PYTHONPATH):

    harbor run --agent-import-path dar_terminus2_multiagent:Terminus2ReaderVerifier ...

``tools/run_dar_qwen35_35b.sh`` with ``AGENT=t2rv`` does this.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harbor.agents.terminus_2.terminus_2 import Command
from harbor.agents.terminus_2.tmux_session import TmuxSession
from harbor.llms.base import ContextLengthExceededError, LLMResponse
from harbor.llms.chat import Chat
from harbor.models.trajectories import (
    Agent,
    FinalMetrics,
    Observation,
    ObservationResult,
    Step,
    SubagentTrajectoryRef,
    Trajectory,
)
from harbor.utils.trajectory_utils import format_trajectory_json

from dar_terminus2_verifier import (
    MARKER_BYTES_RE,
    Terminus2RubricVerifier,
    _extract_json_object,
)

TOOLS_DIR = Path(__file__).resolve().parent
SOLVER_SKILL_PATH = TOOLS_DIR / "skills" / "solver_v1" / "SKILL.md"
READER_SKILL_PATH = TOOLS_DIR / "skills" / "reader_v1" / "SKILL.md"
VERIFIER_V4_SKILL_PATH = TOOLS_DIR / "skills" / "rubric_verifier_v4" / "SKILL.md"
MULTIAGENT_TEMPLATE_PATH = TOOLS_DIR / "templates" / "terminus-json-multiagent-v1.txt"

# Rubric set of skill v4 (four rubrics; run-3's five are in dar_terminus2_verifier.RUBRIC_LABELS).
RUBRIC_LABELS_V4 = {
    "tool_use": "Tool use",
    "provision_coverage": "Provision coverage",
    "provision_selection": "Provision selection and application",
    "grounded_reasoning": "Grounded reasoning",
}

# Per-command output cap for the reader's own tool calls (bytes); the reader reads narrow
# ranges and the extraction of what it returns is done by the harness, not through this cap.
READER_OUTPUT_LIMIT = 6000

# Headers of the blocks the harness appends to the solver's terminal output. Their presence
# switches off the 10,000-byte observation limit for that message (the terminal part was
# already limited before the block was appended) and lets the verifier's trajectory index
# recognise the step.
READER_HEADER = "=== READER RESULT"
VERIFIER_HEADER = "=== VERIFIER RESULT"
GATE_HEADER = "=== COMPLETION NOT ACCEPTED ==="
REJECTED_HEADER = "=== SUB-AGENT CALL REJECTED ==="
SUBAGENT_MARKS = (READER_HEADER, VERIFIER_HEADER, GATE_HEADER, REJECTED_HEADER)

READER_CALL_RE = re.compile(r"=== READER RESULT \(call (\d+) of this task\) ===")
VERIFIER_CALL_RE = re.compile(r"=== VERIFIER RESULT \(round (\d+) of (\d+)\) ===")
LINES_RE = re.compile(r"^\s*L?(\d+)\s*(?:-|–|—|:|,|to)\s*L?(\d+)\s*$")


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


class Terminus2ReaderVerifier(Terminus2RubricVerifier):
    """Run-3 solver-verifier agent whose solver calls the reader and the verifier as tools."""

    def __init__(
        self,
        *args,
        solver_skill_path: str | None = None,
        reader_skill_path: str | None = None,
        reader_max_steps: int | str = 3,
        reader_max_chars: int | str = 16000,
        rule_path: str = "/app/statute.txt",
        max_unverified_claims: int | str = 2,
        skill_path: str | None = None,
        derivation_record: bool | str = True,
        derivation_template: str | None = None,
        verifier_max_steps: int | str = 4,
        packet_mode: str = "index",
        report_confidence: bool | str = False,
        **kwargs,
    ):
        super().__init__(
            *args,
            skill_path=skill_path or str(VERIFIER_V4_SKILL_PATH),
            derivation_record=derivation_record,
            derivation_template=derivation_template or str(MULTIAGENT_TEMPLATE_PATH),
            verifier_max_steps=verifier_max_steps,
            packet_mode=packet_mode,
            report_confidence=report_confidence,
            **kwargs,
        )
        # The solver's skill fills the {skill} slot of the template; the template is
        # str.format-ed later, so braces in the skill are doubled.
        self._solver_skill_path = (
            Path(solver_skill_path) if solver_skill_path else SOLVER_SKILL_PATH
        )
        self._solver_skill_text = self._solver_skill_path.read_text()
        if "{skill}" not in self._prompt_template:
            raise ValueError("the solver prompt template has no {skill} placeholder")
        self._prompt_template = self._prompt_template.replace(
            "{skill}",
            self._solver_skill_text.strip().replace("{", "{{").replace("}", "}}"),
        )
        self._rubric_labels = dict(RUBRIC_LABELS_V4)
        self._reader_skill_path = (
            Path(reader_skill_path) if reader_skill_path else READER_SKILL_PATH
        )
        self._reader_skill_text = self._reader_skill_path.read_text()
        self._reader_max_steps = int(reader_max_steps)
        self._reader_max_chars = int(reader_max_chars)
        self._rule_path = rule_path
        self._max_unverified_claims = int(max_unverified_claims)

        # Per-episode state set in _handle_llm_interaction, consumed in _execute_commands.
        self._pending_subagent: dict[str, Any] | None = None
        self._episode_claim = False
        self._gate_message: str | None = None
        # Trajectory steps (system, with sub-agent refs) to insert before the next agent step.
        self._pending_subagent_steps: list[Step] = []
        # Completion gate.
        self._verified_hash: str | None = None
        self._unverified_claims = 0
        self._claims: list[dict[str, Any]] = []
        self._finish_reason = "not_finished"
        self._awaiting_decision = False
        # Reader.
        self._reader_calls: list[dict[str, Any]] = []
        self._reader_returned: list[dict[str, Any]] = []  # {call, file, lines, label}
        self._line_counts: dict[str, int | None] = {}
        self._reader_index: dict[
            int, str
        ] = {}  # call no -> "lines a-b, c-d" for the index
        # Bookkeeping.
        self._solver_rule_file_reads = 0
        self._subagent_call_errors: list[dict[str, Any]] = []

    @staticmethod
    def name() -> str:
        return "terminus-2-reader-verifier"

    # ------------------------------------------------------------------ hooks

    def _limit_output_length(self, output: str, max_bytes: int = 10000) -> str:
        # A message that carries a sub-agent block was built from an already-limited terminal
        # output plus the block; the block itself has its own budget and must not be cut.
        if any(mark in output for mark in SUBAGENT_MARKS):
            return output
        return super()._limit_output_length(output, max_bytes)

    async def _handle_llm_interaction(
        self,
        chat: Chat,
        prompt: str,
        logging_paths,
        original_instruction: str = "",
        session: TmuxSession | None = None,
    ):
        if original_instruction:
            self._original_instruction = original_instruction
        self._flush_pending_subagent_steps()
        # Skip Terminus2RubricVerifier's hook (completion-claim trigger); use the stock one.
        (
            commands,
            is_task_complete,
            feedback,
            analysis,
            plan,
            llm_response,
        ) = await super(Terminus2RubricVerifier, self)._handle_llm_interaction(
            chat, prompt, logging_paths, original_instruction, session
        )
        self._current_analysis = analysis or ""
        self._current_plan = plan or ""
        self._claimed_complete = False  # the base class's verifier trigger stays off
        self._episode_claim = False
        self._pending_subagent = None
        self._gate_message = None

        parse_error = bool(feedback and "ERROR:" in feedback)
        if parse_error:
            return commands, is_task_complete, feedback, analysis, plan, llm_response

        obj = _extract_json_object(llm_response.content) or {}
        sub = self._normalise_subagent(obj.get("subagent"))
        self._pending_subagent = sub
        self._episode_claim = bool(is_task_complete)
        if sub is not None and sub.get("error"):
            self._subagent_call_errors.append(
                {
                    "episode_step_id": len(self._trajectory_steps) + 1,
                    "error": sub["error"],
                }
            )

        rule_name = Path(self._rule_path).name
        if rule_name:
            self._solver_rule_file_reads += sum(
                1 for c in commands if rule_name in (c.keystrokes or "")
            )

        if self._awaiting_decision:
            self._awaiting_decision = False
            self._decisions.append(
                {
                    "after_round": self._verify_round,
                    "task_complete": bool(is_task_complete),
                    "n_commands": len(commands),
                    "subagent": (sub or {}).get("name")
                    if sub and not sub.get("error")
                    else None,
                    "episode_step_id": len(self._trajectory_steps) + 1,
                }
            )
        return commands, is_task_complete, feedback, analysis, plan, llm_response

    async def _execute_commands(
        self, commands: list[Command], session: TmuxSession
    ) -> tuple[bool, str]:
        # Skip Terminus2RubricVerifier's hook (it would run the verifier on a claim).
        timeout_occurred, terminal_output = await super(
            Terminus2RubricVerifier, self
        )._execute_commands(commands, session)
        environment = session.environment
        sub = self._pending_subagent
        self._pending_subagent = None
        self._gate_message = None

        block: str | None = None
        if sub is not None:
            if sub.get("error"):
                block = f"{REJECTED_HEADER}\n{sub['error']}"
            elif sub["name"] == "reader":
                block = await self._reader_call(session, sub["query"])
            else:
                block = await self._verifier_call(session, commands, terminal_output)
            if self._episode_claim:
                block += (
                    '\n\nNote: "task_complete" in this response was ignored because the '
                    "response also called a sub-agent. Read the result above and decide; to "
                    'finish, send "task_complete": true in a response with no "subagent".'
                )
                self._claims.append(
                    {
                        "episode_step_id": len(self._trajectory_steps) + 1,
                        "outcome": "ignored_subagent_call",
                        "subagent": sub.get("name"),
                        "rounds_run": self._verify_round,
                    }
                )
                # The loop will set pending and call _get_completion_confirmation_message.
                self._pending_completion = False

        if self._episode_claim and sub is None:
            await self._gate_claim(environment)

        if block is not None:
            terminal_output = (
                f"{terminal_output.rstrip()}\n\n{block}"
                if terminal_output.strip()
                else block
            )
        return timeout_occurred, terminal_output

    def _get_completion_confirmation_message(self, terminal_output: str) -> str:
        # Called by the loop on a task_complete that this class did not accept: either a
        # bounced claim (gate message set) or a claim made together with a sub-agent call
        # (the block in terminal_output already says the flag was ignored).
        if self._gate_message:
            return f"Current terminal state:\n{terminal_output}\n\n{self._gate_message}"
        return terminal_output

    # ------------------------------------------------------------ completion

    async def _gate_claim(self, environment) -> None:
        answer = await self._read_answer(environment)
        h = _sha(answer)
        verified = self._verified_hash is not None and h == self._verified_hash
        claim: dict[str, Any] = {
            "episode_step_id": len(self._trajectory_steps) + 1,
            "answer_hash": h[:16],
            "verified": verified,
            "rounds_run": self._verify_round,
        }
        rounds_left = self._max_verify_rounds - self._verify_round
        if verified:
            claim["outcome"] = "accepted"
            self._finish_reason = "verified"
            self._pending_completion = True
        elif rounds_left <= 0:
            claim["outcome"] = "accepted_budget_exhausted"
            self._finish_reason = "budget_exhausted"
            self._pending_completion = True
        elif self._unverified_claims >= self._max_unverified_claims:
            claim["outcome"] = "accepted_unverified"
            self._finish_reason = "unverified_accepted"
            self._pending_completion = True
        else:
            self._unverified_claims += 1
            claim["outcome"] = "bounced"
            self._pending_completion = False
            reason = (
                "no verifier round has run in this task"
                if self._verified_hash is None
                else "it changed after the last verifier report"
            )
            self._gate_message = (
                f"{GATE_HEADER}\n"
                f"The answer file {self._answer_path} has not been verified in its current "
                f'form ({reason}). "task_complete" is not accepted until the verifier has '
                'checked this version. Respond with "subagent": {"name": "verifier"}, an '
                'empty "commands" array and no "task_complete"; after the report, finish with '
                f'"task_complete": true if you do not change the file. {rounds_left} '
                f"verification round(s) remain. (Unverified claim {self._unverified_claims} of "
                f"{self._max_unverified_claims}; after that, task_complete is accepted as the "
                "file stands.)"
            )
        self._claims.append(claim)
        if self._pending_completion:
            self._final_answer = answer

    # -------------------------------------------------------------- verifier

    async def _verifier_call(
        self, session: TmuxSession, commands: list[Command], terminal_output: str
    ) -> str:
        if self._verify_round >= self._max_verify_rounds:
            return (
                f"{VERIFIER_HEADER} ===\n"
                f"No verification rounds remain for this task ({self._max_verify_rounds} of "
                f'{self._max_verify_rounds} used). "task_complete" with an empty "commands" '
                "array will now be accepted as the answer file stands."
            )
        self._verify_round += 1
        round_no = self._verify_round
        started = time.time()
        try:
            report_text = await self._run_verifier_round(
                session, commands, terminal_output
            )
        except Exception as exc:  # keep the trial alive; CancelledError passes
            self.logger.error(f"Rubric verifier round {round_no} failed: {exc!r}")
            report_text = (
                f"VERIFICATION REPORT (round {round_no} of {self._max_verify_rounds})\n"
                f"The verifier could not complete this round ({type(exc).__name__}). No "
                "findings are available."
            )
            self._rounds_log.append(
                {
                    "round": round_no,
                    "error": repr(exc),
                    "elapsed_sec": time.time() - started,
                }
            )
            # The answer the verifier would have seen counts as verified: the budget was spent.
            self._previous_answer = await self._read_answer(session.environment)
        # _run_verifier_round read the answer file and stored it in _previous_answer.
        self._verified_hash = _sha(self._previous_answer or "")
        if self._rounds_log and self._rounds_log[-1].get("round") == round_no:
            self._rounds_log[-1]["answer_hash"] = self._verified_hash[:16]
        if self._pending_verifier_step is not None:
            self._pending_subagent_steps.append(self._pending_verifier_step)
            self._pending_verifier_step = None
        self._awaiting_decision = True
        rounds_left = self._max_verify_rounds - round_no
        return (
            f"{VERIFIER_HEADER} (round {round_no} of {self._max_verify_rounds}) ===\n"
            f"{report_text}\n\n{self._decision_instruction(rounds_left)}"
        )

    def _decision_instruction(self, rounds_left: int) -> str:
        if rounds_left > 0:
            return (
                f"Decide how to proceed. {rounds_left} verification round(s) remain.\n"
                "To continue: address the items above. Ask the reader for rule text you lack, "
                "run commands for computations, and change the answer file only if text you "
                "received from the rule file or a computation you ran supports the change. A "
                "NO is a violation the verifier checked against the rule file; an UNKNOWN is "
                "something it could not verify, a lead rather than an instruction. If you "
                "change the answer file, call the verifier again before finishing.\n"
                'To finish: respond with "task_complete": true and an empty "commands" array. '
                "The answer file as it stands has been verified and will be accepted."
            )
        return (
            "This was the final verification round; no further verification will run. "
            '"task_complete": true with an empty "commands" array will be accepted as the '
            "answer file stands. If you still change the file, do it in a response without "
            '"task_complete", then finish.'
        )

    # ---------------------------------------------------------------- reader

    def _normalise_subagent(self, sub: Any) -> dict[str, Any] | None:
        if sub is None or sub is False or sub == "" or sub == {}:
            return None
        if isinstance(sub, str):
            sub = {"name": sub}
        if not isinstance(sub, dict):
            return {
                "error": '"subagent" must be an object such as {"name": "reader", "query": '
                '"..."} or {"name": "verifier"}; got ' + type(sub).__name__ + ". No "
                "sub-agent was called."
            }
        name = (
            str(sub.get("name") or sub.get("agent") or sub.get("tool") or "")
            .strip()
            .lower()
        )
        if name not in ("reader", "verifier"):
            return {
                "error": f'Unknown sub-agent {name!r}. Available: "reader" (with a '
                '"query") and "verifier" (no arguments). No sub-agent was called.'
            }
        if name == "reader":
            query = sub.get("query") or sub.get("question") or sub.get("request") or ""
            if not str(query).strip():
                return {
                    "error": 'The reader needs a non-empty "query": describe the rule, the '
                    "question or the facts you need rule text for. No sub-agent was called."
                }
            return {"name": "reader", "query": str(query).strip()}
        return {"name": "verifier"}

    def _build_reader_packet(self, query: str, call_no: int) -> str:
        earlier: list[str] = []
        by_call: dict[int, list[str]] = {}
        for r in self._reader_returned:
            by_call.setdefault(r["call"], []).append(
                f"lines {r['lines']}" + (f" ({r['label']})" if r.get("label") else "")
            )
        for c in sorted(by_call):
            earlier.append(f"call {c}: " + "; ".join(by_call[c]))
        return "\n".join(
            [
                "=== TASK ===",
                self._original_instruction.strip(),
                "",
                f"=== QUERY (from the solver, call {call_no} of this task) ===",
                query,
                "",
                "=== RETURNED EARLIER IN THIS TASK ===",
                "\n".join(earlier) if earlier else "nothing yet",
                "",
                "=== BUDGET ===",
                f"{self._reader_max_steps} command steps, {READER_OUTPUT_LIMIT:,} bytes of "
                f"output per command, {self._reader_max_chars:,} characters of excerpts in "
                f'total. Ranges without a "file" refer to {self._rule_path}.',
            ]
        )

    async def _reader_call(self, session: TmuxSession, query: str) -> str:
        environment = session.environment
        call_no = len(self._reader_calls) + 1
        started = time.time()
        packet = self._build_reader_packet(query, call_no)

        rchat = Chat(self._llm, interleaved_thinking=self._interleaved_thinking)
        rsteps: list[Step] = []
        session_id = str(uuid.uuid4())
        prompt = (
            self._reader_skill_text
            + "\n\n"
            + packet
            + "\n\nBegin. Respond with exactly one JSON object as described in the skill."
        )
        rsteps.append(self._user_step(rsteps, prompt))

        result: dict | None = None
        commands_run: list[dict[str, str]] = []
        parse_failures = 0
        steps_used = 0
        context_exceeded = False
        n_calls = 0

        async def call(p: str) -> LLMResponse:
            nonlocal n_calls
            t0 = time.time()
            resp = await rchat.chat(p, **self._llm_call_kwargs)
            self._track_api_request_time(t0)
            self._update_subagent_metrics(resp.usage)
            self._collect_subagent_rollout_detail(resp)
            n_calls += 1
            return resp

        while steps_used < self._reader_max_steps:
            try:
                resp = await call(prompt)
            except ContextLengthExceededError:
                context_exceeded = True
                break
            parsed = _extract_json_object(resp.content)
            if parsed is None:
                parse_failures += 1
                steps_used += 1
                observation = (
                    "Your response could not be parsed as a single JSON object. Reply with "
                    'exactly one JSON object with the fields "analysis", "commands" and '
                    '"result". A common cause is a backslash that JSON does not allow, such '
                    "as grep 'a\\|b': use grep -E 'a|b'."
                )
                self._append_verifier_step(rsteps, resp, [], observation)
                prompt = observation
                continue
            if isinstance(parsed.get("result"), dict):
                result = parsed["result"]
                self._append_verifier_step(rsteps, resp, [], None)
                break
            cmds = parsed.get("commands") or []
            if not isinstance(cmds, list):
                cmds = [str(cmds)]
            cmds = [str(c) for c in cmds if str(c).strip()]
            if not cmds:
                steps_used += 1
                observation = (
                    'No commands and no result. Either run commands or fill in the "result" '
                    "field."
                )
                self._append_verifier_step(rsteps, resp, [], observation)
                prompt = observation
                continue
            steps_used += 1
            outputs = []
            for c in cmds:
                out = await self._exec_capped(environment, c, READER_OUTPUT_LIMIT)
                commands_run.append({"step": steps_used, "command": c, "output": out})
                outputs.append(f"$ {c}\n{out}")
            observation = (
                "\n\n".join(outputs)
                + f"\n\n[reader: {steps_used} of {self._reader_max_steps} command steps used]"
            )
            self._append_verifier_step(rsteps, resp, cmds, observation)
            prompt = observation

        if result is None and not context_exceeded:
            closing = (
                "You have used all command steps. Reply now with exactly one JSON object "
                'whose "result" field is filled in from the lines you have already displayed: '
                '"excerpts" (each with "lines", "label", "why") and "unresolved"; '
                '"commands": [].'
            )
            retry = (
                'Your reply could not be parsed as one JSON object whose "result" is an '
                "object. Check the brackets and reply once more with exactly that JSON object "
                "and nothing else."
            )
            prompt_text: str | None = closing
            for attempt in range(2):
                try:
                    resp = await call(closing if attempt == 0 else retry)
                except ContextLengthExceededError:
                    context_exceeded = True
                    break
                parsed = _extract_json_object(resp.content)
                if parsed and isinstance(parsed.get("result"), dict):
                    result = parsed["result"]
                    self._append_verifier_step(
                        rsteps, resp, [], None, prompt_text=prompt_text
                    )
                    break
                parse_failures += 1
                self._append_verifier_step(
                    rsteps, resp, [], retry, prompt_text=prompt_text
                )
                prompt_text = None

        excerpts, extraction = await self._extract_excerpts(
            environment, result, call_no
        )
        block = self._render_reader_result(
            call_no, query, result, excerpts, extraction, steps_used, context_exceeded
        )
        elapsed = time.time() - started

        traj_name = f"trajectory.reader-call-{call_no}.json"
        self._save_subagent_trajectory(
            session_id,
            rsteps,
            traj_name,
            rchat,
            agent_name="terminus-2-reader-verifier/reader",
            extra={
                "parent_session_id": self._session_id,
                "reader_call": call_no,
                "skill_path": str(self._reader_skill_path),
            },
        )
        record = {
            "call": call_no,
            "query": query,
            "result": result,
            "excerpts": excerpts,
            "chars_inserted": extraction["chars"],
            "truncated": extraction["truncated"],
            "not_inserted": extraction["not_inserted"],
            "invalid": extraction["invalid"],
            "result_text": block,
            "steps_used": steps_used,
            "parse_failures": parse_failures,
            "context_exceeded": context_exceeded,
            "commands": commands_run,
            "packet_chars": len(packet),
            "prompt_tokens": rchat.total_input_tokens,
            "completion_tokens": rchat.total_output_tokens,
            "n_llm_calls": n_calls,
            "elapsed_sec": elapsed,
            "trajectory_file": traj_name,
        }
        self._reader_calls.append(record)
        out_dir = self.logs_dir / "reader"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"call-{call_no}.json").write_text(json.dumps(record, indent=2))
        (out_dir / f"call-{call_no}.packet.txt").write_text(packet)

        ranges = ", ".join(e["lines"] for e in excerpts if e.get("inserted"))
        self._reader_index[call_no] = ranges or "nothing inserted"
        self._pending_subagent_steps.append(
            Step(
                step_id=len(self._trajectory_steps) + 1,  # re-assigned when appended
                timestamp=datetime.now(timezone.utc).isoformat(),
                source="system",
                message=(
                    f"Reader call {call_no} ({steps_used} command steps, {elapsed:.0f} s); "
                    "its result is part of the previous observation."
                ),
                observation=Observation(
                    results=[
                        ObservationResult(
                            subagent_trajectory_ref=[
                                SubagentTrajectoryRef(
                                    session_id=session_id,
                                    trajectory_path=traj_name,
                                    extra={
                                        "summary": f"Reader call {call_no}: lines "
                                        f"{ranges or 'none'}"
                                    },
                                )
                            ]
                        )
                    ]
                ),
            )
        )
        return block

    async def _exec_capped(self, environment, command: str, limit: int) -> str:
        try:
            result = await environment.exec(command, timeout_sec=60)
        except Exception as exc:  # timeouts and transport errors become observations
            return f"[command failed to run: {type(exc).__name__}: {exc}]"
        out = result.stdout or ""
        if result.stderr:
            out = f"{out}\n{result.stderr}" if out else result.stderr
        if result.return_code not in (0, None):
            out = f"{out}\n[exit code {result.return_code}]"
        return self._limit_output_length(out, limit) if out else "(no output)"

    async def _line_count(self, environment, path: str) -> int | None:
        if path in self._line_counts:
            return self._line_counts[path]
        count: int | None = None
        try:
            result = await environment.exec(
                f"test -f {shlex.quote(path)} && wc -l < {shlex.quote(path)}",
                timeout_sec=10,
            )
            if (
                result.return_code in (0, None)
                and (result.stdout or "").strip().isdigit()
            ):
                count = int(result.stdout.strip())
                # wc -l counts newlines; a final line without one is still a line.
                tail = await environment.exec(
                    f"tail -c 1 {shlex.quote(path)} | od -An -c", timeout_sec=10
                )
                if "\\n" not in (tail.stdout or ""):
                    count += 1
        except Exception as exc:
            self.logger.debug(f"line count of {path} failed: {exc!r}")
        self._line_counts[path] = count
        return count

    async def _extract_excerpts(
        self, environment, result: dict | None, call_no: int
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Validate the reader's ranges mechanically and extract the lines verbatim."""
        extraction: dict[str, Any] = {
            "chars": 0,
            "truncated": [],
            "not_inserted": [],
            "invalid": [],
        }
        excerpts: list[dict[str, Any]] = []
        if not isinstance(result, dict):
            return excerpts, extraction
        raw = result.get("excerpts")
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list):
            if raw not in (None, "", []):
                extraction["invalid"].append(
                    {"index": 0, "reason": '"excerpts" is not a list'}
                )
            return excerpts, extraction

        remaining = self._reader_max_chars
        for i, ex in enumerate(raw, 1):
            if not isinstance(ex, dict):
                extraction["invalid"].append(
                    {"index": i, "reason": "excerpt is not an object"}
                )
                continue
            file = str(ex.get("file") or self._rule_path).strip()
            label = str(ex.get("label") or "").strip()
            why = str(ex.get("why") or ex.get("relevance") or "").strip()
            lines_raw = str(ex.get("lines") or ex.get("range") or "").strip()
            m = LINES_RE.match(lines_raw)
            if lines_raw.isdigit():
                m = LINES_RE.match(f"{lines_raw}-{lines_raw}")
            entry: dict[str, Any] = {
                "index": i,
                "file": file,
                "lines": lines_raw,
                "label": label,
                "why": why,
                "inserted": False,
            }
            if not m:
                entry["reason"] = f'range {lines_raw!r} is not of the form "a-b"'
                extraction["invalid"].append({"index": i, "reason": entry["reason"]})
                excerpts.append(entry)
                continue
            a, b = int(m.group(1)), int(m.group(2))
            if a < 1 or b < a:
                entry["reason"] = f"range {a}-{b} is empty or starts before line 1"
                extraction["invalid"].append({"index": i, "reason": entry["reason"]})
                excerpts.append(entry)
                continue
            n_lines = await self._line_count(environment, file)
            if n_lines is None:
                entry["reason"] = f"{file} is not a readable file"
                extraction["invalid"].append({"index": i, "reason": entry["reason"]})
                excerpts.append(entry)
                continue
            if a > n_lines:
                entry["reason"] = (
                    f"range starts after the last line ({n_lines}) of {file}"
                )
                extraction["invalid"].append({"index": i, "reason": entry["reason"]})
                excerpts.append(entry)
                continue
            b = min(b, n_lines)
            entry["lines"] = f"{a}-{b}"
            if remaining <= 0:
                entry["reason"] = "character budget exhausted"
                extraction["not_inserted"].append({"index": i, "lines": entry["lines"]})
                excerpts.append(entry)
                continue
            try:
                res = await environment.exec(
                    f"sed -n '{a},{b}p' {shlex.quote(file)}", timeout_sec=30
                )
                text = (res.stdout or "").rstrip("\n")
            except Exception as exc:
                entry["reason"] = f"extraction failed: {type(exc).__name__}"
                extraction["invalid"].append({"index": i, "reason": entry["reason"]})
                excerpts.append(entry)
                continue
            if len(text) > remaining:
                cut = text.rfind("\n", 0, remaining)
                cut = cut if cut > 0 else remaining
                shown = text[:cut]
                shown_lines = shown.count("\n") + 1 if shown else 0
                omitted = (b - a + 1) - shown_lines
                text = (
                    shown
                    + f"\n[excerpt truncated by the harness: lines {a}-{a + shown_lines - 1} "
                    f"shown, {omitted} line(s) omitted; the reader's character budget is spent]"
                )
                entry["truncated"] = True
                entry["lines_shown"] = f"{a}-{a + shown_lines - 1}"
                extraction["truncated"].append(
                    {"index": i, "lines_shown": entry["lines_shown"]}
                )
                remaining = 0
            else:
                remaining -= len(text)
            entry["text"] = text
            entry["inserted"] = True
            extraction["chars"] += len(text)
            excerpts.append(entry)
            self._reader_returned.append(
                {
                    "call": call_no,
                    "file": file,
                    "lines": entry.get("lines_shown", entry["lines"]),
                    "label": label,
                }
            )
        return excerpts, extraction

    def _render_reader_result(
        self,
        call_no: int,
        query: str,
        result: dict | None,
        excerpts: list[dict[str, Any]],
        extraction: dict[str, Any],
        steps_used: int,
        context_exceeded: bool,
    ) -> str:
        header = f"{READER_HEADER} (call {call_no} of this task) ==="
        lines = [header, f"Query: {query}", ""]
        if result is None:
            reason = (
                "the reader's context limit was exceeded"
                if context_exceeded
                else "the reader did not return a result within its step budget"
            )
            lines.append(
                f"No result: {reason}. Nothing was inserted. You may call the reader again, "
                "with a narrower query."
            )
            lines.append(f"({steps_used} command steps used)")
            return "\n".join(lines)

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
            lines.append("No rule text was inserted.")
            lines.append("")
        skipped = [
            e
            for e in excerpts
            if not e.get("inserted") and "budget" in e.get("reason", "")
        ]
        if skipped:
            lines.append("Not inserted (character budget exhausted):")
            for e in skipped:
                lines.append(
                    f"- [{e['index']}] lines {e['lines']} of {e['file']}"
                    + (f" · {e['label']}" if e.get("label") else "")
                )
            lines.append("")
        invalid = [
            e
            for e in excerpts
            if not e.get("inserted") and "budget" not in e.get("reason", "")
        ]
        if invalid or extraction["invalid"]:
            lines.append("Invalid excerpts (not inserted):")
            seen = set()
            for e in invalid:
                seen.add(e["index"])
                lines.append(
                    f"- [{e['index']}] {e.get('lines') or '?'}: {e.get('reason')}"
                )
            for inv in extraction["invalid"]:
                if inv["index"] not in seen:
                    lines.append(f"- [{inv['index']}]: {inv['reason']}")
            lines.append("")
        unresolved = result.get("unresolved") or []
        if isinstance(unresolved, str):
            unresolved = [unresolved]
        if isinstance(unresolved, list) and unresolved:
            lines.append("Unresolved (per the reader):")
            lines.extend(f"- {u}" for u in unresolved)
            lines.append("")
        trunc = extraction["truncated"]
        trunc_text = (
            "nothing truncated"
            if not trunc
            else "excerpt "
            + ", ".join(
                f"[{t['index']}] cut after line {t['lines_shown'].split('-')[1]}"
                for t in trunc
            )
        )
        lines.append(
            f"({len(inserted)} excerpt(s) inserted, {extraction['chars']:,} characters, "
            f"{trunc_text}; {steps_used} command steps used)"
        )
        return "\n".join(lines)

    # ------------------------------------------------- verifier packet builders

    def _subagent_flag(self, obs: str) -> str:
        flags = []
        for m in READER_CALL_RE.finditer(obs):
            n = int(m.group(1))
            flags.append(f"reader call {n}: lines {self._reader_index.get(n, '?')}")
        for m in VERIFIER_CALL_RE.finditer(obs):
            flags.append(f"verifier round {m.group(1)}")
        if GATE_HEADER in obs:
            flags.append("task_complete claimed, not accepted (unverified)")
        if REJECTED_HEADER in obs:
            flags.append("sub-agent call rejected")
        return (" | " + "; ".join(flags)) if flags else ""

    @staticmethod
    def _index_line_text(n: int, cmds: list[str], obs: str, flag: str) -> str:
        # The size reported is that of the terminal part, before any sub-agent block.
        terminal = obs
        for mark in SUBAGENT_MARKS:
            pos = terminal.find(mark)
            if pos != -1:
                terminal = terminal[:pos]
        size = len(terminal.encode("utf-8"))
        m = MARKER_BYTES_RE.search(terminal)
        trunc = f", truncated ({m.group(1)} bytes omitted)" if m else ""
        cmd_text = "; ".join(c for c in cmds if c) or "(no commands)"
        return f"step {n} | {cmd_text} | terminal output {size} bytes{trunc}{flag}"

    def _build_index_packet(
        self, commands: list[Command], terminal_output: str, answer: str, round_no: int
    ) -> str:
        from dar_terminus2_verifier import CONTAINER_TRAJECTORY_PATH

        parts = [
            "=== TASK INSTRUCTION ===",
            self._original_instruction.strip(),
            "",
            f"=== ANSWER FILE (contents of {self._answer_path}) ===",
            answer,
            "",
            "=== SOLVER TRAJECTORY INDEX ===",
            f"Full trajectory (every analysis, command, terminal output and sub-agent result "
            f"exactly as the solver saw them) is at {CONTAINER_TRAJECTORY_PATH}; steps are "
            "headed '--- step N ---'. Read it with grep -n / sed -n as needed. A step marked "
            "'reader call N' contains, after its terminal output, a block headed "
            f"'{READER_HEADER} (call N of this task) ===' with the verbatim rule-file lines the "
            "solver received (each excerpt headed '[k] lines a-b of <file>'). A step marked "
            "'verifier round N' contains your previous report.",
        ]
        n = 0
        for step in self._trajectory_steps:
            if step.source != "agent":
                continue
            n += 1
            cmds = [
                tc.arguments.get("keystrokes", "").strip().splitlines()[0][:100]
                if tc.arguments.get("keystrokes", "").strip()
                else ""
                for tc in (step.tool_calls or [])
                if tc.function_name == "bash_command" and isinstance(tc.arguments, dict)
            ]
            obs = ""
            if step.observation and step.observation.results:
                obs = "\n".join(
                    r.content for r in step.observation.results if r.content
                )
            parts.append(self._index_line_text(n, cmds, obs, self._subagent_flag(obs)))
        n += 1
        cmds = [
            c.keystrokes.strip().splitlines()[0][:100]
            for c in commands
            if c.keystrokes.strip()
        ]
        parts.append(
            self._index_line_text(
                n, cmds, terminal_output, f" | verifier round {round_no} called"
            )
        )
        parts.append("")
        parts.append(
            f"=== VERIFICATION ROUND {round_no} of {self._max_verify_rounds} ==="
        )
        if round_no > 1 and self._previous_report_text:
            changed = "unknown"
            if self._previous_answer is not None:
                changed = "yes" if answer != self._previous_answer else "no"
            parts.append(f"Answer file changed since the previous round: {changed}")
            parts.append("Previous report:")
            parts.append(self._previous_report_text)
        return "\n".join(parts)

    def _build_packet(
        self, commands: list[Command], terminal_output: str, answer: str, round_no: int
    ) -> str:
        """Full trajectory text (uploaded to the container for the verifier to read)."""
        parts = [
            "=== TASK INSTRUCTION ===",
            self._original_instruction.strip(),
            "",
            f"=== CANDIDATE ANSWER (contents of {self._answer_path}) ===",
            answer,
            "",
        ]
        steps_text: list[str] = []
        n = 0
        for step in self._trajectory_steps:
            if step.source == "agent":
                n += 1
                steps_text.append(self._format_agent_step(n, step))
            elif step.source == "user" and step.step_id != 1:
                steps_text.append(f"--- message to solver ---\n{step.message}")
        n += 1
        current = [f"--- step {n} (verifier call) ---"]
        if self._current_analysis:
            current.append(f"[analysis] {self._current_analysis}")
        if self._current_plan:
            current.append(f"[plan] {self._current_plan}")
        if commands:
            current.append("[commands]")
            current.extend(f"$ {c.keystrokes.rstrip()}" for c in commands)
        current.append("[observation]")
        current.append(terminal_output.rstrip() or "(empty)")
        current.append(f"[verifier round {round_no} called]")
        steps_text.append("\n".join(current))
        parts.append(f"=== SOLVER TRAJECTORY ({n} steps) ===")
        parts.extend(steps_text)
        parts.append("")
        parts.append(
            f"=== VERIFICATION ROUND {round_no} of {self._max_verify_rounds} ==="
        )
        if round_no > 1 and self._previous_report_text:
            changed = "unknown"
            if self._previous_answer is not None:
                changed = "yes" if answer != self._previous_answer else "no"
            parts.append(f"Answer changed since the previous round: {changed}")
            if self._previous_answer is not None:
                parts.append(f"Previous answer: {self._previous_answer}")
            parts.append("Previous report:")
            parts.append(self._previous_report_text)
        return "\n".join(parts)

    # ---------------------------------------------------------------- helpers

    def _flush_pending_subagent_steps(self) -> None:
        if self._pending_verifier_step is not None:
            self._pending_subagent_steps.append(self._pending_verifier_step)
            self._pending_verifier_step = None
        for step in self._pending_subagent_steps:
            step.step_id = len(self._trajectory_steps) + 1
            self._trajectory_steps.append(step)
        self._pending_subagent_steps = []

    def _save_subagent_trajectory(
        self,
        session_id: str,
        steps: list[Step],
        filename: str,
        chat: Chat,
        agent_name: str,
        extra: dict[str, Any],
    ) -> None:
        trajectory = Trajectory(
            session_id=session_id,
            agent=Agent(
                name=agent_name,
                version=self.version() or "unknown",
                model_name=self._model_name,
                extra=extra,
            ),
            steps=steps,
            final_metrics=FinalMetrics(
                total_prompt_tokens=chat.total_input_tokens,
                total_completion_tokens=chat.total_output_tokens,
                total_cached_tokens=chat.total_cache_tokens,
                total_cost_usd=chat.total_cost if chat.total_cost > 0 else None,
            ),
        )
        path = self.logs_dir / filename
        try:
            path.write_text(format_trajectory_json(trajectory.to_json_dict()))
        except Exception as exc:
            self.logger.error(f"Failed to save sub-agent trajectory {path}: {exc}")

    def _write_summary(self) -> None:
        super()._write_summary()
        path = self.logs_dir / "rubric_verifier" / "summary.json"
        try:
            data = json.loads(path.read_text()) if path.exists() else {}
            final_hash = (
                _sha(self._final_answer) if self._final_answer is not None else None
            )
            data.update(
                {
                    "harness": "reader-verifier-v1",
                    "solver_skill_path": str(self._solver_skill_path),
                    "rubric_names": list(self._rubric_labels),
                    "reader_skill_path": str(self._reader_skill_path),
                    "reader_max_steps": self._reader_max_steps,
                    "reader_max_chars": self._reader_max_chars,
                    "rule_path": self._rule_path,
                    "max_unverified_claims": self._max_unverified_claims,
                    "n_reader_calls": len(self._reader_calls),
                    "reader_calls": [
                        {
                            k: v
                            for k, v in r.items()
                            if k
                            not in ("commands", "result_text", "excerpts", "result")
                        }
                        | {
                            "excerpts": [
                                {k: v for k, v in e.items() if k != "text"}
                                for e in r.get("excerpts", [])
                            ]
                        }
                        for r in self._reader_calls
                    ],
                    "reader_prompt_tokens": sum(
                        r["prompt_tokens"] for r in self._reader_calls
                    ),
                    "reader_completion_tokens": sum(
                        r["completion_tokens"] for r in self._reader_calls
                    ),
                    "reader_seconds": sum(r["elapsed_sec"] for r in self._reader_calls),
                    "claims": self._claims,
                    "unverified_claims": self._unverified_claims,
                    "finish_reason": self._finish_reason,
                    "verified_hash": self._verified_hash[:16]
                    if self._verified_hash
                    else None,
                    "final_answer_hash": final_hash[:16] if final_hash else None,
                    "final_answer_verified": (
                        None
                        if final_hash is None or self._verified_hash is None
                        else final_hash == self._verified_hash
                    ),
                    "solver_rule_file_reads": self._solver_rule_file_reads,
                    "subagent_call_errors": self._subagent_call_errors,
                    "completed_via_confirmation": self._finish_reason != "not_finished",
                    "subagent_prompt_tokens": self._subagent_metrics.total_prompt_tokens,
                    "subagent_completion_tokens": self._subagent_metrics.total_completion_tokens,
                }
            )
            # The base fields named "verifier_*" now include the reader; keep them but say so.
            data["verifier_prompt_tokens_note"] = (
                "verifier_prompt_tokens / verifier_completion_tokens cover both sub-agents; "
                "reader_* fields give the reader's share"
            )
            path.write_text(json.dumps(data, indent=2))
        except Exception as exc:
            self.logger.error(f"Failed to extend the summary: {exc}")
