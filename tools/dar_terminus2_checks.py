"""terminus-2 solver with the rule document in its context and a verifier that is a list of
skill-defined checks (solver-checks harness).

Design: claude_docs/10_solver_checks_harness.md. Built on the reader-verifier agent
(``Terminus2ReaderVerifier``: sub-agent calls through a top-level ``"subagent"`` field, cost
ledger, output-token limits); there is no compactor. Three things change.

1. **The rule document is in the solver's first message when it is small.** At the start of
   the trial the harness reads ``rule_path`` from the container. If it has at most
   ``rule_inline_max_chars`` characters, the whole text fills the ``{rule_document}`` slot of
   the solver template (before the skill and the task) and the reader is switched off; above the limit the solver is told to use the reader, as in the
   reader-verifier harness. The limit is the only thing the harness knows about the file.

2. **The verifier is a list of checks.** ``checks`` names skills (``tools/skills/check_<name>_v1/
   SKILL.md``). One verifier call runs them in turn, each as an independent sub-agent with its
   own context. Every check gets the same input, in this order: the task, the rule document
   (text, or the path when it is too long), the shared report format
   (``tools/skills/check_report_v1/FORMAT.md``), its skill, and a packet with the answer file,
   its own findings of the previous round and the solver's rebuttal, if any. Task and rule
   document come first so that all checks of a trial share a prompt prefix. Every check returns
   the same object: ``table`` (rows the skill defines), ``verdict`` (PASS | FAIL), ``findings``
   (``issue``, ``rule_text``, ``basis``) and ``notes``.

   What a check looks for is in its skill. The harness only (a) validates the shape of the
   result and asks once for a correction, (b) searches the rule document for each quoted
   ``rule_text`` and marks a finding whose quote is not there, (c) merges the results into one
   report. A check's status is PASS, FAIL (at least one finding the harness could support),
   UNSUPPORTED (FAIL, but no finding with a quote found in the rule document; shown to the
   solver as a lead, does not block) or NO_REPORT (no valid result).

   A check whose last result is for the current answer file is not run again unless it has
   findings and the solver sent a rebuttal; a verifier call that would run nothing uses no round.

3. **The gate follows the verdict.** ``task_complete`` is accepted when a round on the answer
   file as it stands passed (every check PASS or UNSUPPORTED). After a round that did not pass,
   the solver corrects the file and calls the verifier again, or calls it with
   ``"subagent": {"name": "verifier", "rebuttal": "..."}`` and leaves the file as it is; the
   checks with findings then run again with their findings and the rebuttal. When the rounds
   are used up, or after ``max_unverified_claims`` bounced claims, the file is accepted as it
   stands and the finish reason says so.

Agent kwargs (``--ak key=value``), in addition to the reader-verifier agent's: ``checks``
(section_table,fact_table,grounded_reasoning; comma-separated names or paths),
``check_format_path``, ``rule_inline_max_chars`` (60000), ``check_max_steps`` (1; command steps
of one check when the rule document is in its prompt), ``check_max_steps_file`` (6; when it is
not; a skill's own ``max_steps`` raises either), ``check_max_output_tokens`` (8000),
``check_thinking`` (true), ``check_fallback`` (true), ``quote_min_chars`` (12),
``answer_headings`` (the section headings an answer file must have before a round is spent on
it: "## Sections,## Facts,## Reasoning,## Answer"), ``max_format_rejections`` (2). Defaults changed: ``solver_skill_path`` -> solver_v4,
``derivation_template`` -> terminus-json-multiagent-v4.txt.

Logs per trial: ``<trial>/agent/rubric_verifier/round-N.json`` (answer, rebuttal, every check's
table, findings, status and usage), ``round-N.<check>.packet.txt``,
``<trial>/agent/trajectory.verifier-<check>-round-N.json``, and ``summary.json`` extended with
the check configuration and history. Ledger roles are ``verifier.<check>``.

Usage (from ``harbor-deonticbench/``, with ``tools/`` on PYTHONPATH):

    harbor run --agent-import-path dar_terminus2_checks:Terminus2SolverChecks ...

``tools/run_dar_qwen35_35b.sh`` with ``AGENT=t2sc`` does this.
"""

from __future__ import annotations

import json
import re
import shlex
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harbor.agents.terminus_2.terminus_2 import Command
from harbor.agents.terminus_2.tmux_session import TmuxSession
from harbor.models.trajectories import (
    Observation,
    ObservationResult,
    Step,
    SubagentTrajectoryRef,
)

from dar_terminus2_multiagent import (
    GATE_HEADER,
    VERIFIER_HEADER,
    Terminus2ReaderVerifier,
    _sha,
)
from dar_terminus2_verifier import (
    CONTAINER_TRAJECTORY_PATH,
    SubagentClock,
    _falsy,
    _opt_tokens,
)

TOOLS_DIR = Path(__file__).resolve().parent
SKILLS_DIR = TOOLS_DIR / "skills"
SOLVER_V4_SKILL_PATH = SKILLS_DIR / "solver_v4" / "SKILL.md"
CHECK_FORMAT_PATH = SKILLS_DIR / "check_report_v1" / "FORMAT.md"
CHECKS_TEMPLATE_PATH = TOOLS_DIR / "templates" / "terminus-json-multiagent-v4.txt"
DEFAULT_CHECKS = "section_table,fact_table,grounded_reasoning"

DEFAULT_ANSWER_HEADINGS = "## Sections,## Facts,## Reasoning,## Answer"
RULE_SLOT = "{rule_document}"  # in the solver template; filled when the trial starts

NO_ANSWER = "(no answer file)"  # what _read_answer returns for a missing or empty file
FIELD_LIMIT = 700  # characters of one finding field shown to the solver
NOTES_SHOWN = 2

SUBAGENT_FORM = (
    '"subagent" is a top-level field of the JSON response, next to "commands"; it is never a '
    'keystroke and never a field inside a command object. Correct form: {"analysis": "...", '
    '"plan": "...", "commands": [], "subagent": {"name": "verifier"}} or, to answer findings '
    'without changing the answer file, "subagent": {"name": "verifier", "rebuttal": "..."}.'
)
CLOSING = (
    "You have used all command steps. Reply now with exactly one JSON object whose "
    '"result" field is filled in from what you already have ("table", "verdict", '
    '"findings", "notes") and "commands": [].'
)

_WS_RE = re.compile(r"\s+")
_GAP_RE = re.compile(r"\[\s*\.\.\.\s*\]|\.{3,}|…")
_QUOTE_CHARS = str.maketrans(
    {"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "}
)


def normalise_text(text: str) -> str:
    """Lower case, straight quotes and dashes, whitespace runs collapsed."""
    return _WS_RE.sub(" ", (text or "").translate(_QUOTE_CHARS)).strip().lower()


def quote_found(
    quote: str, normalised_document: str, min_chars: int = 12
) -> bool | None:
    """Whether ``quote`` occurs in the document. None for an empty quote. Line breaks and
    letter case are ignored; a quote with an ellipsis is searched piece by piece, and pieces
    shorter than ``min_chars`` are too short to count as evidence."""
    q = normalise_text(quote).strip("\"' ")
    if not q:
        return None
    pieces = [p.strip("\"' ,;:") for p in _GAP_RE.split(q)]
    pieces = [p for p in pieces if len(p) >= min_chars]
    if not pieces:
        return False
    return all(p in normalised_document for p in pieces)


@dataclass
class CheckSkill:
    """One check: the skill text sent to the model and the options in its front matter."""

    key: str  # [a-z0-9_]+, used in file names and ledger roles
    label: str
    path: Path
    text: str  # the skill without its front matter
    table_required: bool = True
    quote_required: bool = True
    needs_trajectory: bool = False
    max_steps: int | None = None


def load_check_skill(spec: str) -> CheckSkill:
    """``spec`` is a path to a skill file or directory, a directory name under
    ``tools/skills/``, or a short name ``x`` for ``tools/skills/check_x_v1/SKILL.md``."""
    spec = spec.strip()
    candidates = [
        Path(spec),
        Path(spec) / "SKILL.md",
        SKILLS_DIR / spec / "SKILL.md",
        SKILLS_DIR / f"check_{spec}_v1" / "SKILL.md",
    ]
    path = next((c for c in candidates if c.is_file()), None)
    if path is None:
        raise ValueError(f"no check skill found for {spec!r}")
    raw = path.read_text()
    meta: dict[str, str] = {}
    body = raw
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            for line in raw[3:end].splitlines():
                key, sep, value = line.partition(":")
                if sep:
                    meta[key.strip().lower()] = value.strip()
            body = raw[end + 4 :]
    name = meta.get("name") or path.parent.name
    key = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "check"
    steps = meta.get("max_steps", "")
    return CheckSkill(
        key=key,
        label=meta.get("label") or name.replace("_", " ").capitalize(),
        path=path,
        text=body.strip(),
        table_required=meta.get("table", "required").lower() == "required",
        quote_required=meta.get("quote", "required").lower() == "required",
        needs_trajectory=meta.get("trajectory", "false").lower()
        in ("true", "yes", "1"),
        max_steps=int(steps) if steps.isdigit() else None,
    )


def _clip(value: Any, limit: int = FIELD_LIMIT) -> str:
    text = _WS_RE.sub(" ", str(value if value is not None else "")).strip()
    return text if len(text) <= limit else text[: limit - 15].rstrip() + " [...cut]"


class Terminus2SolverChecks(Terminus2ReaderVerifier):
    """Solver with the rule document in context; the verifier is a list of skill-defined checks."""

    SUBAGENT_HELP = (
        '"verifier" (optionally with a "rebuttal") and, when the rule document is not in '
        'your first message, "reader" (with a "query")'
    )
    SUBAGENT_FORM = SUBAGENT_FORM

    def __init__(
        self,
        *args,
        checks: str | list[str] = DEFAULT_CHECKS,
        check_format_path: str | None = None,
        rule_inline_max_chars: int | str = 60000,
        check_max_steps: int | str = 1,
        check_max_steps_file: int | str = 6,
        answer_headings: str | list[str] = DEFAULT_ANSWER_HEADINGS,
        max_format_rejections: int | str = 2,
        check_max_output_tokens: int | str | None = 8000,
        check_thinking: bool | str = True,
        check_fallback: bool | str = True,
        quote_min_chars: int | str = 12,
        solver_skill_path: str | None = None,
        derivation_template: str | None = None,
        **kwargs,
    ):
        super().__init__(
            *args,
            solver_skill_path=solver_skill_path or str(SOLVER_V4_SKILL_PATH),
            derivation_template=derivation_template or str(CHECKS_TEMPLATE_PATH),
            **kwargs,
        )
        specs = checks.split(",") if isinstance(checks, str) else list(checks)
        self._checks = [load_check_skill(s) for s in specs if str(s).strip()]
        if not self._checks:
            raise ValueError("the check list is empty")
        if len({c.key for c in self._checks}) != len(self._checks):
            raise ValueError("two checks have the same name")
        self._check_format_path = (
            Path(check_format_path) if check_format_path else CHECK_FORMAT_PATH
        )
        self._check_format_text = self._check_format_path.read_text().strip()
        self._rule_inline_max_chars = int(rule_inline_max_chars)
        self._check_max_steps = int(check_max_steps)
        self._check_max_steps_file = int(check_max_steps_file)
        self._check_max_output_tokens = _opt_tokens(check_max_output_tokens)
        self._check_thinking = not _falsy(check_thinking)
        self._check_fallback = not _falsy(check_fallback)
        self._quote_min_chars = int(quote_min_chars)
        heads = (
            answer_headings.split(",")
            if isinstance(answer_headings, str)
            else list(answer_headings)
        )
        self._answer_headings = [h.strip() for h in heads if str(h).strip()]
        self._max_format_rejections = int(max_format_rejections)
        self._format_rejections = 0

        self._task_instruction = ""
        self._rule_text: str | None = None
        self._rule_norm = ""
        self._rule_inline = False
        # Last outcome per check key (see _run_check for the fields).
        self._check_state: dict[str, dict[str, Any]] = {}
        self._passed_hashes: set[str] = set()
        self._last_passed = False
        self._pending_rebuttal: str | None = None
        self._n_rebuttals = 0
        self._n_free_calls = 0  # verifier calls that ran nothing and used no round

    @staticmethod
    def name() -> str:
        return "terminus-2-solver-checks"

    # ------------------------------------------------------- rule document

    async def run(self, instruction, environment, context) -> None:
        self._task_instruction = instruction
        await self._load_rule(environment)
        if self._rule_inline:
            block = self._rule_block()
        else:
            size = (
                f" has {self._rule_line_count()} lines ({len(self._rule_text):,} characters) "
                "and"
                if self._rule_text is not None
                else ""
            )
            block = (
                f"[The rule document {self._rule_path}{size} is too long to include here. "
                "Get rule text through the reader sub-agent.]"
            )
        if RULE_SLOT in self._prompt_template:
            # The template is str.format-ed with the instruction later: double the braces.
            self._prompt_template = self._prompt_template.replace(
                RULE_SLOT, block.replace("{", "{{").replace("}", "}}")
            )
        else:  # a template without the slot: the rule document follows the task
            instruction = f"{instruction.rstrip()}\n\n{block}"
        await super().run(instruction, environment, context)

    async def _load_rule(self, environment) -> None:
        text: str | None = None
        try:
            result = await environment.exec(
                f"cat {shlex.quote(self._rule_path)}", timeout_sec=30
            )
            if result.return_code in (0, None) and (result.stdout or "").strip():
                text = result.stdout
        except Exception as exc:
            self.logger.error(f"Could not read the rule document: {exc!r}")
        self._rule_text = text
        self._rule_norm = normalise_text(text or "")
        self._rule_inline = (
            text is not None and len(text) <= self._rule_inline_max_chars
        )

    def _rule_line_count(self) -> int:
        return len((self._rule_text or "").splitlines())

    def _rule_block(self) -> str:
        return (
            f"=== RULE DOCUMENT: {self._rule_path} (complete, {self._rule_line_count()} "
            f"lines) ===\n{(self._rule_text or '').rstrip()}\n=== END OF RULE DOCUMENT ==="
        )

    # ------------------------------------------------------------ sub-agents

    def _normalise_subagent(self, sub: Any) -> dict[str, Any] | None:
        raw = sub if isinstance(sub, dict) else {}
        out = super()._normalise_subagent(sub)
        if out is None or out.get("error"):
            return out
        if out["name"] == "reader" and self._rule_inline:
            return {
                "error": "The reader is not available in this task: the complete rule "
                "document is in your first message (the block headed === RULE DOCUMENT). "
                "Work from that text. No sub-agent was called."
            }
        if out["name"] == "verifier":
            rebuttal = next(
                (
                    raw[k]
                    for k in ("rebuttal", "reply", "response", "reason", "message")
                    if raw.get(k)
                ),
                None,
            )
            if rebuttal is not None:
                if not isinstance(rebuttal, str):
                    rebuttal = json.dumps(rebuttal, ensure_ascii=False)
                out["rebuttal"] = rebuttal.strip()
        return out

    async def _dispatch_subagent(
        self,
        session: TmuxSession,
        sub: dict[str, Any],
        commands: list[Command],
        terminal_output: str,
    ) -> str:
        self._pending_rebuttal = sub.get("rebuttal") or None
        return await super()._dispatch_subagent(session, sub, commands, terminal_output)

    # ------------------------------------------------------------ completion

    async def _gate_claim(self, environment) -> None:
        answer = await self._read_answer(environment)
        h = _sha(answer)
        passed = h in self._passed_hashes
        claim: dict[str, Any] = {
            "episode_step_id": len(self._trajectory_steps) + 1,
            "answer_hash": h[:16],
            "verified": passed,
            "rounds_run": self._verify_round,
        }
        rounds_left = self._max_verify_rounds - self._verify_round
        if passed:
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
            if self._verified_hash is None:
                reason = "no verifier round has run in this task"
                todo = 'Respond with "subagent": {"name": "verifier"}'
            elif h != self._verified_hash:
                reason = "it changed after the last verifier report"
                todo = 'Respond with "subagent": {"name": "verifier"}'
            else:
                reason = "the last verifier report on it did not pass"
                todo = (
                    "Correct the file where a finding holds and call the verifier again, or "
                    'answer the findings with "subagent": {"name": "verifier", "rebuttal": '
                    '"..."}'
                )
            claim["reason"] = reason
            self._gate_message = (
                f"{GATE_HEADER}\n"
                f"The answer file {self._answer_path} has not passed verification in its "
                f'current form ({reason}). {todo}, with an empty "commands" array and no '
                f'"task_complete". {rounds_left} verification round(s) remain. (Claim '
                f"{self._unverified_claims} of {self._max_unverified_claims} that was not "
                "accepted; after that, task_complete is accepted as the file stands.)"
            )
        self._claims.append(claim)
        if self._pending_completion:
            self._final_answer = answer

    # -------------------------------------------------------------- verifier

    async def _verifier_call(
        self, session: TmuxSession, commands: list[Command], terminal_output: str
    ) -> str:
        environment = session.environment
        rebuttal = self._pending_rebuttal
        self._pending_rebuttal = None
        if self._verify_round >= self._max_verify_rounds:
            return (
                f"{VERIFIER_HEADER} ===\n"
                f"No verification rounds remain for this task ({self._max_verify_rounds} of "
                f'{self._max_verify_rounds} used). "task_complete" with an empty "commands" '
                "array will now be accepted as the answer file stands."
            )
        answer = await self._read_answer(environment)
        if answer == NO_ANSWER:
            self._n_free_calls += 1
            return (
                f"{VERIFIER_HEADER} ===\n"
                f"There is no answer file at {self._answer_path} yet, so nothing was checked "
                "and no round was used. Write the answer file, then call the verifier."
            )
        missing = self._missing_headings(answer)
        if missing and self._format_rejections < self._max_format_rejections:
            self._format_rejections += 1
            self._n_free_calls += 1
            return (
                f"{VERIFIER_HEADER} ===\n"
                f"The answer file {self._answer_path} is not complete: it has no line "
                f"{', '.join(repr(m) for m in missing)}. The checks read the sections of the "
                "answer file, so nothing was checked and no round was used. Write the file "
                "with all its sections ("
                + ", ".join(self._answer_headings)
                + ", each filled in as the skill describes, the final answer line last), then "
                "call the verifier."
            )
        h = _sha(answer)

        # A check runs unless its last result is for this very file and nothing new has been
        # put to it: a PASS stands, and findings stand until the solver answers them.
        to_run: list[CheckSkill] = []
        for skill in self._checks:
            prev = self._check_state.get(skill.key)
            current = prev is not None and prev.get("hash") == h
            if current and prev["status"] == "PASS":
                continue
            if current and prev["status"] in ("FAIL", "UNSUPPORTED") and not rebuttal:
                continue
            to_run.append(skill)
        if not to_run:
            self._n_free_calls += 1
            self._awaiting_decision = True
            rounds_left = self._max_verify_rounds - self._verify_round
            return (
                f"{VERIFIER_HEADER} ===\n"
                "The answer file is unchanged since the last report and the call carried no "
                "rebuttal, so no check was run again and no round was used. The report "
                f"stands:\n{self._previous_report_text}\n\n"
                f"{self._decision_instruction(rounds_left, passed=self._last_passed)}"
            )

        self._verify_round += 1
        round_no = self._verify_round
        started = time.time()
        if rebuttal:
            self._n_rebuttals += 1
        trajectory_note = ""
        if any(s.needs_trajectory for s in to_run):
            try:
                full = self._build_packet(commands, terminal_output, answer, round_no)
                await self._upload_trajectory(environment, full, round_no)
                trajectory_note = (
                    f"The solver's full trajectory is the file {CONTAINER_TRAJECTORY_PATH} "
                    "in this environment (steps headed '--- step N ---')."
                )
            except Exception as exc:
                self.logger.error(f"Could not upload the trajectory: {exc!r}")

        outcomes: list[dict[str, Any]] = []
        for index, skill in enumerate(self._checks, 1):
            prev = self._check_state.get(skill.key)
            if skill not in to_run:
                outcomes.append({**prev, "reused": True})
                continue
            try:
                outcome = await self._run_check(
                    environment,
                    skill,
                    index,
                    answer,
                    round_no,
                    prev,
                    rebuttal,
                    trajectory_note if skill.needs_trajectory else "",
                )
            except Exception as exc:  # keep the trial alive; CancelledError passes
                self.logger.error(f"Check {skill.key} round {round_no} failed: {exc!r}")
                outcome = self._empty_outcome(
                    skill,
                    h,
                    round_no,
                    f"the check failed to run ({type(exc).__name__})",
                )
                outcome["error"] = repr(exc)
            self._check_state[skill.key] = outcome
            outcomes.append({**outcome, "reused": False})

        passed = all(o["status"] in ("PASS", "UNSUPPORTED") for o in outcomes)
        self._verified_hash = h
        self._last_passed = passed
        if passed:
            self._passed_hashes.add(h)
        if any(o["status"] == "NO_REPORT" and not o["reused"] for o in outcomes):
            self._failed_rounds += 1
        report_text = self._render_round(round_no, outcomes, passed)
        elapsed = time.time() - started

        ran = [o for o in outcomes if not o["reused"]]
        record = {
            "round": round_no,
            "answer": answer,
            "answer_hash": h[:16],
            "answer_changed": (
                None
                if self._previous_answer is None
                else answer != self._previous_answer
            ),
            "rebuttal": rebuttal,
            "passed": passed,
            "checks": outcomes,
            "report": {
                "passed": passed,
                "rubrics": [
                    {"name": o["check"], "status": o["status"]} for o in outcomes
                ],
            },
            "report_text": report_text,
            "steps_used": sum(o.get("steps_used", 0) for o in ran),
            "parse_failures": sum(o.get("parse_failures", 0) for o in ran),
            "timed_out": any(o.get("timed_out") for o in ran),
            "thinking_fallback": any(o.get("thinking_fallback") for o in ran),
            "prompt_tokens": sum(o.get("prompt_tokens", 0) for o in ran),
            "completion_tokens": sum(o.get("completion_tokens", 0) for o in ran),
            "n_llm_calls": sum(o.get("n_llm_calls", 0) for o in ran),
            "elapsed_sec": elapsed,
        }
        self._rounds_log.append(record)
        out_dir = self.logs_dir / "rubric_verifier"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"round-{round_no}.json").write_text(json.dumps(record, indent=2))

        self._previous_answer = answer
        self._previous_report_text = report_text
        self._awaiting_decision = True
        rounds_left = self._max_verify_rounds - round_no
        return (
            f"{VERIFIER_HEADER} (round {round_no} of {self._max_verify_rounds}) ===\n"
            f"{report_text}\n\n{self._decision_instruction(rounds_left, passed=passed)}"
        )

    def _decision_instruction(
        self, rounds_left: int, verified: bool = True, passed: bool = True
    ) -> str:
        if passed:
            return (
                "The answer file as it stands has passed. To finish: respond with "
                '"task_complete": true and an empty "commands" array. If you change the '
                "file, it must pass again before you finish"
                + (f" ({rounds_left} round(s) remain)." if rounds_left > 0 else ".")
            )
        if rounds_left > 0:
            return (
                f"The answer file has not passed. {rounds_left} verification round(s) "
                "remain. Decide per finding: read the quoted rule text in the rule document "
                "together with the conditions around it.\n"
                "- The finding holds: correct the answer file (recompute with python3 where "
                'numbers change), then call the verifier again with "subagent": {"name": '
                '"verifier"}.\n'
                "- The finding does not hold: leave the file as it is and answer the finding. "
                'Respond with "subagent": {"name": "verifier", "rebuttal": "<finding number>: '
                '<the rule text or case fact that defeats it>"}; the checks with findings '
                "then look again.\n"
                "A check with NO REPORT produced nothing and runs again at the next call. A "
                "finding whose quote was not found in the rule document is a lead, not rule "
                "text."
            )
        return (
            "The answer file has not passed and this was the final verification round. "
            '"task_complete": true with an empty "commands" array will be accepted as the '
            "file stands. If a finding holds, correct the file first, in a response without "
            '"task_complete", then finish.'
        )

    def _missing_headings(self, answer: str) -> list[str]:
        """Section headings the answer file must have (the contract between the solver's
        skill and the checks) and does not have."""
        lines = {ln.strip().lower() for ln in answer.splitlines()}
        return [h for h in self._answer_headings if h.lower() not in lines]

    # ----------------------------------------------------------------- checks

    def _check_prefix(self) -> str:
        """Task and rule document: the part of the prompt that all checks of a trial share."""
        parts = ["=== TASK ===", self._task_instruction.strip(), ""]
        if self._rule_inline:
            parts.append(self._rule_block())
        else:
            parts += [
                "=== RULE DOCUMENT ===",
                f"The rule document is the file {self._rule_path} in this environment"
                + (
                    f" ({self._rule_line_count()} lines)"
                    if self._rule_text is not None
                    else ""
                )
                + "; it is too long to print here. Read the parts you need with commands "
                "(grep -n to locate, sed -n 'a,bp' to read a range).",
            ]
        return "\n".join(parts) + "\n\n"

    def _findings_text(self, index: int, findings: list[dict[str, Any]]) -> list[str]:
        lines: list[str] = []
        for j, f in enumerate(findings, 1):
            lines.append(f"  {index}.{j} {_clip(f.get('issue'))}")
            quote = _clip(f.get("rule_text"))
            if quote:
                mark = (
                    ""
                    if f.get("quote_found")
                    else "  [quote NOT found in the rule document]"
                )
                lines.append(f'      Rule text: "{quote}"{mark}')
            basis = _clip(f.get("basis"))
            if basis:
                lines.append(f"      Basis: {basis}")
        return lines

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
            f"=== ROUND {round_no} of {self._max_verify_rounds} ===",
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
        if prev is not None and prev.get("findings"):
            same = prev.get("hash") == _sha(answer)
            parts += [
                "",
                f"=== YOUR FINDINGS IN ROUND {prev['round']} (the answer file "
                f"{'is unchanged' if same else 'has changed'} since then) ===",
                *self._findings_text(index, prev["findings"]),
            ]
            if rebuttal:
                parts += ["", "=== THE SOLVER'S REPLY TO THE FINDINGS ===", rebuttal]
        return "\n".join(parts)

    @staticmethod
    def _unwrap(result: Any) -> dict | None:
        """Some replies put the whole reply object inside ``result``."""
        while (
            isinstance(result, dict)
            and "verdict" not in result
            and isinstance(result.get("result"), dict)
        ):
            result = result["result"]
        return result if isinstance(result, dict) else None

    def _shape_problem(self, skill: CheckSkill, result: dict) -> str | None:
        """What is wrong with the shape of a check's result, or None."""
        result = self._unwrap(result) or {}
        verdict = str(result.get("verdict", "")).strip().upper()
        if verdict not in ("PASS", "FAIL"):
            return '"verdict" must be "PASS" or "FAIL".'
        findings = result.get("findings") or []
        if not isinstance(findings, list) or any(
            not isinstance(f, dict) for f in findings
        ):
            return '"findings" must be a list of objects with "issue", "rule_text" and "basis".'
        table = result.get("table")
        if skill.table_required and not (
            isinstance(table, list)
            and table
            and all(isinstance(r, dict) for r in table)
        ):
            return (
                '"table" must be a non-empty list of row objects, one per row your check '
                "instructions define."
            )
        if verdict == "FAIL" and not findings:
            return 'The verdict is "FAIL" but "findings" is empty: every FAIL needs a finding.'
        if verdict == "PASS" and findings:
            return (
                'The verdict is "PASS" but "findings" is not empty: a verified finding makes '
                'the verdict "FAIL"; what you could not verify belongs in "notes".'
            )
        for i, f in enumerate(findings, 1):
            if not str(f.get("issue") or "").strip():
                return f'Finding {i} has no "issue".'
            if skill.quote_required and not str(f.get("rule_text") or "").strip():
                return (
                    f'Finding {i} has no "rule_text": quote the clause of the rule document '
                    "it rests on, verbatim."
                )
        return None

    def _result_problem(self, skill: CheckSkill, result: dict) -> str | None:
        """Shape problem, or a quote that is not in the rule document (asked once to fix)."""
        problem = self._shape_problem(skill, result)
        if problem or self._rule_text is None:
            return problem
        findings = (self._unwrap(result) or {}).get("findings") or []
        missing = [
            str(i)
            for i, f in enumerate(findings, 1)
            if quote_found(
                str(f.get("rule_text") or ""), self._rule_norm, self._quote_min_chars
            )
            is False
        ]
        if missing:
            return (
                f'The "rule_text" of finding {", ".join(missing)} does not occur in the rule '
                "document. Copy the clause exactly as it is printed there (no paraphrase, no "
                'ellipsis inside a clause), or move the finding to "notes" if you cannot '
                "quote text for it."
            )
        return None

    def _empty_outcome(
        self, skill: CheckSkill, answer_hash: str, round_no: int, reason: str
    ) -> dict[str, Any]:
        return {
            "check": skill.key,
            "label": skill.label,
            "round": round_no,
            "hash": answer_hash,
            "status": "NO_REPORT",
            "reason": reason,
            "verdict": None,
            "table": [],
            "findings": [],
            "notes": [],
        }

    async def _run_check(
        self,
        environment,
        skill: CheckSkill,
        index: int,
        answer: str,
        round_no: int,
        prev: dict[str, Any] | None,
        rebuttal: str | None,
        trajectory_note: str,
    ) -> dict[str, Any]:
        started = time.time()
        h = _sha(answer)
        max_steps = max(
            skill.max_steps or 0,
            self._check_max_steps if self._rule_inline else self._check_max_steps_file,
        )
        packet = self._build_check_packet(
            skill, index, answer, round_no, prev, rebuttal, max_steps, trajectory_note
        )
        clock = SubagentClock(
            None,
            self._check_thinking,
            self._check_fallback,
            max_output_tokens=self._check_max_output_tokens,
        )
        run = await self._run_tool_subagent(
            environment,
            role=f"verifier.{skill.key}",
            prefix=self._check_prefix(),
            skill_text=f"{self._check_format_text}\n\n{skill.text}",
            packet=packet,
            max_steps=max_steps,
            closing=CLOSING,
            clock=clock,
            validate=lambda r: self._result_problem(skill, r),
            # a reply with "verdict" next to "analysis" instead of inside "result"
            lift=lambda reply: reply if "verdict" in reply else None,
        )
        result = self._unwrap(run["result"])
        if result is None:
            reason = (
                "the check's context limit was exceeded"
                if run["context_exceeded"]
                else f"{clock.describe_limit()}"
                if run["timed_out"]
                else "no result within its command steps"
            )
            outcome = self._empty_outcome(skill, h, round_no, reason)
        elif (problem := self._shape_problem(skill, result)) is not None:
            outcome = self._empty_outcome(
                skill, h, round_no, f"invalid result: {problem}"
            )
            outcome["raw_result"] = result
        else:
            findings = []
            for f in result.get("findings") or []:
                found = quote_found(
                    str(f.get("rule_text") or ""),
                    self._rule_norm,
                    self._quote_min_chars,
                )
                if self._rule_text is None and found is False:
                    found = None  # the harness has no text to search
                findings.append(
                    {
                        "issue": str(f.get("issue") or "").strip(),
                        "rule_text": str(f.get("rule_text") or "").strip(),
                        "basis": str(f.get("basis") or "").strip(),
                        "quote_found": found,
                        # A quote that is there supports a finding; so does no quote where
                        # the skill does not ask for one (an arithmetic step).
                        "supported": found is True
                        or (found is None and not skill.quote_required)
                        or (found is None and self._rule_text is None),
                    }
                )
            verdict = str(result.get("verdict")).strip().upper()
            if verdict == "PASS":
                status = "PASS"
            else:
                status = (
                    "FAIL" if any(f["supported"] for f in findings) else "UNSUPPORTED"
                )
            notes = result.get("notes") or []
            outcome = {
                "check": skill.key,
                "label": skill.label,
                "round": round_no,
                "hash": h,
                "status": status,
                "reason": None,
                "verdict": verdict,
                "table": result.get("table") or [],
                "findings": findings,
                "notes": [str(n) for n in notes]
                if isinstance(notes, list)
                else [str(notes)],
            }
        elapsed = time.time() - started
        traj_name = f"trajectory.verifier-{skill.key}-round-{round_no}.json"
        self._save_subagent_trajectory(
            run["session_id"],
            run["steps"],
            traj_name,
            run["chat"],
            agent_name=f"{self.name()}/verifier.{skill.key}",
            extra={
                "parent_session_id": self._session_id,
                "verify_round": round_no,
                "skill_path": str(skill.path),
                "format_path": str(self._check_format_path),
            },
        )
        outcome.update(
            {
                "rebuttal_seen": bool(rebuttal and prev and prev.get("findings")),
                "previous_status": prev.get("status") if prev else None,
                "steps_used": run["steps_used"],
                "parse_failures": run["parse_failures"],
                "repairs": run["repairs"],
                "context_exceeded": run["context_exceeded"],
                "timed_out": run["timed_out"],
                "limit_reached": clock.limit_reached,
                "thinking_fallback": clock.fell_back,
                "attempts": clock.attempts,
                "commands": run["commands"],
                "packet_chars": len(packet),
                "prompt_tokens": run["chat"].total_input_tokens,
                "completion_tokens": run["chat"].total_output_tokens,
                "n_llm_calls": run["n_calls"],
                "elapsed_sec": elapsed,
                "trajectory_file": traj_name,
            }
        )
        out_dir = self.logs_dir / "rubric_verifier"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"round-{round_no}.{skill.key}.packet.txt").write_text(packet)
        self._pending_subagent_steps.append(
            Step(
                step_id=len(self._trajectory_steps) + 1,  # re-assigned when appended
                timestamp=datetime.now(timezone.utc).isoformat(),
                source="system",
                message=(
                    f"Verifier round {round_no}, check {skill.key}: {outcome['status']} "
                    f"({run['steps_used']} command steps, {elapsed:.0f} s"
                    + (f"; {clock.describe_limit()}" if clock.limit_reached else "")
                    + "); the merged report is part of the previous observation."
                ),
                observation=Observation(
                    results=[
                        ObservationResult(
                            subagent_trajectory_ref=[
                                SubagentTrajectoryRef(
                                    session_id=run["session_id"],
                                    trajectory_path=traj_name,
                                    extra={
                                        "summary": f"Check {skill.key} round {round_no}: "
                                        f"{outcome['status']}, "
                                        f"{len(outcome['findings'])} finding(s)"
                                    },
                                )
                            ]
                        )
                    ]
                ),
            )
        )
        return outcome

    def _render_round(
        self, round_no: int, outcomes: list[dict[str, Any]], passed: bool
    ) -> str:
        lines = [
            f"VERIFICATION REPORT (round {round_no} of {self._max_verify_rounds}): "
            f"{'PASSED' if passed else 'NOT PASSED'}"
        ]
        for index, o in enumerate(outcomes, 1):
            status = {
                "PASS": "PASS",
                "FAIL": "FAIL",
                "UNSUPPORTED": "FAIL without a quote found in the rule document (leads "
                "only, does not block)",
                "NO_REPORT": f"NO REPORT ({o.get('reason') or 'no result'})",
            }[o["status"]]
            reused = (
                f" (from round {o['round']}, not run again)" if o.get("reused") else ""
            )
            lines.append(f"[{index}] {o['label']}: {status}{reused}")
            lines.extend(self._findings_text(index, o.get("findings") or []))
            for note in (o.get("notes") or [])[:NOTES_SHOWN]:
                lines.append(f"      Unverified note: {_clip(note, 300)}")
        return "\n".join(lines)

    # ---------------------------------------------------------------- helpers

    def _write_summary(self) -> None:
        super()._write_summary()
        path = self.logs_dir / "rubric_verifier" / "summary.json"
        try:
            data = json.loads(path.read_text()) if path.exists() else {}
            data.update(
                {
                    "harness": "solver-checks-v1",
                    "checks": [
                        {
                            "name": c.key,
                            "label": c.label,
                            "skill_path": str(c.path),
                            "table_required": c.table_required,
                            "quote_required": c.quote_required,
                            "needs_trajectory": c.needs_trajectory,
                            "max_steps": c.max_steps,
                        }
                        for c in self._checks
                    ],
                    "rubric_names": [c.key for c in self._checks],
                    "check_format_path": str(self._check_format_path),
                    "answer_headings": self._answer_headings,
                    "n_format_rejections": self._format_rejections,
                    "check_max_steps": self._check_max_steps,
                    "check_max_steps_file": self._check_max_steps_file,
                    "check_max_output_tokens": self._check_max_output_tokens,
                    "check_thinking": self._check_thinking,
                    "check_fallback": self._check_fallback,
                    "quote_min_chars": self._quote_min_chars,
                    "rule_inline_max_chars": self._rule_inline_max_chars,
                    "rule_inline": self._rule_inline,
                    "rule_chars": len(self._rule_text)
                    if self._rule_text is not None
                    else None,
                    "rule_lines": self._rule_line_count()
                    if self._rule_text is not None
                    else None,
                    "last_round_passed": self._last_passed
                    if self._verify_round
                    else None,
                    "n_passed_versions": len(self._passed_hashes),
                    "final_answer_passed": (
                        None
                        if self._final_answer is None
                        else _sha(self._final_answer) in self._passed_hashes
                    ),
                    "n_rebuttals": self._n_rebuttals,
                    "n_free_verifier_calls": self._n_free_calls,
                    "rounds": [
                        {
                            k: v
                            for k, v in r.items()
                            if k not in ("report_text", "checks")
                        }
                        | {
                            "checks": [
                                {
                                    k: v
                                    for k, v in o.items()
                                    if k not in ("commands", "table", "raw_result")
                                }
                                | {"table_rows": len(o.get("table") or [])}
                                for o in r.get("checks", [])
                            ]
                        }
                        for r in self._rounds_log
                    ],
                }
            )
            path.write_text(json.dumps(data, indent=2))
        except Exception as exc:
            self.logger.error(f"Failed to extend the summary: {exc}")
