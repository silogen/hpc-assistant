# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from hpc_mcp import remote_fs
from hpc_mcp.config import load_config
from hpc_mcp.errors import PolicyViolation
from hpc_mcp.mounts import MountTable
from hpc_mcp.policy import Policy
from hpc_mcp.session import Session
from hpc_mcp.transport.base import CommandResult
from hpc_mcp.transport.local import LocalTransport

from conftest import RecordingTransport, config_document


def run(coroutine):
    return asyncio.run(coroutine)


class TestBounds:
    def test_reading_outside_the_project_root_is_refused(self, session):
        with pytest.raises(PolicyViolation):
            run(remote_fs.read_file(session, "/etc/passwd"))

    def test_writing_outside_the_project_root_is_refused(self, session):
        with pytest.raises(PolicyViolation):
            run(remote_fs.write_file(session, "../../etc/cron.d/evil", "content"))

    def test_a_negative_offset_is_refused(self, session):
        with pytest.raises(PolicyViolation):
            run(remote_fs.read_file(session, "notes.md", offset=-1))

    def test_a_read_is_capped_at_the_configured_limit(self, session, transport):
        run(remote_fs.read_file(session, "big.log", max_bytes=100_000_000))
        cap = session.config.limits.max_read_bytes
        assert f"count={cap}" in transport.scripts[0]

    def test_an_oversized_write_is_refused_with_an_alternative(self, session):
        oversized = "x" * (session.config.limits.max_read_bytes + 1)
        with pytest.raises(PolicyViolation) as caught:
            run(remote_fs.write_file(session, "generated.c", oversized))
        assert "file-access mode" in str(caught.value)

    def test_listing_deeper_than_the_limit_is_refused(self, session):
        with pytest.raises(PolicyViolation) as caught:
            run(remote_fs.list_dir(session, ".", depth=9))
        assert "max_find_depth" in str(caught.value)

    def test_find_depth_is_silently_clamped_rather_than_refused(self, session, transport):
        run(remote_fs.find_files(session, "*.c", depth=99))
        assert "-maxdepth 3" in transport.scripts[0]

    def test_find_by_path_is_refused_because_it_is_a_directory_argument(self, session):
        with pytest.raises(PolicyViolation) as caught:
            run(remote_fs.find_files(session, "src/*.c"))
        assert "without slashes" in str(caught.value)


class TestScriptConstruction:
    def test_paths_with_spaces_are_quoted(self, session, transport):
        run(remote_fs.read_file(session, "my results/run 1.log"))
        assert "'/scratch/project_465000000/work/my results/run 1.log'" in transport.scripts[0]

    def test_a_heredoc_delimiter_cannot_be_forged_by_the_content(self, session, transport):
        run(remote_fs.write_file(session, "script.sh", "echo EOF\nEOF\n"))
        script = transport.scripts[0]
        delimiter = script.split("<<'")[1].split("'")[0]
        assert delimiter.startswith("HPC_MCP_")
        # The content contains the word EOF; only the generated delimiter closes the block.
        assert script.count(delimiter) == 2

    def test_a_failed_traversal_is_an_error_not_an_empty_listing(self, config, policy):
        # The scripts end in a pipeline, so a failing `find` still exits zero. Without
        # this check the agent would be told the directory is empty, which is worse than
        # an error because it is confidently wrong.
        transport = RecordingTransport(
            [
                CommandResult(
                    exit_code=0,
                    stdout="",
                    stderr="find: unknown predicate `-printf'",
                    duration_seconds=0.0,
                )
            ]
        )
        session = Session(config, policy, transport)
        with pytest.raises(PolicyViolation) as caught:
            run(remote_fs.list_dir(session, "."))
        assert "unknown predicate" in str(caught.value)

    def test_a_partial_traversal_returns_what_it_found_and_says_so(self, config, policy):
        transport = RecordingTransport(
            [
                CommandResult(
                    exit_code=0,
                    stdout="f  10  visible.txt\n",
                    stderr="find: './private': Permission denied",
                    duration_seconds=0.0,
                )
            ]
        )
        session = Session(config, policy, transport)
        listing = run(remote_fs.list_dir(session, "."))
        assert "visible.txt" in listing
        assert "Permission denied" in listing

    def test_a_genuinely_empty_directory_is_reported_as_empty(self, config, policy):
        transport = RecordingTransport(
            [CommandResult(exit_code=0, stdout="", stderr="", duration_seconds=0.0)]
        )
        session = Session(config, policy, transport)
        listing = run(remote_fs.list_dir(session, "."))
        assert "depth 1" in listing

    def test_the_size_marker_is_parsed_off_the_content(self, config, policy):
        transport = RecordingTransport(
            [
                CommandResult(
                    exit_code=0,
                    stdout=f"{remote_fs.SIZE_MARKER} 4096\nfile body here",
                    stderr="",
                    duration_seconds=0.0,
                )
            ]
        )
        session = Session(config, policy, transport)
        result = run(remote_fs.read_file(session, "notes.md"))
        assert result.total_bytes == 4096
        assert result.content == "file body here"
        assert not result.complete


# The remote scripts rely on GNU coreutils behaviour that the cluster has and macOS does
# not: `dd iflag=skip_bytes` and `find -printf`. Running them against a real shell is worth
# doing, so it happens on Linux and is skipped elsewhere rather than being weakened.
@pytest.mark.skipif(
    sys.platform != "linux", reason="the remote scripts target GNU coreutils"
)
class TestAgainstARealShell:
    @pytest.fixture
    def local_session(self, tmp_path: Path):
        root = tmp_path / "project"
        root.mkdir()
        document = config_document()
        document["remote"]["project_root"] = str(root)
        document["preamble"] = {"modules": [], "env": {}, "commands": []}
        document["file_access"] = {"mode": "rsync", "local_root": str(tmp_path)}
        config_path = tmp_path / ".hpc-assistant.json"
        config_path.write_text(json.dumps(document), encoding="utf-8")
        config = load_config(config_path)
        return Session(config, Policy(config, mounts=MountTable(entries=[])), LocalTransport())

    def test_write_then_read_round_trips(self, local_session):
        run(remote_fs.write_file(local_session, "notes.md", "hello world\n"))
        result = run(remote_fs.read_file(local_session, "notes.md"))
        assert result.content == "hello world\n"
        assert result.total_bytes == 12
        assert result.complete

    def test_reading_from_an_offset_returns_the_rest(self, local_session):
        run(remote_fs.write_file(local_session, "notes.md", "0123456789\n"))
        result = run(remote_fs.read_file(local_session, "notes.md", offset=5))
        assert result.content == "56789\n"

    def test_a_read_stops_at_the_cap_and_says_how_to_continue(self, local_session):
        run(remote_fs.write_file(local_session, "big.log", "A" * 5000))
        result = run(remote_fs.read_file(local_session, "big.log", max_bytes=100))
        assert result.returned_bytes == 100
        assert not result.complete

    def test_tail_returns_the_end_of_the_file(self, local_session):
        run(remote_fs.write_file(local_session, "job.out", "start\n" + "x" * 4000 + "end\n"))
        tail = run(remote_fs.tail_file(local_session, "job.out", max_bytes=64))
        assert tail.rstrip().endswith("end")

    def test_listing_is_depth_limited(self, local_session):
        run(remote_fs.write_file(local_session, "a/b/c/deep.txt", "x"))
        run(remote_fs.write_file(local_session, "top.txt", "x"))
        listing = run(remote_fs.list_dir(local_session, ".", depth=1))
        assert "top.txt" in listing
        assert "deep.txt" not in listing

    def test_find_matches_by_name(self, local_session):
        run(remote_fs.write_file(local_session, "src/kernel.hip", "x"))
        found = run(remote_fs.find_files(local_session, "*.hip", depth=2))
        assert "kernel.hip" in found

    def test_find_reports_no_matches_plainly(self, local_session):
        found = run(remote_fs.find_files(local_session, "*.nonexistent"))
        assert "No files matching" in found

    def test_reading_a_directory_is_refused_with_a_clear_message(self, local_session):
        run(remote_fs.write_file(local_session, "dir/file.txt", "x"))
        with pytest.raises(PolicyViolation) as caught:
            run(remote_fs.read_file(local_session, "dir"))
        assert "directory" in str(caught.value)
