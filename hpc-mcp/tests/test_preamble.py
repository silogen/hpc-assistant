# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

from hpc_mcp.config import Preamble
from hpc_mcp.preamble import build_batch_script, build_script, preamble_lines, render_inline


class TestRendering:
    def test_modules_bring_the_lmod_bootstrap_with_them(self):
        lines = preamble_lines(Preamble(modules=("LUMI/24.03",)))
        joined = "\n".join(lines)
        # Without this, `module` is not a shell function in a non-interactive session and
        # the preamble fails with "module: command not found".
        assert "command -v module" in joined
        assert "module load LUMI/24.03" in joined

    def test_no_bootstrap_when_no_modules_are_declared(self):
        lines = preamble_lines(Preamble(env=(("A", "b"),)))
        assert not any("command -v module" in line for line in lines)

    def test_environment_values_are_quoted(self):
        lines = preamble_lines(Preamble(env=(("MY_VAR", "value with spaces"),)))
        assert lines == ["export MY_VAR='value with spaces'"]

    def test_declaration_order_is_preserved(self):
        lines = preamble_lines(
            Preamble(modules=("a",), env=(("B", "1"),), commands=("echo hi",))
        )
        assert lines[-1] == "echo hi"
        assert lines[-2] == "export B=1"

    def test_the_inline_form_chains_with_and(self):
        rendered = render_inline(Preamble(env=(("A", "1"), ("B", "2"))))
        assert rendered == "export A=1 && export B=2"


class TestDispatchScript:
    def test_the_script_is_self_contained(self, config):
        script = build_script(
            ["squeue", "--me"], cwd="/scratch/p/work", preamble=config.preamble
        )
        assert script.startswith("#!/usr/bin/env bash\n")
        assert "cd /scratch/p/work" in script
        assert script.rstrip().endswith("exec squeue --me")

    def test_pipefail_is_set_but_nounset_is_not(self, config):
        # Lmod's init scripts reference unset variables, so `set -u` breaks the preamble
        # before the command ever runs.
        script = build_script(["true"], cwd="/tmp", preamble=config.preamble)
        assert "set -eo pipefail" in script
        assert "set -u" not in script

    def test_arguments_are_quoted(self, config):
        script = build_script(
            ["grep", "needle in haystack", "file name.txt"],
            cwd="/tmp",
            preamble=Preamble(),
        )
        assert "'needle in haystack'" in script
        assert "'file name.txt'" in script

    def test_a_working_directory_with_spaces_is_quoted(self, config):
        script = build_script(["true"], cwd="/scratch/my project", preamble=Preamble())
        assert "cd '/scratch/my project'" in script

    def test_the_preamble_can_be_skipped(self, config):
        script = build_script(
            ["hostname"], cwd="/tmp", preamble=config.preamble, include_preamble=False
        )
        assert "module load" not in script


class TestShellFunctionCommands:
    """`module` is an Lmod shell function, and `exec` cannot run a function.

    Found against a real login node: run_command(["module", "list"]) exited 127 while
    run_command's own documentation offered it as the example to follow.
    """

    def test_module_is_called_rather_than_exec_d(self, config):
        script = build_script(["module", "list"], cwd="/tmp", preamble=config.preamble)
        assert script.rstrip().endswith("module list")
        assert "exec module" not in script

    def test_ml_is_treated_the_same_way(self, config):
        script = build_script(["ml"], cwd="/tmp", preamble=config.preamble)
        assert "exec ml" not in script

    def test_lmod_is_sourced_even_when_the_project_declares_no_modules(self):
        # Otherwise `module list` fails with "command not found" on a project whose
        # preamble happens to be empty, which is most of them to begin with.
        script = build_script(["module", "list"], cwd="/tmp", preamble=Preamble())
        assert "command -v module" in script

    def test_lmod_is_not_sourced_twice_when_the_preamble_already_did_it(self, config):
        script = build_script(["module", "list"], cwd="/tmp", preamble=config.preamble)
        assert script.count("command -v module") == 1

    def test_the_bootstrap_is_still_added_when_the_preamble_is_skipped(self, config):
        script = build_script(
            ["module", "list"], cwd="/tmp", preamble=config.preamble, include_preamble=False
        )
        assert "command -v module" in script
        assert "module load" not in script

    def test_everything_else_is_still_exec_d(self, config):
        # exec is what keeps a killed dispatch from orphaning a process on the login node,
        # so the exception has to stay narrow.
        script = build_script(["hostname"], cwd="/tmp", preamble=Preamble())
        assert script.rstrip().endswith("exec hostname")


class TestBatchScript:
    def test_directives_and_preamble_come_from_the_same_declaration(self, config):
        script = build_batch_script(
            ["make", "-j", "8"],
            cwd="/scratch/p/work",
            preamble=config.preamble,
            job_name="build",
            account="project_465000000",
            partition="small-g",
            time_limit="00:30:00",
            nodes=1,
            gpus_per_node=1,
            output_pattern="logs/build-%j.out",
        )
        assert "#SBATCH --job-name=build" in script
        assert "#SBATCH --account=project_465000000" in script
        assert "#SBATCH --gpus-per-node=1" in script
        assert "#SBATCH --output=logs/build-%j.out" in script
        # The same preamble as an interactive dispatch, which is the point: a command that
        # works from a tool call must not fail in a job because a module was missing.
        assert "module load LUMI/24.03 rocm/6.0.3" in script
        assert script.rstrip().endswith("make -j 8")

    def test_extra_directives_are_rendered_as_sbatch_lines(self, config):
        script = build_batch_script(
            ["hostname"],
            cwd="/tmp",
            preamble=Preamble(),
            job_name="gpu",
            account="",
            partition="gpu",
            time_limit="00:10:00",
            gpus_per_node=0,
            extra_directives=("--gres=gpu:a100:1", "--mem=32G", "--qos=debug"),
        )
        assert "#SBATCH --partition=gpu" in script
        assert "#SBATCH --gres=gpu:a100:1" in script
        assert "#SBATCH --mem=32G" in script
        assert "#SBATCH --qos=debug" in script
        assert "gpus-per-node" not in script
        assert "--account" not in script

    def test_the_module_bootstrap_includes_tcl_environment_modules(self):
        lines = preamble_lines(Preamble(modules=("gcc/12",)))
        joined = "\n".join(lines)
        assert "${MODULESHOME}/init/bash" in joined
        assert "/usr/share/Modules/init/bash" in joined

    def test_a_cpu_only_job_gets_no_gpu_directive(self, config):
        script = build_batch_script(
            ["hostname"],
            cwd="/tmp",
            preamble=Preamble(),
            job_name="cpu",
            account="acct",
            partition="small",
            time_limit="00:05:00",
            gpus_per_node=0,
        )
        assert "gpus-per-node" not in script

    def test_no_account_directive_when_there_is_no_account(self):
        script = build_batch_script(
            ["hostname"],
            cwd="/tmp",
            preamble=Preamble(),
            job_name="x",
            account="",
            partition="small",
            time_limit="00:05:00",
        )
        assert "--account" not in script
