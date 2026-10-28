"""terminus-2 with a truthful output-truncation marker (DAR harness, local runs).

Harbor's ``Terminus2`` limits every terminal observation to 10,000 bytes by keeping the
first and last 5,000 bytes and inserting ``[... output limited to 10000 bytes; N
interior bytes omitted ...]`` in between. In the agent loop that function is applied
**twice** to the same output (``_execute_commands`` and again in ``_run_agent_loop``;
``terminus_2.py`` lines 1246 and 1439/1442 in the vendored copy). The first pass yields
~10,070 bytes (two halves plus the marker), so the second pass trims 71 more bytes around
the marker and rewrites it as ``71 interior bytes omitted``. On DAR's SARA tasks the model
``cat``s the 27 KB statute, sees "71 bytes omitted", and in 38/65 trials never reads the
missing 17 KB (claude_docs/06 §1.5, observed 2026-10-16).

Decision 2026-10-16 (claude_docs/04 §8, option "c"): keep the 10,000-byte limit, which is
part of the DAR task design (the agent is expected to navigate the statute with shell
tools), but make the marker truthful. This subclass makes ``_limit_output_length``
idempotent: text that already carries a marker is returned unchanged, so the model sees
the true number of omitted bytes from the first pass. Nothing else is changed; prompts,
parsing, sampling and the loop are inherited.

Usage (from ``harbor-deonticbench/``, with ``tools/`` on PYTHONPATH):

    harbor run --agent-import-path dar_terminus2_local:Terminus2TruthfulMarker ...

``tools/run_dar_qwen35_35b.sh`` with ``AGENT=t2fix`` does this.
"""

import re

from harbor.agents.terminus_2.terminus_2 import Terminus2

MARKER_RE = re.compile(
    r"\[\.\.\. output limited to \d+ bytes; \d+ interior bytes omitted \.\.\.\]"
)


class Terminus2TruthfulMarker(Terminus2):
    """Terminus2 whose output limiter is idempotent (see module docstring)."""

    @staticmethod
    def name() -> str:
        return "terminus-2-truthful-marker"

    def _limit_output_length(self, output: str, max_bytes: int = 10000) -> str:
        if MARKER_RE.search(output):
            return output
        return super()._limit_output_length(output, max_bytes)
