"""baseline_kira with tool-call argument normalisation for locally served open models.

The meta-harness ``baseline_kira`` agent (DAR's KIRA harness) expects the ``commands``
argument of ``execute_commands`` to be a JSON array of ``{"keystrokes", "duration"}``
objects. Qwen3.5 served through vLLM's ``qwen3_xml`` tool parser emits a single object
(or occasionally a list of plain strings) for that parameter, and ``baseline_kira``
then crashes with ``AttributeError: 'str' object has no attribute 'get'`` before the
first command runs (observed 2026-10-16, see claude_docs/07 §1).

This subclass only reshapes the arguments into the expected form, recovers ``commands``
when the model wrote JSON inside one XML parameter, and strips stray ``<parameter=...>``
tags from keystrokes; prompts, tools,
loop logic and everything else are inherited unchanged.

Usage (from ``harbor-deonticbench/``, with ``tools/`` and
``meta-harness/reference_examples/terminal_bench_2`` on PYTHONPATH):

    harbor run --agent-import-path dar_kira_local:AgentHarness ...

``tools/run_dar_qwen35_35b.sh`` with ``AGENT=kira`` does this.
"""

import json
import re
from typing import Any

from agents.baseline_kira import AgentHarness as _BaselineKira


_STRAY_TAG = re.compile(r"^\s*</?parameter(=\w+)?>\s*$", re.MULTILINE)


def clean_keystrokes(ks: str) -> str:
    """Drop stray XML parameter tags the qwen3_xml parser leaves inside keystrokes.

    For an array-of-objects argument the model nests ``<parameter=keystrokes>`` inside
    ``<parameter=commands>``; vLLM's parser keeps the inner tag as literal text, so the
    keystrokes arrive as ``"\\n<parameter=keystrokes>\\ncat /app/statute.txt\\n"`` and the
    shell would run a bogus ``<parameter=keystrokes>`` line first (observed 2026-10-16).
    """
    ks = _STRAY_TAG.sub("", ks)
    # exactly one trailing newline: the model tends to add one more "\n" per turn, and a bare
    # "\n" (press Enter / wait) stays a bare "\n"
    return ks.strip("\n") + "\n"


_KEYSTROKES_RE = re.compile(r'\\?"keystrokes\\?"\s*:\s*\\?"(.*?)\\?"\s*,\s*\\?"duration\\?"', re.DOTALL)


def repair_arguments(parsed: dict[str, Any]) -> dict[str, Any]:
    """Recover ``commands`` when the model wrote JSON inside one XML parameter.

    Observed 2026-10-16 (kira-smoke3): the model opens ``<parameter=analysis>`` and then
    continues in JSON style, so the parser returns
    ``{"analysis": 'I read the statute...", "plan": "...", "commands": {...}}'}`` and
    ``commands`` is missing. The value is the remainder of a JSON object, so re-attaching
    the opening ``{"analysis": "`` usually makes it parse; failing that, the keystrokes are
    pulled out with a regex.
    """
    if "commands" in parsed:
        return parsed
    for key, val in list(parsed.items()):
        if not isinstance(val, str) or "commands" not in val:
            continue
        for candidate in (f'{{"{key}": "{val}', f'{{"{key}": "{val}}}'):
            try:
                fixed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(fixed, dict) and "commands" in fixed:
                return {**parsed, **fixed}
        m = _KEYSTROKES_RE.findall(val)
        if m:
            cmds = [json.loads(f'"{k}"') if "\\" in k else k for k in m]
            return {**parsed, "commands": cmds}
    return parsed


def normalise_commands(cmds: Any) -> list[dict[str, Any]]:
    """Coerce whatever the model put under ``commands`` into a list of dicts."""
    if isinstance(cmds, str):
        try:
            cmds = json.loads(cmds)
        except json.JSONDecodeError:
            cmds = [cmds]
    if isinstance(cmds, dict):
        cmds = [cmds]
    if not isinstance(cmds, list):
        return []
    out: list[dict[str, Any]] = []
    for c in cmds:
        if isinstance(c, dict):
            c = dict(c)
            c["keystrokes"] = clean_keystrokes(str(c.get("keystrokes", "")))
        elif isinstance(c, str):
            c = {"keystrokes": clean_keystrokes(c), "duration": 1.0}
        else:
            continue
        if c["keystrokes"]:
            out.append(c)
    return out


def repair_tool_call(tc: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of an execute_commands tool call with well-formed arguments."""
    fn = tc.get("function") or {}
    if fn.get("name") != "execute_commands":
        return tc
    args = fn.get("arguments", "{}")
    try:
        parsed = json.loads(args) if isinstance(args, str) else dict(args)
    except (json.JSONDecodeError, TypeError):
        return tc
    if not isinstance(parsed, dict):
        return tc
    parsed = repair_arguments(parsed)
    parsed["commands"] = normalise_commands(parsed.get("commands", []))
    return {**tc, "function": {**fn, "arguments": json.dumps(parsed)}}


class AgentHarness(_BaselineKira):
    @staticmethod
    def name() -> str:
        return "terminus-kira-local"

    def _extract_tool_calls(self, response) -> list[dict[str, Any]]:
        """Repair arguments *before* they enter the chat history.

        baseline_kira echoes the raw tool calls back to the server on the next turn; vLLM
        json-parses ``function.arguments`` while rendering the chat template and rejects the
        malformed ones with HTTP 400 ``Expecting value`` (9 of 65 trials in sara-kira-run1,
        2026-10-16). Repairing here fixes both the history and the parsing below.
        """
        return [repair_tool_call(tc) for tc in super()._extract_tool_calls(response)]

    def _parse_tool_calls(self, tool_calls: list[dict[str, Any]]):
        return super()._parse_tool_calls([repair_tool_call(tc) for tc in tool_calls])
