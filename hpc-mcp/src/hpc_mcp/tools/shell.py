# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# The deliberate escape hatch.
#
# Named tools cover the known cases; this covers the rest, because a tool surface with no
# general path forces users to abandon it the first time they need something unanticipated.
# It is bounded by the same policy engine as everything else, so widening it is a decision
# about ergonomics, not about safety.

from __future__ import annotations

from ..session import Session


def register(server, session: Session) -> None:
    @server.tool()
    async def run_command(
        command: list[str],
        cwd: str | None = None,
        timeout_seconds: int | None = None,
    ) -> dict:
        """Run a single short command on the login node.

        For anything that computes, use run_on_compute — compilers, interpreters, package
        managers and profilers are refused here. There is no shell, so pass the command as
        an argv list; pipes, redirection and chaining are not available.

        Args:
            command: The command as an argv list, for example ["module", "list"].
            cwd: Working directory, inside the project root. Defaults to the project root.
            timeout_seconds: Override the configured command timeout.
        """
        outcome = await session.execute(
            command,
            tool="run_command",
            cwd=cwd,
            timeout=float(timeout_seconds) if timeout_seconds else None,
        )
        return {
            "exit_code": outcome.exit_code,
            "stdout": outcome.stdout,
            "stderr": outcome.stderr,
            "duration_seconds": round(outcome.duration_seconds, 2),
            "truncated": outcome.truncated,
            "timed_out": outcome.timed_out,
        }
