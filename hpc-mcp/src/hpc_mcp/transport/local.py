# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# Local transport, for when the agent is already on a cluster node.
#
# This is what stops the on-cluster and local placements becoming two products. The tools
# and the policy engine are identical either way; only this class changes, so a guard added
# for laptop users protects on-cluster users too.

from __future__ import annotations

import asyncio
import os
import shutil
import time

from ..errors import TransportError
from .base import CommandResult


class LocalTransport:
    """Runs the same self-contained scripts on the machine the agent is running on."""

    def __init__(self) -> None:
        self._bash = shutil.which("bash")
        if not self._bash:
            raise TransportError("bash was not found in PATH.")

    @property
    def description(self) -> str:
        return "local shell on this machine"

    async def run_script(self, script: str, *, timeout: float) -> CommandResult:
        started = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                self._bash,
                "-s",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ},
            )
        except OSError as exc:
            raise TransportError(f"Could not start bash: {exc}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(script.encode()), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return CommandResult(
                exit_code=124,
                stdout="",
                stderr=(
                    f"Command exceeded the {timeout:g}s timeout and was killed. If the "
                    f"work legitimately takes longer, it belongs in a batch job."
                ),
                duration_seconds=time.monotonic() - started,
                timed_out=True,
            )

        return CommandResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
            duration_seconds=time.monotonic() - started,
        )

    async def close(self) -> None:
        return None
