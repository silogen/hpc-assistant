# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# The template is rendered by a shell script and validated by Python. Nothing else connects
# the two, so without this test they can drift apart silently and the first person to find
# out is a user running init for the first time.

from __future__ import annotations

from pathlib import Path

import pytest

from hpc_mcp.config import FILE_ACCESS_MODES, load_config

TEMPLATE = (
    Path(__file__).resolve().parents[2] / "setup" / "templates" / "hpc-assistant-local.json"
)

SUBSTITUTIONS = {
    "{{HPC_SITE}}": "LUMI",
    "{{REMOTE_HOST}}": "lumi",
    "{{REMOTE_ROOT}}": "/scratch/project_465000000/work",
    "{{SLURM_ACCOUNT}}": "project_465000000",
    "{{DEFAULT_PARTITION}}": "small-g",
    "{{LOCAL_ROOT}}": "/home/me/projects/myapp",
    "{{SSHFS_MOUNTPOINT}}": "/home/me/mnt/lumi",
}


def render(tmp_path: Path, mode: str) -> Path:
    text = TEMPLATE.read_text(encoding="utf-8")
    for placeholder, value in {**SUBSTITUTIONS, "{{FILE_ACCESS_MODE}}": mode}.items():
        text = text.replace(placeholder, value)
    assert "{{" not in text, "a placeholder went unsubstituted"
    path = tmp_path / ".hpc-assistant.json"
    path.write_text(text, encoding="utf-8")
    return path


class TestShippedTemplate:
    def test_the_template_exists_where_the_init_script_looks_for_it(self):
        assert TEMPLATE.is_file()

    @pytest.mark.parametrize("mode", FILE_ACCESS_MODES)
    def test_it_loads_in_every_file_access_mode(self, tmp_path, mode):
        config = load_config(render(tmp_path, mode))
        assert config.file_access.mode == mode
        assert config.remote.host == "lumi"
        assert config.remote.project_root == "/scratch/project_465000000/work"
        assert config.slurm.partition == "small-g"
        assert config.slurm.extra_directives == ()
        assert config.login_commands == (
            "lumi-allocations",
            "lumi-quota",
            "lumi-workspaces",
        )
        assert config.env_probes == ("ROCM_PATH",)
        # The site's container images live outside any project. Without this the
        # assistant cannot reach them. Both collections are listed: the slurm skill's
        # examples point at /appl/local/laifs/containers, and a path the skill documents
        # but the config refuses is worse than one that is merely missing.
        assert config.remote.readable_paths == (
            "/appl/local/containers",
            "/appl/local/laifs/containers",
        )

    def test_the_declared_limits_match_the_template_defaults(
        self, tmp_path
    ):
        limits = load_config(render(tmp_path, "rsync")).limits
        assert limits.max_read_bytes == 262_144
        assert limits.max_output_bytes == 65_536
        assert limits.max_list_entries == 500
        assert limits.max_find_depth == 3
        assert limits.scheduler_min_interval_seconds == 10
        assert limits.await_job_initial_interval_seconds == 10
        assert limits.await_job_max_interval_seconds == 120
        assert limits.command_timeout_seconds == 120

    def test_the_template_declares_no_credentials(self):
        text = TEMPLATE.read_text(encoding="utf-8").lower()
        for term in ("password", "passphrase", "private_key", "identityfile", "token"):
            assert term not in text, f"the template mentions {term}"

    def test_the_template_is_documented_inline(self, tmp_path):
        # Users edit this file by hand, and the _comment keys are how it explains itself.
        # They must survive the loader rather than tripping validation.
        text = TEMPLATE.read_text(encoding="utf-8")
        assert text.count("_comment") >= 5
        load_config(render(tmp_path, "rsync"))

    def test_the_template_points_at_harness_approval(self, tmp_path):
        # Per-call prompts are the agent config, not this file. The comment is how a
        # reader looking here finds the overlay instead of expecting a key the loader
        # would have to ignore.
        text = TEMPLATE.read_text(encoding="utf-8")
        assert "permission.hpc_*" in text
        assert "mcp__hpc__*" in text
        assert "denies edits of this file" in text
        load_config(render(tmp_path, "rsync"))
