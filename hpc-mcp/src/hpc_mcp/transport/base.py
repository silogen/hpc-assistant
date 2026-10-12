# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# The transport seam.
#
# Everything above this layer works in terms of "run this script somewhere and give me the
# result". That keeps one tool surface and one policy engine working whether the agent is
# on a laptop reaching the cluster over SSH, already on a cluster node, or eventually
# talking to a site-hosted API — which is the layer most likely to end up site-owned.

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@runtime_checkable
class Transport(Protocol):
    """Runs a self-contained bash script and returns its result."""

    @property
    def description(self) -> str:
        """Human-readable placement, for site_info."""

    async def run_script(self, script: str, *, timeout: float) -> CommandResult: ...

    async def close(self) -> None: ...
