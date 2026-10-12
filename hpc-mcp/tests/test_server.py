# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# Assembly tests. These catch the failures that only appear once the MCP machinery looks at
# the tool signatures — a schema it cannot build, or a tool name that quietly went missing.

from __future__ import annotations

import asyncio
import json

import pytest

from mcp.server.mcpserver.exceptions import ToolError

from hpc_mcp.server import build_server
from hpc_mcp.transport.base import CommandResult

from conftest import RecordingTransport, config_document

EXPECTED_TOOLS = {
    "await_job",
    "find_files",
    "list_dir",
    "read_file",
    "run_command",
    "run_on_compute",
    "sacct",
    "sbatch",
    "scancel",
    "scontrol_show_job",
    "sinfo",
    "site_info",
    "squeue",
    "tail_file",
    "write_file",
}


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def built(config, transport):
    server, session = build_server(config, transport=transport)
    return server, session, transport


class TestToolSurface:
    def test_every_expected_tool_is_registered(self, built):
        server, _, _ = built
        tools = run(server.list_tools())
        assert {tool.name for tool in tools} == EXPECTED_TOOLS

    def test_every_tool_has_a_description_and_a_schema(self, built):
        server, _, _ = built
        for tool in run(server.list_tools()):
            assert tool.description, f"{tool.name} has no description"
            assert tool.input_schema.get("type") == "object", f"{tool.name} has no schema"

    def test_the_scheduler_tools_are_named_after_their_commands(self, built):
        server, _, _ = built
        names = {tool.name for tool in run(server.list_tools())}
        # Naming is what lets a skill written for an on-cluster agent work unchanged here.
        assert {"squeue", "sacct", "sinfo", "sbatch", "scancel"} <= names

    def test_the_instructions_tell_the_agent_where_to_search(self, built):
        server, _, _ = built
        assert "local working copy" in (server.instructions or "")

    def test_the_instructions_do_not_tell_the_agent_to_wait_after_submit(self, built):
        server, _, _ = built
        text = server.instructions or ""
        assert "report the job id and stop" in text
        assert "Do not call await_job" in text


class TestCallingThrough:
    def test_calling_a_tool_reaches_the_transport(self, built):
        server, _, transport = built
        run(server.call_tool("squeue", {}))
        assert len(transport.scripts) == 1
        script = transport.scripts[0]
        assert script.rstrip().endswith("--me")
        assert "exec squeue" in script

    def test_a_refusal_reaches_the_agent_with_its_suggestion_intact(self, built):
        server, _, transport = built
        with pytest.raises(ToolError) as caught:
            run(server.call_tool("run_command", {"command": ["make"]}))
        # The suggestion is the part that changes what the model does next, so it has to
        # survive the trip through the MCP error path.
        assert "run_on_compute" in str(caught.value)
        assert transport.scripts == []

    def test_an_invalid_job_id_is_refused_before_any_dispatch(self, built):
        server, _, transport = built
        with pytest.raises(ToolError):
            run(server.call_tool("scancel", {"job_id": "; rm -rf /"}))
        assert transport.scripts == []

    def test_sbatch_needs_a_script_path_inside_the_project(self, built):
        server, _, transport = built
        with pytest.raises(ToolError) as caught:
            run(server.call_tool("sbatch", {"script_path": "/etc/passwd"}))
        assert "outside the project root" in str(caught.value)
        assert transport.scripts == []

    def test_a_traversal_refusal_names_both_alternatives(self, built):
        server, _, transport = built
        with pytest.raises(ToolError) as caught:
            run(server.call_tool("run_command", {"command": ["grep", "-r", "x", "."]}))
        message = str(caught.value)
        assert "your own machine" in message and "find_files" in message
        assert transport.scripts == []


class TestSbatchPreflight:
    def test_a_rejected_request_is_not_submitted(self, config, policy):
        from hpc_mcp.session import Session

        transport = RecordingTransport(
            [
                CommandResult(
                    exit_code=1,
                    stdout="",
                    stderr="sbatch: error: invalid partition specified",
                    duration_seconds=0.0,
                )
            ]
        )
        server, _ = build_server(config, transport=transport)
        result = run(server.call_tool("sbatch", {"script_path": "run.sbatch"}))
        text = str(result.content)
        assert "nothing was submitted" in text
        # One dispatch: the pre-flight. The submission never happened.
        assert len(transport.scripts) == 1
        assert "--test-only" in transport.scripts[0]

    def test_an_accepted_request_reports_the_job_id(self, config, policy):
        transport = RecordingTransport(
            [
                CommandResult(
                    exit_code=0,
                    stdout="",
                    stderr="sbatch: Job 1234567 to start at 2026-08-17T16:00:00",
                    duration_seconds=0.0,
                ),
                CommandResult(
                    exit_code=0, stdout="1234567;lumi\n", stderr="", duration_seconds=0.0
                ),
            ]
        )
        server, _ = build_server(config, transport=transport)
        result = run(server.call_tool("sbatch", {"script_path": "run.sbatch"}))
        text = str(result.content)
        assert "1234567" in text
        assert "Not waiting" in text
        assert "await_job" not in text

    def test_sbatch_without_an_account_does_not_invent_one(self, tmp_path):
        document = config_document()
        document["remote"]["account"] = ""
        document["file_access"]["local_root"] = str(tmp_path)
        document.setdefault("limits", {})
        document["limits"]["scheduler_min_interval_seconds"] = 0.01
        path = tmp_path / ".hpc-assistant.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        from hpc_mcp.config import load_config

        config = load_config(path)
        transport = RecordingTransport(
            [
                CommandResult(
                    exit_code=0, stdout="", stderr="sbatch: Job 1 to start", duration_seconds=0.0
                ),
                CommandResult(exit_code=0, stdout="1\n", stderr="", duration_seconds=0.0),
            ]
        )
        server, _ = build_server(config, transport=transport)
        run(server.call_tool("sbatch", {"script_path": "run.sbatch"}))
        assert "--account" not in transport.scripts[0]
