"""terminus-2 with a rubric-based verifier sub-agent consulted at task completion.

Design (claude_docs/10_rubric_verifier.md). Stock terminus-2 handles ``task_complete`` in two
steps: the first claim puts the agent in a *pending* state and sends back the "Are you sure
you want to mark the task as complete?" confirmation message; a second ``task_complete``
ends the trial, running any commands in that response first. This subclass keeps that
protocol untouched and only changes *what the confirmation message says*:

1. When the solver claims completion (first ``task_complete`` of an attempt) and the
   verification budget is not exhausted, a **verifier sub-agent** is run in a fresh
   ``Chat`` with the same model. It receives ``tools/skills/rubric_verifier/SKILL.md``, the
   task instruction, the candidate answer file and the solver's trajectory (analyses, plans,
   commands, observations exactly as the solver saw them; the solver's ``reasoning_content``
   only with ``include_reasoning=true``). It may run shell commands in the task container
   through ``environment.exec`` (non-interactive, so the solver's tmux pane is untouched),
   up to ``verifier_max_steps`` steps, and returns a structured report.
2. The report, rendered to text, replaces the "Are you sure?" message, followed by a fixed
   decision instruction: continue (run commands, do not set ``task_complete``) or finish
   (``task_complete: true``, empty ``commands``, plus a top-level ``"confidence"`` field).
3. At most ``max_verify_rounds`` verifier rounds per trial (default 3). After the last
   round, or if the solver claims completion again once the budget is spent, the message
   says so and asks it to finish with a confidence.
4. If the trial ends through the normal confirmation path and no confidence was parsed from
   the final response, one extra turn asks for it.

Nothing in the harness inspects the report's content; what the verifier checks and how it
phrases it is entirely the skill's business. The harness only counts rounds, passes
messages and logs. Per-round logs go to ``<trial>/agent/rubric_verifier/round-N.json`` and
``<trial>/agent/trajectory.verifier-round-N.json`` (ATIF), with a ``summary.json`` at the
end; the verifier's tokens are added to the trial's totals through the subagent metrics.

Agent kwargs (``--ak key=value``): ``max_verify_rounds`` (3), ``verifier_max_steps`` (8),
``skill_path`` (tools/skills/rubric_verifier/SKILL.md), ``include_reasoning`` (false),
``answer_path`` (/app/output/answer.txt, DAR's convention in all four subdomains).

Usage (from ``harbor-deonticbench/``, with ``tools/`` on PYTHONPATH):

    harbor run --agent-import-path dar_terminus2_verifier:Terminus2RubricVerifier ...

``tools/run_dar_qwen35_35b.sh`` with ``AGENT=t2verify`` does this.
"""

from __future__ import annotations

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
    Metrics,
    Observation,
    ObservationResult,
    Step,
    SubagentTrajectoryRef,
    ToolCall,
    Trajectory,
)
from harbor.utils.trajectory_utils import format_trajectory_json

from dar_terminus2_local import Terminus2TruthfulMarker

DEFAULT_SKILL_PATH = (
    Path(__file__).resolve().parent / "skills" / "rubric_verifier" / "SKILL.md"
)

RUBRIC_LABELS = {
    "tool_call_integrity": "Tool-call integrity",
    "statute_coverage": "Statute coverage",
    "grounded_reasoning": "Grounded reasoning",
    "provision_selection": "Provision selection and application",
    "tool_assisted_computation": "Tool-assisted computation",
}

# Per-command output cap for the verifier's own tool calls (bytes). Smaller than the
# solver's 10,000-byte observation limit because the verifier's context also carries the
# whole solver trajectory; the skill tells it to read narrow ranges.
VERIFIER_OUTPUT_LIMIT = 6000

CONFIDENCE_FIELD_RE = re.compile(
    r'"confidence"\s*:\s*"?\s*(\d{1,3}(?:\.\d+)?)\s*%?\s*"?', re.IGNORECASE
)
CONFIDENCE_LINE_RE = re.compile(r"confidence\s*[:=]\s*(\d{1,3}(?:\.\d+)?)\s*%?", re.IGNORECASE)


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_confidence(text: str) -> float | None:
    """Return a confidence in [0, 1] from a JSON field or a 'Confidence: NN%' line."""
    for regex in (CONFIDENCE_FIELD_RE, CONFIDENCE_LINE_RE):
        m = regex.search(text or "")
        if m:
            value = float(m.group(1))
            if 0 <= value <= 100:
                return value / 100.0 if value > 1 or "%" in m.group(0) else value
    return None


def _extract_json_object(text: str) -> dict | None:
    """Parse the first balanced top-level JSON object in ``text`` (tolerates prose around it)."""
    if not text:
        return None
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
                    return obj if isinstance(obj, dict) else None
        start = text.find("{", start + 1)
    return None


class Terminus2RubricVerifier(Terminus2TruthfulMarker):
    """Terminus2 (truthful marker) whose completion confirmation is a verifier report."""

    def __init__(
        self,
        *args,
        max_verify_rounds: int | str = 3,
        verifier_max_steps: int | str = 8,
        skill_path: str | None = None,
        include_reasoning: bool | str = False,
        answer_path: str = "/app/output/answer.txt",
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._max_verify_rounds = int(max_verify_rounds)
        self._verifier_max_steps = int(verifier_max_steps)
        self._include_reasoning = _truthy(include_reasoning)
        self._answer_path = answer_path
        self._skill_path = Path(skill_path) if skill_path else DEFAULT_SKILL_PATH
        self._skill_text = self._skill_path.read_text()

        self._verify_round = 0
        self._claimed_complete = False
        self._current_analysis = ""
        self._current_plan = ""
        self._pending_report_text: str | None = None
        self._final_confidence: float | None = None
        self._rounds_log: list[dict[str, Any]] = []
        self._decisions: list[dict[str, Any]] = []
        self._unverified_finish = False
        self._original_instruction = ""
        self._previous_answer: str | None = None
        self._previous_report_text: str | None = None
        self._pending_verifier_step: Step | None = None
        self._final_answer: str | None = None

    @staticmethod
    def name() -> str:
        return "terminus-2-rubric-verifier"

    # ------------------------------------------------------------------ hooks

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
        if self._pending_verifier_step is not None:
            # Recorded here so that it follows the claiming step and precedes the decision.
            self._pending_verifier_step.step_id = len(self._trajectory_steps) + 1
            self._trajectory_steps.append(self._pending_verifier_step)
            self._pending_verifier_step = None
        was_pending = self._pending_completion
        commands, is_task_complete, feedback, analysis, plan, llm_response = (
            await super()._handle_llm_interaction(
                chat, prompt, logging_paths, original_instruction, session
            )
        )
        self._current_analysis = analysis or ""
        self._current_plan = plan or ""
        # A completion claim that will be followed by a confirmation message (stock
        # semantics: first task_complete of an attempt). The verifier runs after this
        # episode's commands have executed, in _execute_commands.
        self._claimed_complete = bool(is_task_complete) and not was_pending

        if was_pending and not (feedback and "ERROR:" in feedback):
            confidence = _parse_confidence(llm_response.content)
            self._decisions.append(
                {
                    "after_round": self._verify_round,
                    "task_complete": bool(is_task_complete),
                    "n_commands": len(commands),
                    "confidence": confidence,
                    "episode_step_id": len(self._trajectory_steps) + 1,
                }
            )
            if is_task_complete:
                self._final_confidence = confidence
                if commands:
                    # Stock behaviour: the commands run and the trial ends; the answer
                    # may therefore change after the last verification. Recorded (and
                    # checked against the answer file at the end), not prevented.
                    self._unverified_finish = True
        return commands, is_task_complete, feedback, analysis, plan, llm_response

    async def _execute_commands(
        self, commands: list[Command], session: TmuxSession
    ) -> tuple[bool, str]:
        timeout_occurred, terminal_output = await super()._execute_commands(
            commands, session
        )
        if self._claimed_complete:
            self._claimed_complete = False
            if self._verify_round < self._max_verify_rounds:
                self._verify_round += 1
                started = time.time()
                try:
                    self._pending_report_text = await self._run_verifier_round(
                        session, commands, terminal_output
                    )
                except Exception as exc:  # keep the trial alive; CancelledError passes
                    self.logger.error(
                        f"Rubric verifier round {self._verify_round} failed: {exc!r}"
                    )
                    self._pending_report_text = (
                        f"VERIFICATION REPORT (round {self._verify_round} of "
                        f"{self._max_verify_rounds})\nThe verifier could not complete "
                        f"this round ({type(exc).__name__}). No findings are available."
                    )
                    self._rounds_log.append(
                        {
                            "round": self._verify_round,
                            "error": repr(exc),
                            "elapsed_sec": time.time() - started,
                        }
                    )
            else:
                self._pending_report_text = None
        return timeout_occurred, terminal_output

    def _get_completion_confirmation_message(self, terminal_output: str) -> str:
        rounds_left = self._max_verify_rounds - self._verify_round
        if self._pending_report_text is None:
            body = (
                "No verification rounds remain for this task. Finish now: respond with "
                '"task_complete": true, an empty "commands" array, and a top-level field '
                '"confidence": <integer 0-100>, your probability that the current answer '
                "is exactly correct."
            )
        else:
            body = self._pending_report_text + "\n\n" + self._decision_instruction(rounds_left)
        return f"Current terminal state:\n{terminal_output}\n\n{body}"

    @staticmethod
    def _decision_instruction(rounds_left: int) -> str:
        finish = (
            'To finish: respond with "task_complete": true, an empty "commands" array, and '
            'a top-level field "confidence": <integer 0-100>, your probability that the '
            "current answer is exactly correct."
        )
        if rounds_left > 0:
            return (
                f"Decide how to proceed. {rounds_left} verification round(s) remain.\n"
                "To continue: address the items above with commands and do NOT set "
                '"task_complete". Change the answer file only if text you read from the '
                "statute or a computation you ran supports the change; your next "
                "completion claim will be verified again.\n" + finish
            )
        return (
            "This was the final verification round; no further verification will run. "
            + finish
            + " If you still want to apply a correction, include the commands in this "
            "same response: they will run and the task will then end."
        )

    async def _run_agent_loop(
        self,
        initial_prompt: str,
        chat: Chat,
        logging_dir: Path | None = None,
        original_instruction: str = "",
    ) -> None:
        self._original_instruction = original_instruction
        try:
            await super()._run_agent_loop(
                initial_prompt, chat, logging_dir, original_instruction
            )
            if self._pending_completion and self._final_confidence is None:
                await self._ask_confidence(chat)
            if self._session is not None:
                self._final_answer = await self._read_answer(self._session.environment)
        finally:
            self._write_summary()

    # --------------------------------------------------------------- verifier

    async def _run_verifier_round(
        self, session: TmuxSession, commands: list[Command], terminal_output: str
    ) -> str:
        environment = session.environment
        round_no = self._verify_round
        started = time.time()

        answer = await self._read_answer(environment)
        packet = self._build_packet(commands, terminal_output, answer, round_no)

        vchat = Chat(self._llm, interleaved_thinking=self._interleaved_thinking)
        vsteps: list[Step] = []
        session_id = str(uuid.uuid4())
        prompt = (
            self._skill_text
            + "\n\n"
            + packet
            + "\n\nBegin. Respond with exactly one JSON object as described in the skill."
        )
        vsteps.append(self._user_step(vsteps, prompt))

        report: dict | None = None
        commands_run: list[dict[str, str]] = []
        parse_failures = 0
        steps_used = 0
        context_exceeded = False
        last_usage = None
        responses: list[LLMResponse] = []

        async def call(p: str) -> LLMResponse:
            nonlocal last_usage
            t0 = time.time()
            resp = await vchat.chat(p, **self._llm_call_kwargs)
            self._track_api_request_time(t0)
            self._update_subagent_metrics(resp.usage)
            self._collect_subagent_rollout_detail(resp)
            last_usage = resp.usage
            responses.append(resp)
            return resp

        while steps_used < self._verifier_max_steps:
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
                    '"report".'
                )
                self._append_verifier_step(vsteps, resp, [], observation)
                prompt = observation
                continue
            if isinstance(parsed.get("report"), dict):
                report = parsed["report"]
                self._append_verifier_step(vsteps, resp, [], None)
                break
            cmds = parsed.get("commands") or []
            if not isinstance(cmds, list):
                cmds = [str(cmds)]
            cmds = [str(c) for c in cmds if str(c).strip()]
            if not cmds:
                steps_used += 1
                observation = (
                    "No commands and no report. Either run commands or fill in the "
                    '"report" field.'
                )
                self._append_verifier_step(vsteps, resp, [], observation)
                prompt = observation
                continue
            steps_used += 1
            outputs = []
            for c in cmds:
                out = await self._exec_in_env(environment, c)
                commands_run.append({"step": steps_used, "command": c, "output": out})
                outputs.append(f"$ {c}\n{out}")
            observation = (
                "\n\n".join(outputs)
                + f"\n\n[verifier: {steps_used} of {self._verifier_max_steps} command "
                "steps used]"
            )
            self._append_verifier_step(vsteps, resp, cmds, observation)
            prompt = observation

        if report is None and not context_exceeded:
            closing = (
                "You have used all command steps. Reply now with exactly one JSON object "
                'whose "report" field is filled in from the evidence you already have, '
                'and "commands": [].'
            )
            try:
                resp = await call(closing)
                parsed = _extract_json_object(resp.content)
                if parsed and isinstance(parsed.get("report"), dict):
                    report = parsed["report"]
                else:
                    parse_failures += 1
                self._append_verifier_step(vsteps, resp, [], None, prompt_text=closing)
            except ContextLengthExceededError:
                context_exceeded = True

        report_text = self._render_report(report, round_no, context_exceeded)
        elapsed = time.time() - started

        # Logs: ATIF trajectory for the verifier, a compact per-round record, and a
        # subagent reference in the solver's trajectory.
        traj_name = f"trajectory.verifier-round-{round_no}.json"
        self._save_verifier_trajectory(session_id, vsteps, traj_name, vchat)
        record = {
            "round": round_no,
            "answer": answer,
            "answer_changed": (
                None if self._previous_answer is None else answer != self._previous_answer
            ),
            "report": report,
            "report_text": report_text,
            "steps_used": steps_used,
            "parse_failures": parse_failures,
            "context_exceeded": context_exceeded,
            "commands": commands_run,
            "packet_chars": len(packet),
            "prompt_tokens": vchat.total_input_tokens,
            "completion_tokens": vchat.total_output_tokens,
            "n_llm_calls": len(responses),
            "elapsed_sec": elapsed,
            "trajectory_file": traj_name,
        }
        self._rounds_log.append(record)
        out_dir = self.logs_dir / "rubric_verifier"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"round-{round_no}.json").write_text(json.dumps(record, indent=2))
        (out_dir / f"round-{round_no}.packet.txt").write_text(packet)

        statuses = ", ".join(
            f"{r.get('name', '?')}={r.get('status', '?')}"
            for r in (report or {}).get("rubrics", [])
            if isinstance(r, dict)
        )
        self._pending_verifier_step = Step(
            step_id=len(self._trajectory_steps) + 1,  # re-assigned when appended
            timestamp=datetime.now(timezone.utc).isoformat(),
            source="system",
            message=(
                f"Rubric verifier round {round_no} of {self._max_verify_rounds} consulted "
                f"({steps_used} command steps, {elapsed:.0f} s); its report is the next "
                "message to the solver."
            ),
            observation=Observation(
                results=[
                    ObservationResult(
                        subagent_trajectory_ref=[
                            SubagentTrajectoryRef(
                                session_id=session_id,
                                trajectory_path=traj_name,
                                extra={
                                    "summary": f"Rubric verifier round {round_no}: "
                                    f"{statuses or 'no report'}"
                                },
                            )
                        ]
                    )
                ]
            ),
        )
        self._previous_answer = answer
        self._previous_report_text = report_text
        return report_text

    async def _ask_confidence(self, chat: Chat) -> None:
        prompt = (
            "The task has ended and your answer will be graded as it stands. State your "
            "confidence that it is exactly correct. Reply in plain text with one line: "
            "Confidence: <integer 0-100>%"
        )
        before_in, before_out = chat.total_input_tokens, chat.total_output_tokens
        t0 = time.time()
        resp = await chat.chat(prompt, **self._llm_call_kwargs)
        self._track_api_request_time(t0)
        self._final_confidence = _parse_confidence(resp.content)
        self._trajectory_steps.append(
            Step(
                step_id=len(self._trajectory_steps) + 1,
                timestamp=datetime.now(timezone.utc).isoformat(),
                source="user",
                message=prompt,
            )
        )
        self._trajectory_steps.append(
            Step(
                step_id=len(self._trajectory_steps) + 1,
                timestamp=datetime.now(timezone.utc).isoformat(),
                source="agent",
                model_name=resp.model_name or self._model_name,
                message=resp.content,
                reasoning_content=resp.reasoning_content,
                metrics=Metrics(
                    prompt_tokens=chat.total_input_tokens - before_in,
                    completion_tokens=chat.total_output_tokens - before_out,
                ),
            )
        )
        self._decisions.append(
            {
                "after_round": self._verify_round,
                "task_complete": True,
                "n_commands": 0,
                "confidence": self._final_confidence,
                "extra_confidence_turn": True,
            }
        )

    # ---------------------------------------------------------------- helpers

    async def _read_answer(self, environment) -> str:
        result = await environment.exec(
            f"cat {shlex.quote(self._answer_path)} 2>/dev/null", timeout_sec=10
        )
        text = (result.stdout or "").strip()
        return text if text else "(no answer file)"

    async def _exec_in_env(self, environment, command: str) -> str:
        try:
            result = await environment.exec(command, timeout_sec=60)
        except Exception as exc:  # timeouts and transport errors become observations
            return f"[command failed to run: {type(exc).__name__}: {exc}]"
        out = result.stdout or ""
        if result.stderr:
            out = f"{out}\n{result.stderr}" if out else result.stderr
        if result.return_code not in (0, None):
            out = f"{out}\n[exit code {result.return_code}]"
        return self._limit_output_length(out, VERIFIER_OUTPUT_LIMIT) if out else "(no output)"

    def _build_packet(
        self, commands: list[Command], terminal_output: str, answer: str, round_no: int
    ) -> str:
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
        # The episode that made the completion claim is not in _trajectory_steps yet.
        n += 1
        current = [f"--- step {n} (completion claim) ---"]
        if self._current_analysis:
            current.append(f"[analysis] {self._current_analysis}")
        if self._current_plan:
            current.append(f"[plan] {self._current_plan}")
        if commands:
            current.append("[commands]")
            current.extend(f"$ {c.keystrokes.rstrip()}" for c in commands)
        current.append("[observation]")
        current.append(terminal_output.rstrip() or "(empty)")
        current.append("[task_complete claimed]")
        steps_text.append("\n".join(current))

        parts.append(f"=== SOLVER TRAJECTORY ({n} steps) ===")
        parts.extend(steps_text)
        parts.append("")
        parts.append(f"=== VERIFICATION ROUND {round_no} of {self._max_verify_rounds} ===")
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

    def _format_agent_step(self, n: int, step: Step) -> str:
        lines = [f"--- step {n} ---"]
        if self._include_reasoning and step.reasoning_content:
            lines.append(f"[reasoning] {step.reasoning_content}")
        if step.message:
            lines.append(step.message)
        if step.tool_calls:
            cmds = [
                tc.arguments.get("keystrokes", "").rstrip()
                for tc in step.tool_calls
                if tc.function_name == "bash_command" and isinstance(tc.arguments, dict)
            ]
            if cmds:
                lines.append("[commands]")
                lines.extend(f"$ {c}" for c in cmds)
            if any(tc.function_name == "mark_task_complete" for tc in step.tool_calls):
                lines.append("[task_complete claimed]")
        if step.observation and step.observation.results:
            obs = "\n".join(
                r.content for r in step.observation.results if r.content is not None
            )
            if obs.strip():
                lines.append("[observation]")
                lines.append(obs.rstrip())
        return "\n".join(lines)

    def _render_report(
        self, report: dict | None, round_no: int, context_exceeded: bool
    ) -> str:
        header = f"VERIFICATION REPORT (round {round_no} of {self._max_verify_rounds})"
        if report is None:
            reason = (
                "the verifier's context limit was exceeded"
                if context_exceeded
                else "the verifier did not return a report within its step budget"
            )
            return f"{header}\nNo report: {reason}."
        lines = [header]
        rubrics = report.get("rubrics")
        if isinstance(rubrics, list):
            for i, r in enumerate(rubrics, 1):
                if not isinstance(r, dict):
                    lines.append(f"{i}. {r}")
                    continue
                name = str(r.get("name", f"rubric_{i}"))
                label = RUBRIC_LABELS.get(name, name.replace("_", " ").capitalize())
                status = str(r.get("status", "UNKNOWN")).upper()
                evidence = str(r.get("evidence", "")).strip()
                lines.append(f"{i}. {label}: {status}." + (f" {evidence}" if evidence else ""))
        else:
            lines.append(json.dumps(report, indent=2))
        unresolved = report.get("unresolved") or []
        if isinstance(unresolved, list) and unresolved:
            lines.append("Unresolved:")
            lines.extend(f"- {u}" for u in unresolved)
        actions = report.get("suggested_actions") or []
        if isinstance(actions, list) and actions:
            lines.append("Suggested next actions:")
            lines.extend(f"- {a}" for a in actions)
        return "\n".join(lines)

    # ----------------------------------------------------- verifier trajectory

    @staticmethod
    def _user_step(steps: list[Step], message: str) -> Step:
        return Step(
            step_id=len(steps) + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
            source="user",
            message=message,
        )

    def _append_verifier_step(
        self,
        steps: list[Step],
        resp: LLMResponse,
        cmds: list[str],
        observation: str | None,
        prompt_text: str | None = None,
    ) -> None:
        if prompt_text is not None:
            steps.append(self._user_step(steps, prompt_text))
        usage = resp.usage
        tool_calls = [
            ToolCall(
                tool_call_id=f"vcall_{len(steps) + 1}_{i + 1}",
                function_name="bash_command",
                arguments={"command": c},
            )
            for i, c in enumerate(cmds)
        ] or None
        obs = (
            Observation(results=[ObservationResult(content=observation)])
            if observation is not None
            else None
        )
        steps.append(
            Step(
                step_id=len(steps) + 1,
                timestamp=datetime.now(timezone.utc).isoformat(),
                source="agent",
                model_name=resp.model_name or self._model_name,
                message=resp.content,
                reasoning_content=resp.reasoning_content,
                tool_calls=tool_calls,
                observation=obs,
                metrics=Metrics(
                    prompt_tokens=usage.prompt_tokens if usage else None,
                    completion_tokens=usage.completion_tokens if usage else None,
                    cached_tokens=usage.cache_tokens if usage and usage.cache_tokens else None,
                ),
            )
        )

    def _save_verifier_trajectory(
        self, session_id: str, steps: list[Step], filename: str, vchat: Chat
    ) -> None:
        trajectory = Trajectory(
            session_id=session_id,
            agent=Agent(
                name="terminus-2-rubric-verifier/verifier",
                version=self.version() or "unknown",
                model_name=self._model_name,
                extra={
                    "parent_session_id": self._session_id,
                    "verify_round": self._verify_round,
                    "skill_path": str(self._skill_path),
                    "include_reasoning": self._include_reasoning,
                },
            ),
            steps=steps,
            final_metrics=FinalMetrics(
                total_prompt_tokens=vchat.total_input_tokens,
                total_completion_tokens=vchat.total_output_tokens,
                total_cached_tokens=vchat.total_cache_tokens,
                total_cost_usd=vchat.total_cost if vchat.total_cost > 0 else None,
            ),
        )
        path = self.logs_dir / filename
        try:
            path.write_text(format_trajectory_json(trajectory.to_json_dict()))
        except Exception as exc:
            self.logger.error(f"Failed to save verifier trajectory {path}: {exc}")

    def _write_summary(self) -> None:
        out_dir = self.logs_dir / "rubric_verifier"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            summary = {
                "max_verify_rounds": self._max_verify_rounds,
                "verifier_max_steps": self._verifier_max_steps,
                "include_reasoning": self._include_reasoning,
                "skill_path": str(self._skill_path),
                "rounds_run": self._verify_round,
                "rounds": [
                    {k: v for k, v in r.items() if k not in ("commands", "report_text")}
                    for r in self._rounds_log
                ],
                "decisions": self._decisions,
                "final_confidence": self._final_confidence,
                "finish_with_commands": self._unverified_finish,
                "final_answer": self._final_answer,
                "last_verified_answer": self._previous_answer,
                "answer_changed_after_last_round": (
                    None
                    if self._final_answer is None or self._previous_answer is None
                    else self._final_answer != self._previous_answer
                ),
                "completed_via_confirmation": self._pending_completion,
                "verifier_prompt_tokens": self._subagent_metrics.total_prompt_tokens,
                "verifier_completion_tokens": self._subagent_metrics.total_completion_tokens,
            }
            (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        except Exception as exc:
            self.logger.error(f"Failed to write rubric verifier summary: {exc}")
