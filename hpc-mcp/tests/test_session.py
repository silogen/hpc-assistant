# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
import json

import pytest

from hpc_mcp.errors import PolicyViolation, RemoteCommandError
from hpc_mcp.transport.base import CommandResult

from conftest import RecordingTransport


def run(coroutine):
    """Run one coroutine. Avoids a plugin dependency for the handful of async tests."""
    return asyncio.run(coroutine)


class TestDispatch:
    def test_a_command_is_dispatched_as_one_self_contained_script(self, session, transport):
        run(session.execute(["wc", "-l", "notes.txt"], tool="run_command"))
        assert len(transport.scripts) == 1
        script = transport.scripts[0]
        assert "cd /scratch/project_465000000/work" in script
        assert "module load LUMI/24.03 rocm/6.0.3" in script
        assert script.rstrip().endswith("exec wc -l notes.txt")

    def test_the_working_directory_is_bounded_by_the_project_root(self, session):
        with pytest.raises(PolicyViolation):
            run(session.execute(["ls"], tool="run_command", cwd="/etc"))

    def test_a_relative_working_directory_resolves_inside_the_root(self, session, transport):
        run(session.execute(["ls"], tool="run_command", cwd="src"))
        assert "cd /scratch/project_465000000/work/src" in transport.scripts[0]

    def test_a_refused_command_never_reaches_the_transport(self, session, transport):
        with pytest.raises(PolicyViolation):
            run(session.execute(["make"], tool="run_command"))
        assert transport.scripts == []

    def test_check_raises_on_a_non_zero_exit(self, config, policy):
        from hpc_mcp.session import Session

        transport = RecordingTransport(
            [CommandResult(exit_code=1, stdout="", stderr="boom", duration_seconds=0.1)]
        )
        session = Session(config, policy, transport)
        with pytest.raises(RemoteCommandError) as caught:
            run(session.execute(["squeue"], tool="squeue", check=True))
        assert caught.value.exit_code == 1
        assert "boom" in str(caught.value)

    def test_without_check_a_failure_is_returned_for_the_agent_to_reason_about(
        self, config, policy
    ):
        from hpc_mcp.session import Session

        transport = RecordingTransport(
            [CommandResult(exit_code=2, stdout="", stderr="no such job", duration_seconds=0.0)]
        )
        session = Session(config, policy, transport)
        outcome = run(session.execute(["scancel", "1"], tool="scancel"))
        assert outcome.exit_code == 2
        assert not outcome.ok


class TestOutputCapping:
    def test_oversized_output_is_capped_before_the_agent_sees_it(self, config, policy):
        from hpc_mcp.session import Session

        transport = RecordingTransport(
            [
                CommandResult(
                    exit_code=0, stdout="x" * 10_000, stderr="", duration_seconds=0.0
                )
            ]
        )
        session = Session(config, policy, transport)
        outcome = run(session.execute(["ls"], tool="run_command"))
        assert outcome.truncated
        assert len(outcome.stdout.encode()) < 10_000
        assert "omitted by hpc-mcp output cap" in outcome.stdout


class TestSchedulerCallsSkipThePreamble:
    """Measured on LUMI: `module load` costs about 1.5s against a 1s round trip.

    Paying that on every squeue is most of what an agent's remote latency would be, and a
    scheduler client is site infrastructure that does not need the project's environment.
    """

    def test_a_scheduler_command_is_dispatched_without_the_preamble(
        self, session, transport
    ):
        run(session.execute(["squeue", "--me"], tool="squeue"))
        script = transport.scripts[0]
        assert "module load" not in script
        # Still bounded and still self-contained; only the environment is left out.
        assert "cd /scratch/project_465000000/work" in script
        assert script.rstrip().endswith("exec squeue --me")

    def test_an_ordinary_command_still_gets_it(self, session, transport):
        run(session.execute(["git", "status"], tool="run_command"))
        assert "module load LUMI/24.03 rocm/6.0.3" in transport.scripts[0]

    def test_a_site_can_ask_for_it_back(self, tmp_path):
        import json

        from hpc_mcp.config import load_config
        from hpc_mcp.mounts import MountTable
        from hpc_mcp.policy import Policy
        from hpc_mcp.session import Session

        from conftest import config_document

        document = config_document()
        document["preamble"]["apply_to_scheduler"] = True
        document["file_access"]["local_root"] = str(tmp_path)
        path = tmp_path / ".hpc-assistant.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        config = load_config(path)

        transport = RecordingTransport()
        session = Session(config, Policy(config, mounts=MountTable(entries=[])), transport)
        run(session.execute(["squeue", "--me"], tool="squeue"))
        assert "module load LUMI/24.03 rocm/6.0.3" in transport.scripts[0]


class TestSymlinkedProjectRoot:
    """LUMI reaches /scratch/<project> through /pfs/lustrep<n>, and says so in `pwd`."""

    def test_the_physical_path_is_learned_from_the_cluster(self, config, policy):
        from hpc_mcp.session import Session

        physical = "/pfs/lustrep3/scratch/project_465000000/work"
        transport = RecordingTransport(
            [CommandResult(exit_code=0, stdout=physical + "\n", stderr="", duration_seconds=0.0)]
        )
        session = Session(config, policy, transport)
        assert run(session.learn_root_aliases()) == physical
        assert policy.root_aliases == (physical,)

    def test_a_root_that_is_not_a_symlink_teaches_nothing(self, config, policy):
        from hpc_mcp.session import Session

        transport = RecordingTransport(
            [
                CommandResult(
                    exit_code=0,
                    stdout=config.remote.project_root + "\n",
                    stderr="",
                    duration_seconds=0.0,
                )
            ]
        )
        session = Session(config, policy, transport)
        assert run(session.learn_root_aliases()) is None
        assert policy.root_aliases == ()

    def test_an_unreachable_cluster_is_not_fatal(self, config, policy):
        from hpc_mcp.session import Session

        transport = RecordingTransport(
            [CommandResult(exit_code=255, stdout="", stderr="no route", duration_seconds=0.0)]
        )
        session = Session(config, policy, transport)
        assert run(session.learn_root_aliases()) is None


class TestThrottling:
    def test_scheduler_commands_are_throttled_and_others_are_not(self, session):
        run(session.execute(["squeue", "--me"], tool="squeue"))
        assert session.policy.scheduler_limiter.delay_for("scheduler") > 0

        session.policy.scheduler_limiter._last.clear()
        run(session.execute(["hostname"], tool="run_command"))
        assert session.policy.scheduler_limiter.delay_for("scheduler") == 0


class TestAudit:
    def test_every_dispatch_is_recorded(self, session, config):
        run(session.execute(["squeue", "--me"], tool="squeue"))
        run(session.execute(["hostname"], tool="run_command"))

        records = [
            json.loads(line)
            for line in config.audit_log_path.read_text(encoding="utf-8").splitlines()
        ]
        assert [record["tool"] for record in records] == ["squeue", "run_command"]
        assert records[0]["argv"] == ["squeue", "--me"]
        assert records[0]["cwd"] == "/scratch/project_465000000/work"
        assert "duration_seconds" in records[0]
