"""The solver-reader-verifier-compaction harness (v0.1) on the KIRA loop.

``dar_terminus2_srvc.Terminus2SRVC`` is written against terminus-2: the solver replies with
one JSON object, and the reader, the verifier, compaction and the file write are top-level
fields of that object. KIRA (``agents.baseline_kira``, DAR's second harness) uses native
tool calls instead and has its own agent loop. This module puts the same harness on that
loop without a second copy of it:

    KiraSRVC -> Terminus2SRVC -> ... -> CostLedgerMixin -> _KiraUnderSRVC
             -> dar_kira_local.AgentHarness -> baseline_kira.AgentHarness -> Terminus2

Everything the harness does sits in two hooks, ``_handle_llm_interaction`` (what did the
solver ask for) and ``_execute_commands`` (do it and build the next message), plus
``_run_agent_loop`` (the survey before the first step). In the class order above those hooks
end in KIRA's own versions, so KIRA's loop, its command execution with marker polling and
its tool-call history stay as they are. What this module adds:

1. **Four more tools** next to ``execute_commands`` and ``task_complete``: ``write_file``,
   ``reader``, ``verifier``, ``compaction``. ``_KiraUnderSRVC`` takes them out of the tool
   calls of a reply and hands the harness an object of the shape it reads from a JSON reply
   (``{"commands": [...], "subagent": {...}, "write_file": {...}}``). ``image_read`` is not
   offered: its path through KIRA's loop does not pass ``_execute_commands``, where the
   completion gate is, and the benchmarks here are text.
2. **Model calls through harbor's LLM object** instead of KIRA's direct ``litellm`` call,
   so that the request settings (``max_tokens``, thinking, request time-out) and the cost
   ledger are the same as in the terminus-2 version.
3. **Branch calls carry the tool definitions.** The reader and compaction run on a copy of
   the solver's messages; the chat template prints the tools before the first message, so
   a branch request shares its prefix with the solver's requests only if it sends the same
   tools. They are sent with ``tool_choice="none"`` (vLLM keeps the definitions in the
   prompt and returns the reply as text). The verifier's rubrics have their own context and
   no tools, as before.
4. **Wording.** The harness's messages to the solver name JSON fields
   (``"subagent": {"name": "verifier"}``); ``tool_wording`` rewrites those phrases for a
   solver that calls tools. The solver's skill is ``solver_v5_tools`` and its template
   ``terminus-kira-srvc.txt`` (KIRA's own text plus the rule and skill slots).
5. **No block time-out.** KIRA wraps every step in a 600 s limit; a reader or verifier call
   takes longer, and the protocol has no wall-clock limits (cost is the limit).

Usage (from ``harbor-deonticbench/``, with ``tools/`` and
``meta-harness/reference_examples/terminal_bench_2`` on PYTHONPATH):

    harbor run --agent-import-path dar_kira_srvc:KiraSRVC ...

``tools/run_dar_qwen35_35b.sh`` with ``AGENT=kirasrvc`` does this.
"""

import json
import re
from pathlib import Path
from typing import Any

from agents.baseline_kira import TOOLS as KIRA_TOOLS
from agents.baseline_kira import ToolCallResponse
from harbor.llms.base import LLMResponse, OutputLengthExceededError

import dar_kira_local
from dar_terminus2_srvc import Terminus2SRVC

TOOLS_DIR = Path(__file__).resolve().parent
KIRA_SRVC_TEMPLATE_PATH = TOOLS_DIR / "templates" / "terminus-kira-srvc.txt"
SOLVER_V5_TOOLS_SKILL_PATH = TOOLS_DIR / "skills" / "solver_v5_tools" / "SKILL.md"

SUBAGENT_TOOLS = ("reader", "verifier", "compaction")
BRANCH_ROLES = ("reader", "compaction")  # sub-agents that run on the solver's messages


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


HARNESS_TOOLS = [
    _tool(
        "write_file",
        "Write one file. The harness writes it itself, without the terminal, before the "
        "commands of the same response run, and answers with one line (path, number of "
        "lines, last line). The file is not printed back to you.",
        {
            "path": {"type": "string", "description": "Absolute path of the file."},
            "content": {
                "type": "string",
                "description": "The complete new text of the file.",
            },
        },
        ["path", "content"],
    ),
    _tool(
        "reader",
        "Call the reader sub-agent, as described in the skill. It returns rule text "
        "verbatim with line ranges as your next message.",
        {
            "query": {
                "type": "string",
                "description": "The rule text you need: a unit, a line range, or a "
                "question.",
            }
        },
        ["query"],
    ),
    _tool(
        "verifier",
        "Call the verifier sub-agent, as described in the skill. It judges your current "
        "answer file and its report arrives as your next message.",
        {},
        [],
    ),
    _tool(
        "compaction",
        "Rebuild your context around a state page, as described in the skill.",
        {},
        [],
    ),
]

# The harness's messages name the fields of the JSON reply; a solver that calls tools is
# told the same thing in its own terms. Longest phrases first.
_WORDING = [
    (
        '"subagent": {"name": "verifier"}, an empty "commands" array and no "task_complete"',
        "a call to the tool verifier and no other tool call",
    ),
    (
        '"task_complete": true and an empty "commands" array',
        "a call to the tool task_complete and no other tool call",
    ),
    (
        '"task_complete": true with an empty "commands" array',
        "a call to the tool task_complete with no other tool call",
    ),
    (
        '"task_complete": true in a response with no "subagent"',
        "task_complete in a response with no sub-agent call",
    ),
    ('"subagent": {"name": "verifier"}', "a call to the tool verifier"),
    ('"subagent": {"name": "compaction"}', "a call to the tool compaction"),
    (
        'send the complete new file in "write_file"',
        "send the complete new file with the tool write_file",
    ),
    ('"task_complete": true', "a call to the tool task_complete"),
    ('"task_complete"', "task_complete"),
    ('"write_file"', "write_file"),
    ('Call the reader with a "query"', "Call the tool reader with a query"),
]
# In a branch message: the sentence that suspends the solver's reply format.
_NO_FIELDS_RE = re.compile(r'do not send "plan"[^.]*"task_complete"\.')
_NO_FORMAT = "The response format described there does not apply to your next reply"

TOOL_SUBAGENT_FORM = (
    "The sub-agents are tools, called like execute_commands: reader (with a query), "
    "verifier, compaction. A sub-agent is never a keystroke."
)


def tool_wording(text: str) -> str:
    """A harness message for the solver, with JSON-field phrases put in tool terms."""
    for old, new in _WORDING:
        text = text.replace(old, new)
    return text


def branch_wording(text: str) -> str:
    """A branch message (reader, compaction): the reply is text, no tool is called."""
    text = text.replace(
        _NO_FORMAT, "The tools described there are switched off for your next reply"
    )
    return _NO_FIELDS_RE.sub("do not call a tool; write your reply as text.", text)


def _arguments(tool_call: dict[str, Any]) -> dict[str, Any]:
    raw = (tool_call.get("function") or {}).get("arguments", "{}")
    try:
        args = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return {}
    return args if isinstance(args, dict) else {}


class _KiraUnderSRVC(dar_kira_local.AgentHarness):
    """KIRA as the harness classes above it expect a terminus-2 to behave: six values from
    ``_handle_llm_interaction``, and a reply text that is the JSON object of the step."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tools = [
            t for t in KIRA_TOOLS if t["function"]["name"] != "image_read"
        ] + HARNESS_TOOLS
        self._harness_calls: list[tuple[str, dict[str, Any]]] = []
        self._kira_reply: LLMResponse | None = None
        self._dropped_subagents: list[str] = []
        self._raw_reply: Any = None
        self._cuts_this_step = 0
        # The raw reply of a model call, for its tool calls: harbor's LLM object returns
        # text and usage only.
        inner_usage = self._llm._extract_usage_info

        def extract_usage_info(response):
            self._raw_reply = response
            return inner_usage(response)

        self._llm._extract_usage_info = extract_usage_info

    async def _with_block_timeout(self, coro, timeout_sec: int = 0):
        return await coro

    async def _call_llm_with_tools(self, messages: list[dict]) -> ToolCallResponse:
        self._raw_reply = None
        try:
            response = await self._llm.call(
                prompt=messages[-1]["content"],
                message_history=messages[:-1],
                tools=self._tools,
                **self._llm_call_kwargs,
            )
        except OutputLengthExceededError:
            # KIRA asks once for a shorter reply. A second cut reply in the same step
            # would end the trial with an exception; it is passed on as a reply without
            # tool calls instead, which KIRA answers with a warning, and the task goes on.
            self._cuts_this_step += 1
            if self._cuts_this_step < 2:
                raise
            return ToolCallResponse(
                content="[truncated]", tool_calls=[], reasoning_content=None, usage=None
            )
        tool_calls = (
            self._extract_tool_calls(self._raw_reply)
            if self._raw_reply is not None
            else []
        )
        return ToolCallResponse(
            content=response.content or "",
            tool_calls=tool_calls,
            reasoning_content=response.reasoning_content,
            usage=response.usage,
        )

    def _parse_tool_calls(self, tool_calls: list[dict[str, Any]]):
        own, rest = [], []
        for tc in tool_calls:
            name = (tc.get("function") or {}).get("name", "")
            if name == "write_file" or name in SUBAGENT_TOOLS:
                own.append((name, _arguments(tc)))
            else:
                rest.append(tc)
        self._harness_calls = own
        if own and not rest:
            return [], False, "", "", "", None
        return super()._parse_tool_calls(rest)

    async def _handle_llm_interaction(
        self, chat, prompt, logging_paths, original_instruction="", session=None
    ):
        self._harness_calls = []
        self._cuts_this_step = 0
        (
            commands,
            is_task_complete,
            feedback,
            analysis,
            plan,
            llm_response,
            _image_read,
        ) = await super()._handle_llm_interaction(
            chat, prompt, logging_paths, original_instruction, session
        )
        obj: dict[str, Any] = {
            "analysis": analysis,
            "plan": plan,
            "commands": [
                {"keystrokes": c.keystrokes, "duration": c.duration_sec}
                for c in commands
            ],
        }
        writes = [args for name, args in self._harness_calls if name == "write_file"]
        subs = [
            {**args, "name": name}
            for name, args in self._harness_calls
            if name in SUBAGENT_TOOLS
        ]
        if writes:
            obj["write_file"] = writes if len(writes) > 1 else writes[0]
        if subs:
            obj["subagent"] = subs[0]
        self._dropped_subagents = [s["name"] for s in subs[1:]]
        if is_task_complete:
            obj["task_complete"] = True
        self._kira_reply = llm_response
        as_json = LLMResponse(
            content=json.dumps(obj, ensure_ascii=False),
            reasoning_content=llm_response.reasoning_content,
            usage=llm_response.usage,
        )
        return commands, is_task_complete, feedback, analysis, plan, as_json


class KiraSRVC(Terminus2SRVC, _KiraUnderSRVC):
    """Solver-reader-verifier-compaction harness v0.1 with KIRA's loop and tool calls."""

    SUBAGENT_FORM = TOOL_SUBAGENT_FORM
    # A sub-agent typed into the terminal by its tool name ("reader query=...").
    MISPLACED_KEYSTROKE_RES = Terminus2SRVC.MISPLACED_KEYSTROKE_RES + (
        re.compile(r"^\s*(?:reader|verifier|compaction)(?:\s|\(|$)"),
    )

    def __init__(
        self,
        *args,
        solver_skill_path: str | None = None,
        derivation_template: str | None = None,
        **kwargs,
    ):
        super().__init__(
            *args,
            solver_skill_path=solver_skill_path or str(SOLVER_V5_TOOLS_SKILL_PATH),
            derivation_template=derivation_template or str(KIRA_SRVC_TEMPLATE_PATH),
            **kwargs,
        )

    @staticmethod
    def name() -> str:
        return "terminus-kira-srvc"

    async def _handle_llm_interaction(
        self, chat, prompt, logging_paths, original_instruction="", session=None
    ):
        commands, is_task_complete, feedback, analysis, plan, _ = (
            await super()._handle_llm_interaction(
                chat, prompt, logging_paths, original_instruction, session
            )
        )
        if self._dropped_subagents:
            note = (
                "Note: one sub-agent runs per response. The call(s) to "
                + ", ".join(self._dropped_subagents)
                + " in this response were not carried out; call again if still needed."
            )
            self._protocol_note = (
                f"{self._protocol_note}\n\n{note}" if self._protocol_note else note
            )
        # KIRA's loop takes a seventh value (an image_read request; not offered here).
        return (
            commands,
            is_task_complete,
            feedback,
            analysis,
            plan,
            self._kira_reply,
            None,
        )

    async def _execute_commands(self, commands, session):
        timeout_occurred, terminal_output = await super()._execute_commands(
            commands, session
        )
        return timeout_occurred, tool_wording(terminal_output)

    def _get_completion_confirmation_message(self, terminal_output: str) -> str:
        return tool_wording(
            super()._get_completion_confirmation_message(terminal_output)
        )

    async def _run_tool_subagent(self, environment, **kw):
        if kw.get("role") in BRANCH_ROLES:
            # A branch of the solver's context: the same tools in the request, so that
            # its prefix is the solver's; no tool is called in the reply. (The rubrics of
            # the verifier have a context of their own and no tools.)
            base = kw.get("llm_kwargs")
            base = self._llm_call_kwargs if base is None else base
            kw["llm_kwargs"] = {**base, "tools": self._tools, "tool_choice": "none"}
            for key in ("skill_text", "closing", "begin"):
                if isinstance(kw.get(key), str):
                    kw[key] = branch_wording(kw[key])
        return await super()._run_tool_subagent(environment, **kw)

    def _cut_reply_notes(self) -> str:
        return branch_wording(super()._cut_reply_notes())
