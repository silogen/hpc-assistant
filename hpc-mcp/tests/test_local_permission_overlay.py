# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# The overlay lives in setup/config.sh. Nothing else writes permission.hpc_* / mcp__hpc__*,
# so without this test the init path and the docs can drift and the first person to notice
# is someone whose assistant talks to the cluster without asking.

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CONFIG_SH = REPO / "setup" / "config.sh"
OPENCODE_TEMPLATE = REPO / "setup" / "templates" / "opencode-anthropic.json"


def _call_overlay(function: str, *args: str) -> None:
    quoted = " ".join(f"'{argument}'" for argument in args)
    result = subprocess.run(
        ["bash", "-c", f"source '{CONFIG_SH}' && {function} {quoted}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr or result.stdout or f"{function} exited {result.returncode}")


class TestOpenCodeOverlay:
    def test_it_asks_for_every_hpc_mcp_tool(self, tmp_path):
        dest = tmp_path / "opencode.json"
        shutil.copy(OPENCODE_TEMPLATE, dest)
        _call_overlay("apply_local_permission_overlay", str(dest), "rsync")
        cfg = json.loads(dest.read_text(encoding="utf-8"))
        assert cfg["permission"]["hpc_*"] == "ask"
        assert cfg["permission"]["external_directory"] == "deny"
        edit = cfg["permission"]["edit"]
        assert edit["*"] == "ask"
        assert edit["opencode.json"] == "deny"
        assert edit[".hpc-assistant.json"] == "deny"
        assert edit["AGENTS.md"] == "deny"
        bash = cfg["permission"]["bash"]
        assert bash["* opencode.json"] == "deny"
        assert bash["* .hpc-assistant.json"] == "deny"
        assert bash["ssh *"] == "ask"
        assert bash["rsync *"] == "ask"
        assert bash["ls *"] == "deny"
        assert bash["find *"] == "deny"
        assert bash["cat *"] == "deny"
        assert bash["ls"] == "allow"
        assert bash["ls -la"] == "allow"
        assert bash["* /*"] == "deny"
        assert bash["* .."] == "deny"
        assert bash["rm -rf *"] == "deny"

    def test_it_is_idempotent(self, tmp_path):
        dest = tmp_path / "opencode.json"
        shutil.copy(OPENCODE_TEMPLATE, dest)
        _call_overlay("apply_local_permission_overlay", str(dest), "rsync")
        _call_overlay("apply_local_permission_overlay", str(dest), "rsync")
        cfg = json.loads(dest.read_text(encoding="utf-8"))
        assert cfg["permission"]["hpc_*"] == "ask"


class TestClaudeOverlay:
    def test_it_asks_for_every_hpc_mcp_tool(self, tmp_path):
        settings = tmp_path / ".claude" / "settings.json"
        _call_overlay("apply_local_claude_permission_overlay", str(settings))
        cfg = json.loads(settings.read_text(encoding="utf-8"))
        assert "mcp__hpc__*" in cfg["permissions"]["ask"]
        assert cfg["permissions"]["blockReadsOutsideWorkingDirectories"] is True
        assert "Bash(ls *)" in cfg["permissions"]["deny"]
        assert "Bash(* /*)" in cfg["permissions"]["deny"]
        assert "Edit(opencode.json)" in cfg["permissions"]["deny"]
        assert "Edit(.hpc-assistant.json)" in cfg["permissions"]["deny"]
        assert "Edit(AGENTS.md)" in cfg["permissions"]["deny"]

    def test_it_merges_without_duplicating(self, tmp_path):
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir()
        settings.write_text(
            json.dumps({"permissions": {"ask": ["mcp__hpc__*"], "allow": ["Read"]}}),
            encoding="utf-8",
        )
        _call_overlay("apply_local_claude_permission_overlay", str(settings))
        cfg = json.loads(settings.read_text(encoding="utf-8"))
        assert cfg["permissions"]["ask"] == ["mcp__hpc__*"]
        assert cfg["permissions"]["allow"] == ["Read"]
