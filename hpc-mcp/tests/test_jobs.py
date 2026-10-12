# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from mcp.server.mcpserver.exceptions import ToolError

from hpc_mcp.config import load_config
from hpc_mcp.mounts import MountTable
from hpc_mcp.policy import Policy
from hpc_mcp.server import build_server
from hpc_mcp.session import Session
from hpc_mcp.tools.jobs import _parse_scontrol_job, query_state, wait_for_job
from hpc_mcp.transport.base import CommandResult

from conftest import RecordingTransport, config_document


def run(coroutine):
    return asyncio.run(coroutine)


def _session(tmp_path: Path, results: list[CommandResult], document: dict | None = None):
    payload = document or config_document()
    payload["file_access"]["local_root"] = str(tmp_path)
    payload.setdefault("limits", {})
    payload["limits"]["scheduler_min_interval_seconds"] = 0.01
    path = tmp_path / ".hpc-assistant.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    config = load_config(path)
    policy = Policy(config, mounts=MountTable(entries=[]))
    transport = RecordingTransport(results)
    return Session(config, policy, transport), transport


SCONTROL_COMPLETED = """\
JobId=12345 JobName=hpc-mcp-hostname
   UserId=me(1000) GroupId=me(1000)
   JobState=COMPLETED Reason=None Dependency=(null)
   ExitCode=0:0
"""


class TestParseScontrol:
    def test_state_and_exit_code_are_read(self):
        assert _parse_scontrol_job(SCONTROL_COMPLETED) == ("COMPLETED", "0:0")

    def test_missing_state_is_ignored(self):
        assert _parse_scontrol_job("JobId=1 ExitCode=0:0") is None


class TestQueryState:
    def test_squeue_wins_while_the_job_is_live(self, tmp_path):
        session, _ = _session(
            tmp_path,
            [CommandResult(exit_code=0, stdout="RUNNING\n", stderr="", duration_seconds=0.0)],
        )
        assert run(query_state(session, "12345")) == ("RUNNING", None)

    def test_sacct_is_used_once_the_job_has_left_the_queue(self, tmp_path):
        session, _ = _session(
            tmp_path,
            [
                CommandResult(exit_code=0, stdout="", stderr="", duration_seconds=0.0),
                CommandResult(
                    exit_code=0, stdout="COMPLETED|0:0\n", stderr="", duration_seconds=0.0
                ),
            ],
        )
        assert run(query_state(session, "12345")) == ("COMPLETED", "0:0")

    def test_scontrol_is_used_when_sacct_is_missing(self, tmp_path):
        session, transport = _session(
            tmp_path,
            [
                CommandResult(exit_code=0, stdout="", stderr="", duration_seconds=0.0),
                CommandResult(
                    exit_code=127, stdout="", stderr="sacct: command not found", duration_seconds=0.0
                ),
                CommandResult(
                    exit_code=0, stdout=SCONTROL_COMPLETED, stderr="", duration_seconds=0.0
                ),
            ],
        )
        assert run(query_state(session, "12345")) == ("COMPLETED", "0:0")
        assert any("scontrol" in script for script in transport.scripts)

    def test_unknown_is_not_inferred_from_a_failed_sacct_alone(self, tmp_path):
        session, _ = _session(
            tmp_path,
            [
                CommandResult(exit_code=0, stdout="", stderr="", duration_seconds=0.0),
                CommandResult(exit_code=1, stdout="", stderr="error", duration_seconds=0.0),
                CommandResult(exit_code=1, stdout="", stderr="invalid", duration_seconds=0.0),
            ],
        )
        assert run(query_state(session, "12345")) == ("UNKNOWN", None)


class TestWaitForJob:
    def test_a_terminal_state_from_scontrol_ends_the_wait(self, tmp_path):
        session, _ = _session(
            tmp_path,
            [
                CommandResult(exit_code=0, stdout="", stderr="", duration_seconds=0.0),
                CommandResult(exit_code=127, stdout="", stderr="", duration_seconds=0.0),
                CommandResult(
                    exit_code=0, stdout=SCONTROL_COMPLETED, stderr="", duration_seconds=0.0
                ),
            ],
        )
        outcome = run(wait_for_job(session, "12345", timeout_seconds=30))
        assert outcome["state"] == "COMPLETED"
        assert outcome["exit_code"] == "0:0"
        assert outcome["checks"] == 1

    def test_unknown_is_not_treated_as_terminal(self, tmp_path):
        session, _ = _session(
            tmp_path,
            [
                CommandResult(exit_code=0, stdout="", stderr="", duration_seconds=0.0),
                CommandResult(exit_code=127, stdout="", stderr="", duration_seconds=0.0),
                CommandResult(exit_code=1, stdout="", stderr="invalid", duration_seconds=0.0),
            ],
        )
        outcome = run(wait_for_job(session, "999", timeout_seconds=1))
        assert outcome["state"] == "UNKNOWN"
        assert "Stopped waiting" in outcome["note"]
        assert "accounting" in outcome["note"]


class TestRunOnComputeScript:
    def test_config_extra_directives_land_in_the_generated_script(self, tmp_path):
        document = config_document()
        document["slurm"] = {
            "default_partition": "gpu",
            "default_time": "00:10:00",
            "default_nodes": 1,
            "default_gpus_per_node": 0,
            "extra_directives": ["--gres=gpu:a100:1", "--mem=32G"],
        }
        document["remote"]["account"] = ""
        document["file_access"]["local_root"] = str(tmp_path)
        document.setdefault("limits", {})
        document["limits"]["scheduler_min_interval_seconds"] = 0.01
        path = tmp_path / ".hpc-assistant.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        config = load_config(path)
        transport = RecordingTransport(
            [
                CommandResult(
                    exit_code=0,
                    stdout="hpc-mcp: wrote 100 bytes\n",
                    stderr="",
                    duration_seconds=0.0,
                ),
                CommandResult(
                    exit_code=0, stdout="", stderr="sbatch: Job 1 to start", duration_seconds=0.0
                ),
                CommandResult(exit_code=0, stdout="1\n", stderr="", duration_seconds=0.0),
            ]
        )
        server, _ = build_server(config, transport=transport)
        run(
            server.call_tool(
                "run_on_compute",
                {"command": ["hostname"], "wait": False, "job_name": "portability"},
            )
        )
        written = transport.scripts[0]
        assert "#SBATCH --partition=gpu" in written
        assert "#SBATCH --gres=gpu:a100:1" in written
        assert "#SBATCH --mem=32G" in written
        assert "gpus-per-node" not in written
        assert "--account" not in written

    def test_submit_does_not_wait_unless_asked(self, tmp_path):
        document = config_document()
        document["file_access"]["local_root"] = str(tmp_path)
        document.setdefault("limits", {})
        document["limits"]["scheduler_min_interval_seconds"] = 0.01
        path = tmp_path / ".hpc-assistant.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        config = load_config(path)
        transport = RecordingTransport(
            [
                CommandResult(
                    exit_code=0,
                    stdout="hpc-mcp: wrote 100 bytes\n",
                    stderr="",
                    duration_seconds=0.0,
                ),
                CommandResult(
                    exit_code=0, stdout="", stderr="sbatch: Job 1 to start", duration_seconds=0.0
                ),
                CommandResult(exit_code=0, stdout="42\n", stderr="", duration_seconds=0.0),
            ]
        )
        server, _ = build_server(config, transport=transport)
        result = run(
            server.call_tool("run_on_compute", {"command": ["hostname"], "job_name": "nowait"})
        )
        text = result.content[0].text
        payload = json.loads(text)
        assert payload["submitted"] is True
        assert payload["job_id"] == "42"
        assert "state" not in payload
        assert "Not waiting" in payload["note"]
        # write, test-only, sbatch — no squeue/sacct/scontrol from await_job.
        assert len(transport.scripts) == 3
        assert all("squeue" not in script for script in transport.scripts)

    def test_a_bad_extra_directive_is_refused_before_dispatch(self, tmp_path):
        document = config_document()
        document["file_access"]["local_root"] = str(tmp_path)
        path = tmp_path / ".hpc-assistant.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        config = load_config(path)
        transport = RecordingTransport()
        server, _ = build_server(config, transport=transport)
        with pytest.raises(ToolError) as caught:
            run(
                server.call_tool(
                    "run_on_compute",
                    {"command": ["hostname"], "extra_directives": ["rm -rf /"]},
                )
            )
        assert "sbatch options" in str(caught.value)
        assert transport.scripts == []
