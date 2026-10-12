# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# Entry point.
#
# Note that nothing here writes to stdout when serving: stdout is the MCP transport, and a
# stray print corrupts the protocol stream. Diagnostics go to stderr.

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .config import load_config
from .errors import HpcMcpError
from .policy import Policy
from .session import Session
from .transport import LocalTransport, SSHTransport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hpc-mcp",
        description=(
            "MCP bridge exposing HPC cluster operations as bounded tools over your own "
            "SSH connection."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Path to .hpc-assistant.json. Defaults to the nearest one in this directory "
            "or a parent."
        ),
    )
    parser.add_argument(
        "--placement",
        choices=("remote", "local"),
        default="remote",
        help=(
            "remote: reach the cluster over SSH (the default, for an agent on your own "
            "machine). local: the agent is already on a cluster node."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Validate the configuration, open the connection, run the preamble, and "
            "report — then exit without serving."
        ),
    )
    return parser


async def _check(config_path: Path | None, placement: str) -> int:
    config = load_config(config_path)
    print(f"config          {config.path}")
    print(f"site            {config.site}")
    print(f"remote host     {config.remote.host}")
    print(f"project root    {config.remote.project_root}")
    print(f"account         {config.remote.account or '(unset)'}")
    print(f"file access     {config.file_access.mode}")
    print(f"audit log       {config.audit_log_path}")

    policy = Policy(config)
    local_root = config.file_access.local_root
    if policy.workspace_is_remote():
        print(
            f"workspace       WARNING: {local_root} is on "
            f"{policy.mounts.describe(local_root)}, not local disk. Searching it costs "
            f"the cluster the same as searching remotely."
        )
    else:
        print(f"workspace       {local_root} (local disk)")

    transport = (
        LocalTransport() if placement == "local" else SSHTransport(config.remote.host)
    )
    session = Session(config, policy, transport)
    try:
        outcome = await session.execute(
            ["hostname"], tool="check", include_preamble=False
        )
        if not outcome.ok:
            print(f"connection      FAILED: {outcome.stderr.strip()}", file=sys.stderr)
            return 1
        print(f"connection      ok ({outcome.stdout.strip()})")

        physical = await session.learn_root_aliases()
        if physical:
            print(f"project root    also reachable as {physical} (symlink resolved)")

        if config.preamble.is_empty():
            print("preamble        none declared")
        else:
            probe = await session.execute(
                ["true"], tool="check", include_preamble=True
            )
            if probe.ok:
                declared = ", ".join(config.preamble.modules) or (
                    f"{len(config.preamble.env)} variable(s), "
                    f"{len(config.preamble.commands)} command(s)"
                )
                print(f"preamble        ok ({declared})")
            else:
                print(
                    f"preamble        FAILED: {probe.stderr.strip()}\n"
                    f"                Everything sent to the cluster runs through the "
                    f"preamble, so fix this first.",
                    file=sys.stderr,
                )
                return 1
    finally:
        await session.close()

    print("\nReady. Point your agent's MCP configuration at `hpc-mcp`.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.check:
            return asyncio.run(_check(args.config, args.placement))

        # Imported here so that --check does not pay for loading the MCP machinery.
        from .server import build_server

        transport = LocalTransport() if args.placement == "local" else None
        config = load_config(args.config)
        server, session = build_server(
            config, transport=transport or SSHTransport(config.remote.host)
        )

        # One round trip before serving, so the first path the cluster hands back is
        # already understood. Failure here is not fatal: an agent is often started before
        # the VPN is up, and the connection is re-established per dispatch anyway.
        try:
            asyncio.run(session.learn_root_aliases())
        except (HpcMcpError, OSError) as exc:
            print(f"hpc-mcp: could not resolve the project root yet: {exc}", file=sys.stderr)

        server.run("stdio")
        return 0
    except HpcMcpError as exc:
        print(f"hpc-mcp: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
