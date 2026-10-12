# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hpc_mcp.config import load_config
from hpc_mcp.mounts import MountTable
from hpc_mcp.policy import Policy
from hpc_mcp.session import Session
from hpc_mcp.transport.base import CommandResult


def config_document(**overrides) -> dict:
    document = {
        "site": "lumi",
        "remote": {
            "host": "lumi",
            "project_root": "/scratch/project_465000000/work",
            "account": "project_465000000",
        },
        "slurm": {"default_partition": "small-g", "default_time": "00:30:00"},
        "preamble": {
            "modules": ["LUMI/24.03", "rocm/6.0.3"],
            "env": {"MY_VAR": "value with spaces"},
            "commands": [],
        },
        "file_access": {"mode": "rsync"},
        "limits": {"max_find_depth": 3, "max_output_bytes": 2048},
        "audit": {"log": ".hpc-assistant/audit.log"},
    }
    document.update(overrides)
    return document


@pytest.fixture
def config(tmp_path: Path):
    document = config_document()
    document["file_access"]["local_root"] = str(tmp_path)
    path = tmp_path / ".hpc-assistant.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return load_config(path)


@pytest.fixture
def policy(config):
    # An empty mount table means "everything is local disk", which keeps the path tests
    # independent of whatever is mounted on the machine running them.
    return Policy(config, mounts=MountTable(entries=[]))


class RecordingTransport:
    """Captures the scripts it is asked to run instead of running them."""

    def __init__(self, results: list[CommandResult] | None = None) -> None:
        self.scripts: list[str] = []
        self._results = list(results or [])
        self.closed = False

    @property
    def description(self) -> str:
        return "recording transport"

    async def run_script(self, script: str, *, timeout: float) -> CommandResult:
        self.scripts.append(script)
        if self._results:
            return self._results.pop(0)
        return CommandResult(exit_code=0, stdout="", stderr="", duration_seconds=0.0)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def transport():
    return RecordingTransport()


@pytest.fixture
def session(config, policy, transport):
    return Session(config, policy, transport)
