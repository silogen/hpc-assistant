# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# The credential guarantee, checked rather than promised.

from __future__ import annotations

import pytest

from hpc_mcp.errors import TransportError
from hpc_mcp.transport.ssh import (
    SSHTransport,
    _looks_transient,
    assert_no_credential_material,
)


class TestCredentialMaterialIsRefused:
    @pytest.mark.parametrize(
        "argv",
        [
            ["ssh", "-i", "/home/me/.ssh/id_ed25519", "lumi"],
            ["ssh", "-i/home/me/.ssh/id_ed25519", "lumi"],
            ["ssh", "-o", "IdentityFile=/home/me/.ssh/id_ed25519", "lumi"],
            ["ssh", "-o", "identityfile=/key", "lumi"],
            ["ssh", "-oIdentityFile=/key", "lumi"],
            ["ssh", "-o", "PasswordAuthentication=yes", "lumi"],
            ["ssh", "-o", "ProxyCommand=nc %h %p", "lumi"],
            ["ssh", "-o", "PermitLocalCommand=yes", "lumi"],
            ["ssh", "-o", "StrictHostKeyChecking=no", "lumi"],
            ["ssh", "-o", "ForwardAgent=yes", "lumi"],
        ],
    )
    def test_options_that_move_authentication_into_this_process_are_refused(self, argv):
        with pytest.raises(TransportError):
            assert_no_credential_material(argv)

    @pytest.mark.parametrize(
        "argv",
        [
            ["ssh", "-o", "BatchMode=yes", "lumi", "bash", "-s"],
            ["ssh", "-o", "ControlMaster=auto", "-o", "ControlPersist=10m", "lumi"],
            ["ssh", "-T", "-o", "ConnectTimeout=15", "lumi"],
            # -N opens a master connection and must not be mistaken for an identity flag.
            ["ssh", "-N", "-f", "lumi"],
        ],
    )
    def test_the_options_this_bridge_actually_uses_are_accepted(self, argv):
        assert_no_credential_material(argv)

    def test_the_dispatch_argv_contains_no_credential_options(self, monkeypatch):
        transport = SSHTransport("lumi")
        argv = [
            transport._ssh,
            *transport._base_options("/tmp/cm-test"),
            "lumi",
            "bash",
            "-s",
        ]
        assert_no_credential_material(argv)
        assert "-o" in argv and "BatchMode=yes" in argv

    def test_batch_mode_is_always_set_so_nothing_can_block_on_a_prompt(self):
        options = SSHTransport("lumi")._base_options("/tmp/cm-test")
        assert "BatchMode=yes" in options

    def test_multiplexing_is_always_requested(self):
        options = SSHTransport("lumi")._base_options("/tmp/cm-test")
        # Without multiplexing, a session of two hundred operations is two hundred
        # authentications against a shared login node.
        assert "ControlMaster=auto" in options
        assert any(option.startswith("ControlPath=") for option in options)
        assert any(option.startswith("ControlPersist=") for option in options)


class TestTransientFailures:
    """A busy login node sometimes drops the first connection of a session.

    Observed on LUMI: "Connection timed out during banner exchange" on the opening
    connection, with everything afterwards succeeding. That is the machine being busy, not
    the request being wrong, so it earns one retry — and a refused credential does not,
    because repeating it is how an account gets locked.
    """

    @pytest.mark.parametrize(
        "detail",
        [
            "Connection timed out during banner exchange",
            "kex_exchange_identification: read: Connection reset by peer",
            "ssh: connect to host lumi.csc.fi port 22: Operation timed out",
            "client_loop: send disconnect: Broken pipe",
            "ssh: Could not resolve hostname lumi: Temporary failure in name resolution",
            # A loaded login node names the peer rather than saying "remote host".
            "Connection closed by 193.167.209.164 port 22",
            "Connection closed by remote host",
        ],
    )
    def test_a_busy_machine_is_worth_another_attempt(self, detail):
        assert _looks_transient(detail)

    @pytest.mark.parametrize(
        "detail",
        [
            "lumi: Permission denied (publickey).",
            "Host key verification failed.",
            "Bad configuration option: nosuchoption",
            # Reads as transient on the surface, but the rejection is the operative part.
            "Permission denied (publickey). Connection timed out",
        ],
    )
    def test_a_rejection_is_not_retried(self, detail):
        assert not _looks_transient(detail)


class TestConnectionSetupCannotHang:
    """Every dispatch calls ensure_master first, so a hang here stops the assistant dead.

    This is not hypothetical: validating against LUMI, the bridge sat in an indefinite
    wait before its first dispatch — no error, no output, nothing to act on, which is worse
    than failing. Silence is the symptom of an unbounded wait on a subprocess, and this
    module talks to four of them, so each one is now bounded and each bound is asserted
    here rather than assumed.

    The fakes below stand in for a child that misbehaves in the two available ways: one
    that exits while leaving a descriptor held open, and one that simply never returns.
    """

    @pytest.fixture
    def fake_ssh(self, tmp_path):
        script = tmp_path / "fake-ssh"
        script.write_text(
            "#!/bin/sh\n"
            "# `-O check` asks whether a master exists; say no, so a master gets opened.\n"
            'for arg in "$@"; do\n'
            '  if [ "$arg" = "-O" ]; then exit 1; fi\n'
            "done\n"
            "# Background a child that inherits stderr and outlives us, then succeed.\n"
            "( sleep 15 ) &\n"
            "exit 0\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        return script

    def test_a_backgrounded_master_does_not_block_the_caller(self, fake_ssh, tmp_path):
        import asyncio
        import time

        transport = SSHTransport("lumi", control_path=str(tmp_path / "cm"))
        transport._ssh = str(fake_ssh)

        started = time.monotonic()
        asyncio.run(transport.ensure_master())
        elapsed = time.monotonic() - started

        # The child holds stderr for 15s. Waiting on it at all is the bug.
        assert elapsed < 5, f"ensure_master waited {elapsed:.1f}s on the backgrounded child"

    def test_a_wedged_control_socket_does_not_block_the_caller(self, tmp_path):
        """`ssh -O check` against a wedged master can wait indefinitely."""
        import asyncio
        import time

        from hpc_mcp.transport import ssh as ssh_module

        script = tmp_path / "wedged-ssh"
        # exec, so killing this kills the sleep too: a lingering child holding the
        # stdout pipe would be a fake that misbehaves in a second, unintended way.
        script.write_text("#!/bin/sh\nexec sleep 30\n", encoding="utf-8")
        script.chmod(0o755)

        transport = SSHTransport("lumi", control_path=str(tmp_path / "cm"))
        transport._ssh = str(script)

        original = ssh_module.CONTROL_TIMEOUT
        ssh_module.CONTROL_TIMEOUT = 1.0
        try:
            started = time.monotonic()
            alive = asyncio.run(transport._master_is_alive(str(tmp_path / "cm")))
            elapsed = time.monotonic() - started
        finally:
            ssh_module.CONTROL_TIMEOUT = original

        assert alive is False
        assert elapsed < 5, f"the liveness check waited {elapsed:.1f}s"

    def test_reading_the_ssh_config_is_bounded(self, tmp_path):
        """A `Match exec` block runs a command while `ssh -G` parses. It may not return."""
        import asyncio
        import time

        from hpc_mcp.transport import ssh as ssh_module

        script = tmp_path / "slow-ssh"
        # exec, so killing this kills the sleep too: a lingering child holding the
        # stdout pipe would be a fake that misbehaves in a second, unintended way.
        script.write_text("#!/bin/sh\nexec sleep 30\n", encoding="utf-8")
        script.chmod(0o755)

        transport = SSHTransport("lumi")
        transport._ssh = str(script)

        original = ssh_module.CONTROL_TIMEOUT
        ssh_module.CONTROL_TIMEOUT = 1.0
        try:
            started = time.monotonic()
            value = asyncio.run(transport._ssh_config_value("controlpath"))
            elapsed = time.monotonic() - started
        finally:
            ssh_module.CONTROL_TIMEOUT = original

        assert value == ""
        assert elapsed < 5, f"reading the config waited {elapsed:.1f}s"

    def test_a_timeout_falls_back_to_our_own_control_path(self, tmp_path):
        """Giving up on `ssh -G` must still leave multiplexing configured."""
        import asyncio

        from hpc_mcp.transport import ssh as ssh_module

        script = tmp_path / "slow-ssh"
        # exec, so killing this kills the sleep too: a lingering child holding the
        # stdout pipe would be a fake that misbehaves in a second, unintended way.
        script.write_text("#!/bin/sh\nexec sleep 30\n", encoding="utf-8")
        script.chmod(0o755)

        transport = SSHTransport("lumi")
        transport._ssh = str(script)

        original = ssh_module.CONTROL_TIMEOUT
        ssh_module.CONTROL_TIMEOUT = 1.0
        try:
            path = asyncio.run(transport.control_path())
        finally:
            ssh_module.CONTROL_TIMEOUT = original

        assert path.endswith("cm-hpc-mcp-%r@%h:%p")


class TestHostHandling:
    def test_a_user_at_host_target_is_refused(self):
        with pytest.raises(TransportError) as caught:
            SSHTransport("me@lumi.csc.fi")
        assert "~/.ssh/config" in str(caught.value)

    def test_an_alias_is_accepted(self):
        assert SSHTransport("lumi").host == "lumi"

    def test_the_description_names_the_placement(self):
        assert "lumi" in SSHTransport("lumi").description
