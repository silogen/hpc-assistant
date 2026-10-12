# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# Server assembly.

from __future__ import annotations

from pathlib import Path

from mcp.server import MCPServer

from .config import Config, load_config
from .policy import Policy
from .session import Session
from .tools import files, jobs, shell, site, slurm
from .transport import LocalTransport, SSHTransport
from .transport.base import Transport

INSTRUCTIONS = """\
Tools for working with an HPC cluster from this machine.

Call site_info first: it reports the cluster's partitions, the project's bounds, and where
files live, which saves probing the filesystem to find out.

Three things are worth knowing before you start.

Search file contents in the local working copy using your own tools. The cluster's
filesystem is a parallel filesystem whose metadata servers are shared by every user on the
machine, so recursive search there degrades it for everyone. Remote search is by filename
only, and it is depth-limited.

Send compute work to a compute node with run_on_compute. Compilers, interpreters, package
managers and profilers are refused on the login node.

After submitting a job, report the job id and stop. Do not call await_job and do not poll
squeue. When the user asks how a job is doing, call squeue or sacct once and report.
"""


def build_server(
    config: Config | None = None,
    *,
    config_path: Path | None = None,
    transport: Transport | None = None,
) -> tuple[MCPServer, Session]:
    """Assemble the server. Returns the session too, so callers can close the connection."""
    resolved_config = config or load_config(config_path)
    policy = Policy(resolved_config)
    resolved_transport = transport or SSHTransport(resolved_config.remote.host)
    session = Session(resolved_config, policy, resolved_transport)

    server = MCPServer(
        name="hpc-mcp",
        title="HPC Assistant bridge",
        version="0.1.0",
        instructions=INSTRUCTIONS,
    )
    for module in (site, slurm, jobs, files, shell):
        module.register(server, session)
    return server, session


def build_local_server(
    config_path: Path | None = None,
) -> tuple[MCPServer, Session]:
    """Assemble a server for an agent that is already running on a cluster node."""
    resolved_config = load_config(config_path)
    return build_server(
        resolved_config, transport=LocalTransport()
    )
