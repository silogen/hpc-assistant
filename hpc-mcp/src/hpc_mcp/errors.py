# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# Error types. Messages are written for the agent, not for a log file: a refusal that
# does not say what to do instead just gets retried in a slightly different form.

from __future__ import annotations


class HpcMcpError(Exception):
    """Base class for every error this bridge raises deliberately."""


class ConfigError(HpcMcpError):
    """The project's .hpc-assistant.json is missing, unreadable, or invalid."""


class PolicyViolation(HpcMcpError):
    """A request was refused because it falls outside the declared bounds.

    Raise with a suggestion whenever one exists. "Refused" teaches the model nothing;
    "refused, submit it as a batch job instead" redirects it.
    """

    def __init__(self, reason: str, suggestion: str | None = None) -> None:
        self.reason = reason
        self.suggestion = suggestion
        super().__init__(f"{reason} {suggestion}".strip() if suggestion else reason)


class TransportError(HpcMcpError):
    """The command could not be dispatched — connection, authentication, or timeout."""


class RemoteCommandError(HpcMcpError):
    """The command ran and failed. Carries the exit code and captured output."""

    def __init__(self, command: str, exit_code: int, stdout: str, stderr: str) -> None:
        self.command = command
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        detail = (stderr or stdout or "").strip()
        super().__init__(
            f"`{command}` exited {exit_code}" + (f": {detail}" if detail else "")
        )
