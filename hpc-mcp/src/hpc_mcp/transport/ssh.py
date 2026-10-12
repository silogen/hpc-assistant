# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# SSH transport.
#
# Shells out to the system OpenSSH client rather than using a Python SSH library, for two
# reasons that both matter more than convenience.
#
# It inherits the user's ~/.ssh/config, so ProxyJump, certificates, Match blocks and MFA
# keep working exactly as they already do. A library that reimplements the client supports
# a subset and breaks on the sites with the most interesting network topology.
#
# And it means credential material never enters this process. There is no parameter here
# for a key, a key path, a password or a passphrase; authentication happens in the user's
# ssh-agent. That turns "no credential is lent to the assistant" from a promise into a
# property you can check by reading the argv this module builds.

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path

from ..errors import TransportError
from .base import CommandResult

# Option names that would either move authentication into this process or let a remote
# side influence the local one. Asserted against rather than merely avoided, so that a
# future edit cannot quietly reintroduce one.
FORBIDDEN_OPTION_NAMES = frozenset(
    {
        "certificatefile",
        "challengeresponseauthentication",
        "forwardagent",
        "identityagent",
        "identityfile",
        "kbdinteractiveauthentication",
        "localcommand",
        "passwordauthentication",
        "permitlocalcommand",
        "proxycommand",
        "stricthostkeychecking",
    }
)


def assert_no_credential_material(argv: list[str]) -> None:
    """Fail loudly if an argv contains credential or local-execution options.

    A self-check, not input validation: nothing outside this module builds these argv
    lists. It exists so the "no credential is lent to the assistant" guarantee is held up
    by a test rather than by code review.
    """

    def reject(token: str, why: str) -> None:
        raise TransportError(
            f"Refusing to build an ssh command containing {token!r}: {why} "
            f"Authentication belongs to the user's ssh-agent, not to this bridge."
        )

    index = 0
    while index < len(argv):
        token = argv[index]
        lowered = token.lower()

        if lowered == "-i":
            reject(token, "it passes an identity file.")
        if lowered.startswith("-i") and len(lowered) > 2 and not lowered.startswith("-in"):
            reject(token, "it passes an identity file.")

        option: str | None = None
        if lowered == "-o" and index + 1 < len(argv):
            option = argv[index + 1]
            index += 1
        elif lowered.startswith("-o") and len(lowered) > 2:
            option = token[2:]

        if option is not None:
            name = option.replace(" ", "").split("=", 1)[0].lower()
            if name in FORBIDDEN_OPTION_NAMES:
                reject(option, f"{name} is not ours to set.")

        index += 1


# Talking to an existing control socket is local IPC, so it is either quick or wedged.
CONTROL_TIMEOUT = 10.0

# Failures that say "the network or the login node was busy", as distinct from "this
# credential was refused". Observed on LUMI: a banner-exchange timeout on the first
# connection of a session, with every subsequent connection succeeding.
TRANSIENT_FAILURES = (
    "banner exchange",
    "broken pipe",
    # Not "closed by remote host": a loaded login node names the peer instead, as in
    # "Connection closed by 193.167.209.164 port 22".
    "connection closed by",
    "connection refused",
    "connection reset",
    "connection timed out",
    "no route to host",
    "operation timed out",
    "temporary failure in name resolution",
    "timed out",
)


async def _reap(proc: asyncio.subprocess.Process) -> None:
    """Kill a subprocess and collect it, briefly.

    Killing without collecting leaves a zombie and an asyncio transport that is still live
    when the event loop closes. The wait is bounded in turn, because the reason we are here
    is that this process was not behaving.
    """
    try:
        proc.kill()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=2)
    except asyncio.TimeoutError:
        pass


def _looks_transient(detail: str) -> bool:
    lowered = detail.lower()
    # An explicit rejection is never worth a second attempt, whatever else it mentions.
    if "permission denied" in lowered or "host key verification failed" in lowered:
        return False
    return any(marker in lowered for marker in TRANSIENT_FAILURES)


class SSHTransport:
    """One multiplexed OpenSSH connection, reused for every operation in a session.

    Multiplexing is not an optimisation here, it is the thing that makes the connection
    profile defensible: without it a session of two hundred operations is two hundred
    authentications against a shared login node.
    """

    def __init__(
        self,
        host: str,
        *,
        control_persist: str = "10m",
        connect_timeout: int = 15,
        control_path: str | None = None,
    ) -> None:
        if "@" in host:
            raise TransportError(
                f"Refusing host {host!r}: use an alias defined in ~/.ssh/config so that "
                f"connection and authentication settings stay under your control."
            )
        self.host = host
        self.control_persist = control_persist
        self.connect_timeout = connect_timeout
        self._control_path = control_path
        self._ssh = shutil.which("ssh")
        if not self._ssh:
            raise TransportError("ssh was not found in PATH.")
        self._master_checked = False

    @property
    def description(self) -> str:
        return f"ssh host alias {self.host!r}, multiplexed via ControlMaster"

    # ── Connection ───────────────────────────────────────────────────────────────

    async def _ssh_config_value(self, key: str) -> str:
        """Read an effective option from `ssh -G`, which resolves the user's own config.

        Bounded like everything else here. `ssh -G` normally just parses and prints, but a
        `Match exec` block in the user's config runs a command while it does, and that
        command is not ours to trust with an unbounded wait.
        """
        proc = await asyncio.create_subprocess_exec(
            self._ssh,
            "-G",
            self.host,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=CONTROL_TIMEOUT
            )
        except asyncio.TimeoutError:
            await _reap(proc)
            return ""
        for line in stdout.decode(errors="replace").splitlines():
            name, _, value = line.partition(" ")
            if name.lower() == key:
                return value.strip()
        return ""

    async def control_path(self) -> str:
        """Prefer the user's configured ControlPath; fall back to one under ~/.ssh."""
        if self._control_path:
            return self._control_path
        configured = await self._ssh_config_value("controlpath")
        if configured and configured.lower() != "none":
            self._control_path = configured
        else:
            directory = Path.home() / ".ssh"
            directory.mkdir(mode=0o700, exist_ok=True)
            self._control_path = str(directory / "cm-hpc-mcp-%r@%h:%p")
        return self._control_path

    def _base_options(self, control_path: str) -> list[str]:
        return [
            # An agent cannot answer an interactive prompt, and a tool call that blocks
            # forever on one is worse than a clear failure the user can act on.
            "-o", "BatchMode=yes",
            "-o", "ControlMaster=auto",
            "-o", f"ControlPath={control_path}",
            "-o", f"ControlPersist={self.control_persist}",
            "-o", f"ConnectTimeout={self.connect_timeout}",
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-o", "ClearAllForwardings=yes",
            "-T",
        ]

    async def _control_command(self, control_path: str, request: str) -> bool:
        """Ask the running master something. Bounded, because this is the hot path.

        `ssh -O` talks to the control socket, and a socket whose master has wedged — a
        suspended laptop, a dropped VPN — can leave that conversation waiting indefinitely.
        Since this runs before every single dispatch, an unbounded wait here does not
        degrade the assistant, it stops it: no error, no output, nothing to act on.
        """
        proc = await asyncio.create_subprocess_exec(
            self._ssh,
            "-o", f"ControlPath={control_path}",
            "-O", request,
            self.host,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            return await asyncio.wait_for(proc.wait(), timeout=CONTROL_TIMEOUT) == 0
        except asyncio.TimeoutError:
            await _reap(proc)
            return False

    async def _master_is_alive(self, control_path: str) -> bool:
        return await self._control_command(control_path, "check")

    async def ensure_master(self) -> None:
        """Make sure a master connection exists, starting one if it does not.

        Called before every dispatch, because the master can die between calls — a
        suspended laptop or a dropped VPN is the normal case, not the exceptional one.

        A busy login node sometimes drops a connection before the banner, which is a
        property of the machine rather than of the request, so one transient failure is
        retried. Only one, and only for failures that read as transient: repeatedly
        retrying a rejected credential is how an account gets locked.
        """
        control_path = await self.control_path()
        if await self._master_is_alive(control_path):
            self._master_checked = True
            return

        detail = await self._open_master(control_path)
        if detail is not None and _looks_transient(detail):
            await asyncio.sleep(1.5)
            if await self._master_is_alive(control_path):
                self._master_checked = True
                return
            detail = await self._open_master(control_path)

        if detail is not None:
            raise TransportError(
                f"Could not open an SSH connection to {self.host!r}: {detail}\n\n"
                f"This bridge runs ssh in batch mode and never prompts, so an "
                f"authentication failure means the connection has to be established by "
                f"you. Try `ssh {self.host} true` in a terminal, check `ssh-add -l`, and "
                f"confirm the host alias exists in ~/.ssh/config."
            )
        self._master_checked = True

    async def _open_master(self, control_path: str) -> str | None:
        """Start a master connection. Returns None on success, or the failure detail.

        Diagnostics go to a temporary file rather than a pipe, which is not a style
        preference. `-f` sends ssh into the background once it has authenticated, and the
        backgrounded master inherits the descriptors it was given, so a pipe here may never
        reach EOF — the master holds the write end open for as long as it lives. Both of
        asyncio's ways of collecting output then wait for something that will not happen:
        `communicate()` waits for EOF, and `Process.wait()` waits for the pipe transports
        to close as well as for the process to exit. With no pipes there is nothing to wait
        for but the exit, so the timeout below is the whole story.
        """
        argv = [
            self._ssh,
            *self._base_options(control_path),
            "-N",
            "-f",
            self.host,
        ]
        assert_no_credential_material(argv)

        timeout = self.connect_timeout + 5
        with tempfile.TemporaryFile() as diagnostics:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=diagnostics,
            )
            try:
                returncode = await asyncio.wait_for(proc.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                await _reap(proc)
                return f"timed out after {timeout:g}s"

            if returncode != 0:
                diagnostics.seek(0)
                detail = diagnostics.read().decode(errors="replace").strip()
                return detail or f"ssh exited {returncode}"
        return None

    # ── Dispatch ─────────────────────────────────────────────────────────────────

    async def run_script(self, script: str, *, timeout: float) -> CommandResult:
        """Run a self-contained script on the remote host.

        The script is piped to `bash -s` on stdin rather than written to a temporary file.
        Nothing is left on the remote filesystem, there is no cleanup step to fail, and a
        dropped connection cannot leave a stray file behind on shared storage.
        """
        await self.ensure_master()
        control_path = await self.control_path()
        argv = [
            self._ssh,
            *self._base_options(control_path),
            self.host,
            "bash",
            "-s",
        ]
        assert_no_credential_material(argv)

        started = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ},
            )
        except OSError as exc:
            raise TransportError(f"Could not start ssh: {exc}") from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(script.encode()), timeout=timeout
            )
        except asyncio.TimeoutError:
            # Bounded too: the timeout exists to put a ceiling on a dispatch, and an
            # unbounded wait while cleaning it up would quietly remove that ceiling.
            await _reap(proc)
            return CommandResult(
                exit_code=124,
                stdout="",
                stderr=(
                    f"Command exceeded the {timeout:g}s timeout and was killed. If the "
                    f"work legitimately takes longer, it belongs in a batch job."
                ),
                duration_seconds=time.monotonic() - started,
                timed_out=True,
            )

        return CommandResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
            duration_seconds=time.monotonic() - started,
        )

    async def close(self) -> None:
        """Release the multiplexed connection this session opened."""
        if not self._master_checked or not self._control_path:
            return
        await self._control_command(self._control_path, "exit")
        self._master_checked = False
