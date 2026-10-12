# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# Each test corresponds to a policy guarantee, so a change that breaks one fails here
# rather than quietly changing what the assistant will do on the login node.

from __future__ import annotations

import pytest

from hpc_mcp.errors import PolicyViolation
from hpc_mcp.policy import truncate_output


class TestPathBounds:
    def test_relative_paths_resolve_inside_the_project_root(self, policy):
        assert policy.normalize_remote_path("src/main.c") == (
            "/scratch/project_465000000/work/src/main.c"
        )

    def test_the_project_root_itself_is_allowed(self, policy):
        assert policy.normalize_remote_path(".") == "/scratch/project_465000000/work"

    @pytest.mark.parametrize(
        "path",
        [
            "/etc/passwd",
            "../../etc/passwd",
            "/scratch/project_465000000/work/../../other",
            "src/../../../home/someone/.ssh/id_ed25519",
            "/scratch/project_465000000/work-adjacent",
        ],
    )
    def test_escapes_are_refused(self, policy, path):
        with pytest.raises(PolicyViolation):
            policy.normalize_remote_path(path)

    def test_a_sibling_prefix_is_not_treated_as_inside(self, policy):
        # "/scratch/.../work-adjacent" starts with the root as a string but is a different
        # directory. String prefix checks get this wrong; this one must not.
        with pytest.raises(PolicyViolation):
            policy.normalize_remote_path("/scratch/project_465000000/work-adjacent/x")

    def test_null_bytes_are_refused(self, policy):
        with pytest.raises(PolicyViolation):
            policy.normalize_remote_path("src/main\x00.c")

    def test_nothing_outside_the_root_is_readable_by_default(self, policy):
        # readable_paths is opt-in: a config that does not declare it keeps the original
        # single boundary.
        with pytest.raises(PolicyViolation):
            policy.normalize_remote_path("/appl/local/containers/x.sif")


class TestReadablePaths:
    """Shared software lives outside any one project; reading it must be possible without
    widening what the assistant can damage."""

    ALLOWED = "/appl/local/containers"

    @pytest.fixture
    def policy(self, tmp_path):
        import json

        from hpc_mcp.config import load_config
        from hpc_mcp.mounts import MountTable
        from hpc_mcp.policy import Policy
        from conftest import config_document

        document = config_document()
        document["remote"]["readable_paths"] = [self.ALLOWED]
        path = tmp_path / ".hpc-assistant.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return Policy(load_config(path), mounts=MountTable(entries=[]))

    def test_a_listed_path_can_be_read(self, policy):
        target = f"{self.ALLOWED}/sif-images/lumi-jax.sif"
        assert policy.normalize_remote_path(target) == target

    def test_the_listed_directory_itself_can_be_read(self, policy):
        assert policy.normalize_remote_path(self.ALLOWED) == self.ALLOWED

    def test_a_listed_path_cannot_be_written(self, policy):
        with pytest.raises(PolicyViolation) as caught:
            policy.normalize_remote_path(f"{self.ALLOWED}/x.sif", write=True)
        assert "readable but not writable" in str(caught.value)

    def test_a_sibling_prefix_of_a_listed_path_is_still_refused(self, policy):
        with pytest.raises(PolicyViolation):
            policy.normalize_remote_path("/appl/local/containers-other/x")

    def test_unlisted_paths_are_still_refused(self, policy):
        with pytest.raises(PolicyViolation):
            policy.normalize_remote_path("/etc/passwd")

    def test_a_traversal_out_of_a_listed_path_is_refused(self, policy):
        with pytest.raises(PolicyViolation):
            policy.normalize_remote_path(f"{self.ALLOWED}/../../../etc/passwd")

    def test_site_info_names_the_readable_paths(self, policy, transport):
        # Discoverability is the point. A path the agent cannot learn is a path it will
        # guess, and a guess that is refused is indistinguishable from one that does not
        # exist — which is exactly how a run was lost to `/appl/local/laifs/containers`.
        import asyncio

        from hpc_mcp.server import build_server

        server, _ = build_server(policy.config, transport=transport)
        tools = {t.name: t for t in asyncio.run(server.list_tools())}
        assert "readable_paths" in (tools["site_info"].description or "")


class TestSymlinkedRootAliases:
    """A second spelling of the root is accepted; it does not become a second root.

    On LUMI /scratch/<project> resolves into /pfs/lustrep<n>/scratch/<project>, so the
    cluster hands back paths under a prefix the config never mentions. Accepting them is
    necessary; accepting anything under the alias's parent would give away the bounds.
    """

    ALIAS = "/pfs/lustrep3/scratch/project_465000000/work"

    @pytest.fixture
    def aliased(self, policy):
        policy.add_root_alias(self.ALIAS)
        return policy

    def test_an_alias_rooted_path_is_folded_onto_the_configured_root(self, aliased):
        assert aliased.normalize_remote_path(f"{self.ALIAS}/src/main.c") == (
            "/scratch/project_465000000/work/src/main.c"
        )

    def test_the_alias_root_itself_resolves_to_the_configured_root(self, aliased):
        assert aliased.normalize_remote_path(self.ALIAS) == (
            "/scratch/project_465000000/work"
        )

    @pytest.mark.parametrize(
        "path",
        [
            "/pfs/lustrep3/scratch/project_465000000/other",
            "/pfs/lustrep3/scratch/project_999999999/work",
            "/pfs/lustrep3/etc/passwd",
            "/pfs/lustrep3/scratch/project_465000000/work-adjacent/x",
            f"{ALIAS}/../../../../etc/passwd",
        ],
    )
    def test_the_alias_does_not_widen_the_bounds(self, aliased, path):
        with pytest.raises(PolicyViolation):
            aliased.normalize_remote_path(path)

    def test_the_configured_root_still_works(self, aliased):
        assert aliased.normalize_remote_path("src/main.c") == (
            "/scratch/project_465000000/work/src/main.c"
        )

    def test_an_alias_equal_to_the_root_is_not_recorded(self, policy):
        policy.add_root_alias("/scratch/project_465000000/work")
        assert policy.root_aliases == ()

    def test_the_filesystem_root_is_never_an_alias(self, policy):
        policy.add_root_alias("/")
        assert policy.root_aliases == ()

    def test_an_alias_bounds_argv_paths_too(self, aliased):
        # No exception: the physical path names a file inside the project.
        aliased.validate_argv(["wc", "-l", f"{self.ALIAS}/notes.txt"], tool="run_command")
        with pytest.raises(PolicyViolation):
            aliased.validate_argv(
                ["wc", "-l", "/pfs/lustrep3/scratch/project_999999999/x"],
                tool="run_command",
            )

    def test_an_alias_spelled_project_script_is_refused_on_the_login_node(self, aliased):
        with pytest.raises(PolicyViolation) as caught:
            aliased.validate_argv([f"{self.ALIAS}/run.sh"], tool="run_command")
        assert "run_on_compute" in str(caught.value)


class TestComputeStaysOffTheLoginNode:
    @pytest.mark.parametrize(
        "binary", ["make", "cmake", "gcc", "hipcc", "python3", "pip", "pytest", "tar", "rocprofv3"]
    )
    def test_compute_binaries_are_refused(self, policy, binary):
        with pytest.raises(PolicyViolation) as caught:
            policy.validate_argv([binary, "--version"], tool="run_command")
        assert "run_on_compute" in str(caught.value)

    @pytest.mark.parametrize("binary", ["bash", "sh", "env", "xargs", "nohup", "timeout"])
    def test_launchers_are_refused_because_they_bypass_every_other_check(
        self, policy, binary
    ):
        with pytest.raises(PolicyViolation):
            policy.validate_argv([binary, "-c", "make"], tool="run_command")

    @pytest.mark.parametrize(
        "binary",
        [
            "awk", "gawk", "mawk", "sed", "vi", "vim", "emacs", "nano", "less", "more",
            "man", "watch", "flock", "stdbuf", "ionice", "script", "rsync", "scp", "sftp",
            "tclsh", "lua", "php",
        ],
    )
    def test_programs_that_can_run_a_shell_are_refused(self, policy, binary):
        with pytest.raises(PolicyViolation) as caught:
            policy.validate_argv([binary, "--version"], tool="run_command")
        assert "out of reach" in str(caught.value)

    def test_awk_is_allowed_in_a_batch_script(self, policy):
        argv = ["awk", "BEGIN{system(\"make\")}"]
        assert (
            policy.validate_argv(argv, tool="run_on_compute", enforce_placement=False)
            == argv
        )

    @pytest.mark.parametrize("binary", ["srun", "salloc"])
    def test_interactive_allocations_are_refused(self, policy, binary):
        with pytest.raises(PolicyViolation) as caught:
            policy.validate_argv([binary, "-n", "1", "hostname"], tool="run_command")
        assert "run_on_compute" in str(caught.value)

    def test_compute_binaries_are_allowed_when_bound_for_a_batch_script(self, policy):
        argv = policy.validate_argv(
            ["make", "-j", "8"], tool="run_on_compute", enforce_placement=False
        )
        assert argv == ["make", "-j", "8"]

    def test_absolute_paths_to_compute_binaries_are_still_caught(self, policy):
        with pytest.raises(PolicyViolation):
            policy.validate_argv(["/usr/bin/make"], tool="run_command")

    @pytest.mark.parametrize(
        "argv",
        [
            ["python3.11", "train.py"],
            ["python3.12", "-c", "1"],
            ["pip3.11", "install", "torch"],
            ["gcc-13", "a.c"],
            ["g++-12", "a.cc"],
            ["cmake3", "."],
        ],
    )
    def test_versioned_compute_binaries_are_refused(self, policy, argv):
        with pytest.raises(PolicyViolation) as caught:
            policy.validate_argv(argv, tool="run_command")
        assert "run_on_compute" in str(caught.value)

    @pytest.mark.parametrize(
        "binary",
        ["mpirun", "mpiexec", "mpicc", "gdb", "valgrind", "perf", "wget", "curl", "jupyter"],
    )
    def test_missing_compute_and_launcher_names_are_refused(self, policy, binary):
        with pytest.raises(PolicyViolation) as caught:
            policy.validate_argv([binary, "--version"], tool="run_command")
        assert "run_on_compute" in str(caught.value)

    @pytest.mark.parametrize(
        "argv0",
        [
            "./a.out",
            "./configure.sh",
            "bin/train",
            "/scratch/project_465000000/work/run.sh",
            "/scratch/project_465000000/work/build.sh",
            "/scratch/project_465000000/work/src/train.sh",
        ],
    )
    def test_project_executables_are_refused_on_the_login_node(self, policy, argv0):
        with pytest.raises(PolicyViolation) as caught:
            policy.validate_argv([argv0], tool="run_command")
        assert "run_on_compute" in str(caught.value)
        assert "project script" in str(caught.value)

    def test_versioned_and_relative_compute_is_allowed_in_a_batch_script(self, policy):
        for argv in (
            ["python3.11", "train.py"],
            ["mpirun", "-n", "8", "./app"],
            ["./a.out"],
            ["/scratch/project_465000000/work/run.sh"],
        ):
            assert (
                policy.validate_argv(argv, tool="run_on_compute", enforce_placement=False)
                == argv
            )

    def test_an_absolute_login_binary_is_still_allowed(self, policy):
        policy.validate_argv(["/usr/bin/ls", "-l"], tool="run_command")


class TestMetadataStorms:
    def test_find_without_maxdepth_is_refused(self, policy):
        with pytest.raises(PolicyViolation) as caught:
            policy.validate_argv(["find", ".", "-name", "*.c"], tool="run_command")
        assert "-maxdepth" in str(caught.value)

    def test_find_beyond_the_limit_is_refused(self, policy):
        with pytest.raises(PolicyViolation):
            policy.validate_argv(
                ["find", ".", "-maxdepth", "9", "-name", "*.c"], tool="run_command"
            )

    def test_find_within_the_limit_is_allowed(self, policy):
        policy.validate_argv(
            ["find", ".", "-maxdepth", "2", "-name", "*.c"], tool="run_command"
        )

    def test_find_exec_is_refused(self, policy):
        with pytest.raises(PolicyViolation):
            policy.validate_argv(
                ["find", ".", "-maxdepth", "1", "-exec", "rm", "{}", ";"],
                tool="run_command",
            )

    @pytest.mark.parametrize(
        "argv",
        [
            ["grep", "-r", "pattern", "."],
            ["grep", "-R", "pattern", "."],
            ["grep", "--recursive", "pattern", "."],
            # Combined short flags are the evasion that a glob-based permission list
            # misses, so it is checked explicitly here.
            ["grep", "-rn", "pattern", "."],
            ["grep", "-in", "-r", "pattern", "."],
            ["rg", "pattern"],
        ],
    )
    def test_recursive_content_search_is_refused(self, policy, argv):
        with pytest.raises(PolicyViolation) as caught:
            policy.validate_argv(argv, tool="run_command")
        message = str(caught.value)
        # The refusal has to name both alternatives, or the model just tries again with a
        # different flag: search contents locally, look up names with find_files.
        assert "your own machine" in message
        assert "find_files" in message

    def test_non_recursive_grep_on_one_file_is_allowed(self, policy):
        policy.validate_argv(["grep", "-n", "pattern", "src/main.c"], tool="run_command")

    @pytest.mark.parametrize("argv", [["ls", "-R"], ["ls", "-Rla"], ["ls", "--recursive"]])
    def test_recursive_listing_is_refused(self, policy, argv):
        with pytest.raises(PolicyViolation):
            policy.validate_argv(argv, tool="run_command")

    def test_plain_ls_is_allowed(self, policy):
        policy.validate_argv(["ls", "-la", "src"], tool="run_command")

    @pytest.mark.parametrize("binary", ["du", "tree", "locate", "ncdu"])
    def test_whole_tree_walkers_are_refused(self, policy, binary):
        with pytest.raises(PolicyViolation):
            policy.validate_argv([binary, "."], tool="run_command")

    def test_du_refusal_does_not_name_a_site_specific_quota_tool(self, policy):
        with pytest.raises(PolicyViolation) as caught:
            policy.validate_argv(["du", "-sh", "."], tool="run_command")
        message = str(caught.value).lower()
        assert "lumi-quota" not in message
        assert "site_info" in message or "login_commands" in message


class TestDocumentedLoginCommands:
    def test_generic_login_commands_are_documented(self, policy):
        policy.assert_documented_login_command(["squeue", "--me"])
        policy.assert_documented_login_command(["quota"])

    def test_site_cli_names_are_not_hardcoded(self, policy):
        with pytest.raises(PolicyViolation) as caught:
            policy.assert_documented_login_command(["lumi-quota"])
        assert "login_commands" in str(caught.value)

    def test_site_cli_names_are_accepted_when_configured(self, config, policy):
        from dataclasses import replace

        from hpc_mcp.mounts import MountTable
        from hpc_mcp.policy import Policy

        configured = replace(config, login_commands=("lumi-quota", "lumi-workspaces"))
        site_policy = Policy(configured, mounts=MountTable(entries=[]))
        site_policy.assert_documented_login_command(["lumi-quota"])


class TestNoShell:
    @pytest.mark.parametrize("operator", ["|", "&&", ";", ">", "2>&1"])
    def test_shell_operators_are_refused_rather_than_passed_through(
        self, policy, operator
    ):
        with pytest.raises(PolicyViolation) as caught:
            policy.validate_argv(["ls", operator, "out"], tool="run_command")
        assert "shell" in str(caught.value).lower()

    def test_a_command_must_be_an_argv_list(self, policy):
        with pytest.raises(PolicyViolation):
            policy.validate_argv("ls -la", tool="run_command")  # type: ignore[arg-type]

    def test_an_empty_command_is_refused(self, policy):
        with pytest.raises(PolicyViolation):
            policy.validate_argv([], tool="run_command")

    def test_globs_are_inert_because_there_is_no_shell_to_expand_them(self, policy):
        # `wc -l *` would fan out over a directory under a shell. As argv, the asterisk
        # reaches wc literally, so it needs no special-casing.
        assert policy.validate_argv(["wc", "-l", "*"], tool="run_command") == [
            "wc",
            "-l",
            "*",
        ]


class TestArgvPathOperands:
    """Flags are a path too. Skipping anything that starts with `-` is how
    `--follow=/etc/passwd` used to walk out of the project without a check."""

    OUTSIDE = "/etc/passwd"
    INSIDE = "/scratch/project_465000000/work/notes.txt"

    @pytest.mark.parametrize(
        "argv",
        [
            ["cat", OUTSIDE],
            ["tail", "--follow", OUTSIDE],
            ["tail", f"--follow={OUTSIDE}"],
            ["wc", "-l", f"--files0-from={OUTSIDE}"],
            ["git", f"--git-dir={OUTSIDE}", "log"],
            ["git", "--work-tree=/etc", "status"],
            ["git", "-C/etc", "status"],
            ["git", "--git-dir", "/etc", "log"],
            ["wc", "-l", "../../etc/passwd"],
            ["git", "--git-dir=../../etc", "log"],
        ],
    )
    def test_a_path_outside_the_root_is_refused_even_inside_a_flag(self, policy, argv):
        with pytest.raises(PolicyViolation) as caught:
            policy.validate_argv(argv, tool="run_command")
        assert "outside the project root" in str(caught.value)

    def test_a_flag_path_inside_the_root_is_allowed(self, policy):
        policy.validate_argv(
            ["git", f"--git-dir={self.INSIDE}/.git", "log"], tool="run_command"
        )
        policy.validate_argv(["cat", self.INSIDE], tool="run_command")

    @pytest.mark.parametrize(
        "argv",
        [
            ["squeue", "--me"],
            ["squeue", "--account=project_465000000"],
            ["ls", "-la"],
            ["git", "log", "--oneline"],
            ["git", "--git-dir=.git", "status"],
            ["cut", "-d/"],
            ["tail", "--follow=name"],
        ],
    )
    def test_flags_that_are_not_paths_are_left_alone(self, policy, argv):
        policy.validate_argv(argv, tool="run_command")


class TestGitLaunchers:
    """git stays on the login node; the ways it starts another program do not."""

    def test_ordinary_git_commands_are_allowed(self, policy):
        policy.validate_argv(["git", "status"], tool="run_command")
        policy.validate_argv(["git", "log", "--oneline", "-n", "5"], tool="run_command")
        policy.validate_argv(["git", "log", "-p"], tool="run_command")
        policy.validate_argv(["git", "submodule", "status"], tool="run_command")
        policy.validate_argv(
            ["git", "submodule", "update", "--init"], tool="run_command"
        )

    def test_default_paginate_is_allowed(self, policy):
        policy.validate_argv(["git", "--paginate", "log"], tool="run_command")
        policy.validate_argv(["git", "-p", "log"], tool="run_command")

    @pytest.mark.parametrize(
        "argv",
        [
            ["git", "-c", "core.pager=make", "log"],
            ["git", "-ccore.pager=make", "log"],
            ["git", "-c", "alias.x=!make", "x"],
            ["git", "--config-env=core.pager=PAGER", "log"],
        ],
    )
    def test_git_c_is_refused(self, policy, argv):
        with pytest.raises(PolicyViolation) as caught:
            policy.validate_argv(argv, tool="run_command")
        assert "-c" in str(caught.value) or "config" in str(caught.value).lower()

    def test_paginate_with_a_non_default_pager_is_refused(self, policy):
        with pytest.raises(PolicyViolation) as caught:
            policy.validate_argv(
                ["git", "-c", "core.pager=make", "--paginate", "log"],
                tool="run_command",
            )
        assert "paginate" in str(caught.value).lower() or "pager" in str(caught.value).lower()

    @pytest.mark.parametrize(
        "argv",
        [
            ["git", "--exec-path=/tmp/evil", "status"],
            ["git", "--exec-path"],
        ],
    )
    def test_exec_path_is_refused(self, policy, argv):
        with pytest.raises(PolicyViolation) as caught:
            policy.validate_argv(argv, tool="run_command")
        assert "--exec-path" in str(caught.value)

    @pytest.mark.parametrize(
        "argv",
        [
            ["git", "submodule", "foreach", "make"],
            ["git", "submodule", "foreach", "--recursive", "make"],
            ["git", "submodule", "--quiet", "foreach", "make"],
            [
                "git",
                "-C",
                "/scratch/project_465000000/work",
                "submodule",
                "foreach",
                "make",
            ],
        ],
    )
    def test_submodule_foreach_is_refused(self, policy, argv):
        with pytest.raises(PolicyViolation) as caught:
            policy.validate_argv(argv, tool="run_command")
        assert "foreach" in str(caught.value)
        assert "run_on_compute" in str(caught.value) or "batch" in str(caught.value)

    def test_submodule_foreach_is_allowed_in_a_batch_script(self, policy):
        argv = ["git", "submodule", "foreach", "make"]
        assert (
            policy.validate_argv(argv, tool="run_on_compute", enforce_placement=False)
            == argv
        )

    def test_git_config_cannot_persist_a_launcher(self, policy):
        with pytest.raises(PolicyViolation) as caught:
            policy.validate_argv(
                ["git", "config", "core.pager", "make"], tool="run_command"
            )
        assert "config" in str(caught.value)
        with pytest.raises(PolicyViolation):
            policy.validate_argv(
                ["git", "config", "alias.x", "!make"], tool="run_command"
            )

    def test_git_config_reads_and_harmless_keys_are_allowed(self, policy):
        policy.validate_argv(
            ["git", "config", "--get", "core.pager"], tool="run_command"
        )
        policy.validate_argv(
            ["git", "config", "user.email", "dev@example.com"], tool="run_command"
        )

    def test_the_filesystem_root_as_a_flag_value_is_refused(self, policy):
        with pytest.raises(PolicyViolation):
            policy.validate_argv(["git", "--git-dir=/", "log"], tool="run_command")


class TestLocalPaths:
    def test_a_path_inside_the_workspace_is_accepted(self, policy, tmp_path):
        target = tmp_path / "notes.md"
        target.write_text("hello", encoding="utf-8")
        assert policy.assert_local_disk(target) == target

    def test_a_path_outside_the_workspace_is_refused(self, policy):
        with pytest.raises(PolicyViolation):
            policy.assert_local_disk("/etc/hosts")

    def test_a_path_on_a_network_mount_is_refused_even_inside_the_workspace(
        self, config, tmp_path
    ):
        from hpc_mcp.mounts import MountTable
        from hpc_mcp.policy import Policy

        mount = tmp_path / "lumi"
        mount.mkdir()
        # The mount table is compared against resolved paths, and a temp directory is
        # often behind a symlink, so record the resolved form.
        policy = Policy(
            config,
            mounts=MountTable(
                entries=[(str(mount.resolve()), "fuse.sshfs"), ("/", "apfs")]
            ),
        )
        with pytest.raises(PolicyViolation) as caught:
            policy.assert_local_disk(mount / "src" / "main.c")
        assert "network filesystem" in str(caught.value)
        assert policy.workspace_is_remote() is False


class TestRateLimiting:
    def test_the_first_call_is_not_delayed(self, policy):
        assert policy.scheduler_limiter.delay_for("scheduler", now=1000.0) == 0.0

    def test_a_second_call_too_soon_is_delayed_by_the_remainder(self, policy):
        limiter = policy.scheduler_limiter
        limiter.record("scheduler", now=1000.0)
        assert limiter.delay_for("scheduler", now=1002.0) == pytest.approx(8.0)

    def test_no_delay_once_the_interval_has_passed(self, policy):
        limiter = policy.scheduler_limiter
        limiter.record("scheduler", now=1000.0)
        assert limiter.delay_for("scheduler", now=1011.0) == 0.0


class TestOutputCapping:
    def test_short_output_is_untouched(self):
        text, truncated = truncate_output("hello", 100)
        assert (text, truncated) == ("hello", False)

    def test_long_output_keeps_both_ends(self):
        text, truncated = truncate_output("A" * 500 + "B" * 500, 200)
        assert truncated
        assert text.startswith("A")
        assert text.rstrip().endswith("B")
        assert "omitted by hpc-mcp output cap" in text
        assert len(text.encode()) < 500


class TestAuditLog:
    def test_every_decision_is_recorded_as_one_json_line(self, policy, config):
        from hpc_mcp.policy import Decision

        policy.audit("run_command", Decision(allowed=True), argv=["ls"], exit_code=0)
        policy.audit(
            "run_command", Decision(allowed=False, reason="compute on login node")
        )

        lines = config.audit_log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2

        import json

        first, second = (json.loads(line) for line in lines)
        assert first["tool"] == "run_command"
        assert first["allowed"] is True
        assert second["allowed"] is False
        assert second["reason"] == "compute on login node"

    def test_an_unwritable_audit_log_does_not_break_the_session(self, policy, config):
        from hpc_mcp.policy import Decision

        config.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        config.audit_log_path.parent.chmod(0o500)
        try:
            policy.audit("run_command", Decision(allowed=True))
        finally:
            config.audit_log_path.parent.chmod(0o700)
