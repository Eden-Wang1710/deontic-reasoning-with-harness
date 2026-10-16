"""baseline_kira with tool-call argument normalisation for locally served open models.

The meta-harness ``baseline_kira`` agent (DAR's KIRA harness) expects the ``commands``
argument of ``execute_commands`` to be a JSON array of ``{"keystrokes", "duration"}``
objects. Qwen3.5 served through vLLM's ``qwen3_xml`` tool parser emits a single object
(or occasionally a list of plain strings) for that parameter, and ``baseline_kira``
then crashes with ``AttributeError: 'str' object has no attribute 'get'`` before the
first command runs (observed 2026-10-16, see claude_docs/07 §1).

This subclass only reshapes the arguments into the expected form and strips stray
``<parameter=...>`` tags from keystrokes; prompts, tools,
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
    ks = ks.lstrip("\n")
    if ks and not ks.endswith("\n"):
        ks += "\n"
    return ks


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


class AgentHarness(_BaselineKira):
    @staticmethod
    def name() -> str:
        return "terminus-kira-local"

    def _parse_tool_calls(self, tool_calls: list[dict[str, Any]]):
        fixed = []
        for tc in tool_calls:
            fn = (tc.get("function") or {})
            if fn.get("name") == "execute_commands":
                args = fn.get("arguments", "{}")
                try:
                    parsed = json.loads(args) if isinstance(args, str) else dict(args)
                except (json.JSONDecodeError, TypeError):
                    parsed = None
                if isinstance(parsed, dict):
                    parsed["commands"] = normalise_commands(parsed.get("commands", []))
                    tc = {**tc, "function": {**fn, "arguments": json.dumps(parsed)}}
            fixed.append(tc)
        return super()._parse_tool_calls(fixed)
