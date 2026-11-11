"""Per-trial ledger of model calls, and a cost budget that stops a trial (DAR harness).

Decision 2026-11-11 (claude_docs/04 §8): runs are no longer bounded by wall clock, which
depends on the machine, the server settings and the number of concurrent trials. The second
metric next to accuracy is **inference cost**: the tokens a trial consumed, priced with a
fixed price card (``tools/pricing/price_card.json``, OpenRouter prices of the evaluated
model). This mixin gives every harness built on terminus-2 the two things that needs.

1. **Ledger.** Every call to the model, by the solver or by a sub-agent, is appended to
   ``agent/llm_calls.jsonl`` when it returns or fails: role, token usage as the server
   reported it, how the reply ended (``stop`` | ``length`` | ``context`` | ``cancelled`` |
   ``error``) and the material for an offline prompt-cache simulation: the length of the
   prompt in characters and the longest prefix it shares with any earlier prompt of the same
   trial (``lcp_chars``, with the number of that call in ``lcp_call``). A reply cut off by
   an output limit is in the ledger with its tokens, although Harbor's own counters drop it.
   ``tools/inference_cost.py`` prices the ledger.

2. **Cost budget.** With ``cost_budget_usd`` set, the trial stops before the next solver
   step once the calls so far cost that much at the no-cache price (input and output
   tokens times the card's prices). The step in progress, with the sub-agent runs it
   started, is finished first, so a trial can end slightly above the budget. Whatever is
   in the answer file at that point is graded. ``agent/cost_budget.json`` records the
   budget, the amount spent and whether the budget ended the trial.

At the end of the trial the token totals of the trial result (``n_input_tokens``,
``n_output_tokens``, ``cost_usd``) are taken from the ledger, so they include cut-off calls;
``cost_usd`` is the no-cache cost.

The mixin wraps two methods of the agent's LLM object (``call`` and
``_extract_usage_info``). Calls of one trial are sequential, which the bookkeeping relies on.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harbor.llms.base import ContextLengthExceededError, OutputLengthExceededError

PRICE_CARD_PATH = Path(__file__).resolve().parent / "pricing" / "price_card.json"


class CostBudgetExhausted(Exception):
    """The trial has spent its cost budget; raised before a solver step, caught by the loop."""


def load_price_card(path: str | Path | None = None) -> dict[str, float]:
    """USD per token for ``input``, ``output`` and ``cache_read`` from a price card file."""
    card = json.loads(Path(path or PRICE_CARD_PATH).read_text())
    per_million = card["usd_per_million_tokens"]
    return {k: float(per_million[k]) / 1e6 for k in ("input", "output", "cache_read")}


def common_prefix_len(a: str, b: str) -> int:
    """Length of the longest common prefix of two strings."""
    n = min(len(a), len(b))
    if a[:n] == b[:n]:
        return n
    lo, hi = 0, n  # a[:lo] == b[:lo] and a[:hi] != b[:hi]
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if a[:mid] == b[:mid]:
            lo = mid
        else:
            hi = mid
    return lo


def prompt_text(messages: list[Any]) -> str:
    """One string per request, in message order, for prefix comparison between requests.

    Only roles and contents are rendered; the chat template's own tokens are the same for
    every request and add a few tokens per message.
    """
    parts = []
    for m in messages:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "")
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, default=str)
        parts.append(f"<{role}>\n{content}\n")
    return "".join(parts)


def _thinking_on(kwargs: dict) -> bool:
    template_kwargs = (kwargs.get("extra_body") or {}).get("chat_template_kwargs") or {}
    return template_kwargs.get("enable_thinking", True) is not False


class CostLedgerMixin:
    """Ledger of model calls and an optional cost budget; see the module docstring.

    Place it before the terminus-2 class in the bases. A sub-agent sets ``self._llm_role``
    around its calls (``_llm_role_as``); calls outside one belong to the solver.
    """

    def __init__(
        self,
        *args,
        cost_budget_usd: float | str | None = None,
        price_card_path: str | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._price = load_price_card(price_card_path)
        self._cost_budget_usd = (
            float(cost_budget_usd)
            if str(cost_budget_usd).strip().lower() not in {"", "none", "off"}
            and float(cost_budget_usd) > 0
            else None
        )
        self._spent_usd = 0.0
        self._budget_exhausted = False
        self._llm_role = "solver"
        self._ledger: list[dict[str, Any]] = []
        self._ledger_prompts: list[str] = []
        self._ledger_usages: list[Any] = []
        self._wrap_llm()

    # ----------------------------------------------------------------- ledger

    def _llm_role_as(self, role: str) -> "_RoleScope":
        return _RoleScope(self, role)

    def _wrap_llm(self) -> None:
        llm = self._llm
        inner_call = llm.call
        inner_usage = getattr(llm, "_extract_usage_info", None)

        if inner_usage is not None:
            # Called once per reply the server completed, also for a reply that the LLM
            # layer then rejects as cut off, and once per attempt when a call is retried.
            def extract_usage_info(response):
                info = inner_usage(response)
                if info is not None:
                    self._ledger_usages.append(info)
                return info

            llm._extract_usage_info = extract_usage_info

        async def call(*args, **kwargs):
            prompt = kwargs["prompt"] if "prompt" in kwargs else args[0]
            history = kwargs.get("message_history")
            if history is None:
                history = args[1] if len(args) > 1 else []
            text = prompt_text(list(history) + [{"role": "user", "content": prompt}])
            lcp_chars, lcp_call = 0, None
            for i, earlier in enumerate(self._ledger_prompts):
                shared = common_prefix_len(text, earlier)
                if shared >= lcp_chars and shared > 0:
                    lcp_chars, lcp_call = shared, i + 1
            self._ledger_prompts.append(text)
            entry: dict[str, Any] = {
                "n": len(self._ledger_prompts),
                "role": self._llm_role,
                "started": datetime.now(timezone.utc).isoformat(),
                "thinking": _thinking_on(kwargs),
                "max_tokens": kwargs.get("max_tokens"),
                "prompt_chars": len(text),
                "lcp_chars": lcp_chars,
                "lcp_call": lcp_call,
            }
            self._ledger_usages = []
            started = time.time()
            finish = "error"
            try:
                response = await inner_call(*args, **kwargs)
                finish = "stop"
                return response
            except OutputLengthExceededError:
                finish = "length"
                raise
            except ContextLengthExceededError:
                finish = "context"
                raise
            except asyncio.CancelledError:
                finish = "cancelled"
                raise
            finally:
                usages = self._ledger_usages
                self._ledger_usages = []
                entry.update(
                    {
                        "finish": finish,
                        "seconds": round(time.time() - started, 3),
                        # More than one when the LLM layer retried the request.
                        "n_replies": len(usages),
                        "prompt_tokens": sum(u.prompt_tokens for u in usages),
                        "completion_tokens": sum(u.completion_tokens for u in usages),
                        "server_cached_tokens": sum(u.cache_tokens for u in usages),
                    }
                )
                self._spent_usd += (
                    entry["prompt_tokens"] * self._price["input"]
                    + entry["completion_tokens"] * self._price["output"]
                )
                entry["spent_usd"] = round(self._spent_usd, 6)
                self._ledger.append(entry)
                try:
                    with (self.logs_dir / "llm_calls.jsonl").open("a") as f:
                        f.write(json.dumps(entry) + "\n")
                except Exception as exc:  # the ledger must not end a trial
                    self.logger.error(f"could not write the call ledger: {exc!r}")

        llm.call = call

    # ----------------------------------------------------------------- budget

    async def _handle_llm_interaction(self, *args, **kwargs):
        if (
            self._cost_budget_usd is not None
            and self._spent_usd >= self._cost_budget_usd
        ):
            raise CostBudgetExhausted(
                f"spent {self._spent_usd:.4f} USD of {self._cost_budget_usd:.4f} USD"
            )
        return await super()._handle_llm_interaction(*args, **kwargs)

    async def _summarize(self, *args, **kwargs):
        with self._llm_role_as("summarizer"):
            return await super()._summarize(*args, **kwargs)

    async def _run_agent_loop(self, *args, **kwargs) -> None:
        try:
            await super()._run_agent_loop(*args, **kwargs)
        except CostBudgetExhausted as exc:
            self._budget_exhausted = True
            self.logger.info(f"Cost budget exhausted, ending the trial: {exc}")

    async def run(self, instruction, environment, context) -> None:
        try:
            await super().run(instruction, environment, context)
        finally:
            self._close_ledger(context)

    def _close_ledger(self, context) -> None:
        """Take the trial's token totals from the ledger and write ``cost_budget.json``."""
        n_in = sum(e["prompt_tokens"] for e in self._ledger)
        n_out = sum(e["completion_tokens"] for e in self._ledger)
        context.n_input_tokens = n_in
        context.n_output_tokens = n_out
        context.n_cache_tokens = sum(e["server_cached_tokens"] for e in self._ledger)
        context.cost_usd = self._spent_usd
        record = {
            "cost_budget_usd": self._cost_budget_usd,
            "spent_usd": round(self._spent_usd, 6),
            "budget_exhausted": self._budget_exhausted,
            "usd_per_token": self._price,
            "n_llm_calls": len(self._ledger),
            "n_input_tokens": n_in,
            "n_output_tokens": n_out,
        }
        context.metadata = {**(context.metadata or {}), "cost_budget": record}
        try:
            (self.logs_dir / "cost_budget.json").write_text(
                json.dumps(record, indent=2)
            )
        except Exception as exc:
            self.logger.error(f"could not write cost_budget.json: {exc!r}")


class _RoleScope:
    """``with agent._llm_role_as("reader"):`` tags the calls made inside with that role."""

    def __init__(self, agent: CostLedgerMixin, role: str):
        self._agent, self._role = agent, role

    def __enter__(self) -> None:
        self._previous = self._agent._llm_role
        self._agent._llm_role = self._role

    def __exit__(self, *exc) -> None:
        self._agent._llm_role = self._previous
