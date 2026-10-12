# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# The one place where a command becomes a dispatch.
#
# Every tool goes through here, so validation, rate limiting, output capping and the
# audit record hold generally rather than tool by tool, and a new tool cannot forget them.

from __future__ import annotations

import shlex
from dataclasses import dataclass

from .config import Config
from .errors import RemoteCommandError
from .policy import Decision, Policy, quote_argv
from .preamble import build_script
from .transport.base import CommandResult, Transport


@dataclass
class Outcome:
    """A completed dispatch, with output already capped."""

    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    truncated: bool
    timed_out: bool

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class Session:
    def __init__(self, config: Config, policy: Policy, transport: Transport) -> None:
        self.config = config
        self.policy = policy
        self.transport = transport

    # ── Startup ──────────────────────────────────────────────────────────────────

    async def learn_root_aliases(self) -> str | None:
        """Ask the cluster what the project root's physical path is, once, at startup.

        One round trip buys the difference between accepting the paths the cluster hands
        back and refusing them. Returns the physical path when it differs from the
        configured one, so a caller can report it.
        """
        root = self.config.remote.project_root
        result = await self.transport.run_script(
            f"set -e\ncd -- {shlex.quote(root)}\npwd -P\n", timeout=30
        )
        if result.exit_code != 0:
            return None
        physical = result.stdout.strip().splitlines()
        if not physical or physical[-1] == root:
            return None
        self.policy.add_root_alias(physical[-1])
        return physical[-1]

    # ── Dispatch ─────────────────────────────────────────────────────────────────

    async def execute(
        self,
        argv: list[str],
        *,
        tool: str,
        cwd: str | None = None,
        timeout: float | None = None,
        include_preamble: bool = True,
        check: bool = False,
    ) -> Outcome:
        """Validate a user-supplied command, then run it.

        `check=True` raises on a non-zero exit instead of returning it, for the tools
        where a failure is never something the agent should reason about further.
        """
        argv = self.policy.validate_argv(argv, tool=tool)
        working_dir = (
            self.policy.normalize_remote_path(cwd, write=True)
            if cwd
            else self.config.remote.project_root
        )

        waited = 0.0
        if self.policy.is_scheduler_command(argv):
            waited = await self.policy.scheduler_limiter.acquire("scheduler")
            # A scheduler query needs the site's Slurm clients, not the project's compilers,
            # and `module load` is the most expensive line in the preamble — measured at
            # about 1.5s per dispatch on LUMI against a 1s round trip. Paying that on every
            # squeue is the difference between an assistant that feels responsive and one
            # that does not. Sites where Slurm itself arrives by module can set
            # preamble.apply_to_scheduler.
            if not self.config.preamble.apply_to_scheduler:
                include_preamble = False

        script = build_script(
            argv,
            cwd=working_dir,
            preamble=self.config.preamble,
            include_preamble=include_preamble,
        )
        outcome = await self._dispatch(
            script,
            tool=tool,
            timeout=timeout,
            detail={"argv": argv, "cwd": working_dir, "throttled_seconds": round(waited, 2)},
        )
        if check and not outcome.ok:
            raise RemoteCommandError(
                quote_argv(argv), outcome.exit_code, outcome.stdout, outcome.stderr
            )
        return outcome

    async def run_template(
        self,
        script: str,
        *,
        tool: str,
        timeout: float | None = None,
        detail: dict | None = None,
        check: bool = False,
    ) -> Outcome:
        """Run a script this package built itself.

        Distinct from `execute` on purpose. These scripts are templates written here with
        every interpolated value already quoted and every path already bounded, so they do
        not pass through argv validation — which would reject the pipelines they rely on.
        Nothing from a tool argument reaches them unquoted.
        """
        outcome = await self._dispatch(script, tool=tool, timeout=timeout, detail=detail)
        if check and not outcome.ok:
            raise RemoteCommandError(tool, outcome.exit_code, outcome.stdout, outcome.stderr)
        return outcome

    async def _dispatch(
        self,
        script: str,
        *,
        tool: str,
        timeout: float | None,
        detail: dict | None = None,
    ) -> Outcome:
        effective_timeout = timeout or self.config.limits.command_timeout_seconds
        result: CommandResult = await self.transport.run_script(
            script, timeout=effective_timeout
        )

        stdout, out_truncated = self.policy.truncate(result.stdout)
        stderr, err_truncated = self.policy.truncate(result.stderr)
        truncated = out_truncated or err_truncated

        self.policy.audit(
            tool,
            Decision(allowed=True),
            exit_code=result.exit_code,
            duration_seconds=round(result.duration_seconds, 3),
            stdout_bytes=len(result.stdout.encode()),
            truncated=truncated,
            timed_out=result.timed_out,
            **(detail or {}),
        )

        return Outcome(
            exit_code=result.exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=result.duration_seconds,
            truncated=truncated,
            timed_out=result.timed_out,
        )

    async def close(self) -> None:
        await self.transport.close()
