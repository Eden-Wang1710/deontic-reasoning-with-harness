"""BcpSRVC: the agent class of the solver-reader-verifier-compaction BCP-harness v0.1
(claude_docs/12 §5). It is the released CLM agent (`ClmAgent`: its loop, its context file,
its budget control, unchanged) with two additions that cannot be made from a skill:

* the completion gate: the submit command is executed as the skill's `bcp_submit`, which
  prints the submit marker only when the tools server accepts the submit (after a verifier
  round, or after two refused attempts);
* the end of the run: the skill's `bcp_init` writes the empty state table before the first
  step, and `bcp_finalize` writes the answer file from the state table's Best answer when
  the run ended without one.

Reader and verifier are commands of the skill `bcp_solver_v1`, served by bcp/tools_server.py.
v0.2 uses the same class with `solver_skill_name=bcp_solver_v2` (bcp/run_bcpsrvc.sh, VERSION=v02).
Run with bcp/run_bcpsrvc.sh (needs ~/envs/clm, where the CLM package is installed).
"""
from __future__ import annotations

import logging
from typing import Any

from clm_harness.clm_agent.harness import ClmAgent
from clm_harness.utils.finish_policy import is_submit_command

logger = logging.getLogger(__name__)


class BcpSRVC(ClmAgent):
    @staticmethod
    def name() -> str:
        return "bcpsrvc"

    def version(self) -> str:
        return "0.1"

    def __init__(self, *args: Any, solver_skill_name: str = "bcp_solver_v1", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._skill = f"{self._skills.mount_dir}/{solver_skill_name}"
        inner = self._ctx.execute

        async def execute(command: str, environment: Any) -> Any:
            if is_submit_command(command):
                command = f"{self._skill}/bcp_submit"
            return await inner(command, environment)

        self._ctx.execute = execute

    async def setup(self, environment: Any) -> None:
        await super().setup(environment)
        await environment.exec(command=f"{self._skill}/bcp_init", timeout_sec=60)

    async def run(self, instruction: str, environment: Any, context: Any) -> None:
        try:
            await super().run(instruction, environment, context)
        finally:
            try:
                await environment.exec(command=f"{self._skill}/bcp_finalize", timeout_sec=120)
            except Exception as exc:  # noqa: BLE001 - the end of a run must not raise
                logger.warning("bcp_finalize failed: %s: %s", type(exc).__name__, exc)
