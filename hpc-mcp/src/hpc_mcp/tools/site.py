# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# Context, resolved once.
#
# Without this an agent rediscovers the same facts every session by probing the
# filesystem, which is the traffic the whole design is trying to avoid. Resolving the
# preamble once and caching it also means a wrong module name fails immediately with a
# clear message, instead of surfacing as a puzzling failure inside a batch job an hour
# later.

from __future__ import annotations

from ..session import Session


def register(server, session: Session) -> None:
    config = session.config
    cache: dict[str, object] = {}

    async def resolve_environment() -> dict:
        """Run the declared preamble once and report what it produced."""
        if "environment" in cache:
            return cache["environment"]  # type: ignore[return-value]

        script_lines = [
            "set -eo pipefail",
            f"cd {config.remote.project_root}",
        ]
        from ..preamble import preamble_lines

        script_lines.extend(preamble_lines(config.preamble))
        script_lines.append('printf "hostname=%s\\n" "$(hostname)"')
        # LOADEDMODULES, not LOADED_MODULES: that is the name Lmod actually exports,
        # and the plausible-looking one reports "none" however well the preamble ran.
        script_lines.append('printf "loaded_modules=%s\\n" "${LOADEDMODULES:-none}"')
        for name in config.env_probes:
            script_lines.append(
                f'printf "{name}=%s\\n" "${{{name}:-unset}}"'
            )
        outcome = await session.run_template(
            "\n".join(script_lines) + "\n", tool="site_info"
        )

        environment: dict = {"preamble_ok": outcome.ok}
        if outcome.ok:
            known = {"hostname", "loaded_modules", *config.env_probes}
            for line in outcome.stdout.splitlines():
                key, _, value = line.partition("=")
                if key in known:
                    environment[key] = value
        else:
            environment["error"] = (outcome.stderr or outcome.stdout).strip()
            environment["hint"] = (
                "The preamble in .hpc-assistant.json failed. Check the module names — "
                "everything dispatched to the cluster runs through it, so nothing else "
                "will work until this does."
            )
        cache["environment"] = environment
        return environment

    # The "separate filesystems" paragraph below assumes remote placement, where the agent
    # runs on the user's machine and reaches the cluster over SSH. That is the only
    # placement setup/ wires up today. Under --placement local the agent is already on a
    # cluster node and its own file tools CAN see these paths, so the paragraph would be
    # wrong: make it conditional on placement before wiring that mode up.
    @server.tool()
    async def site_info(refresh: bool = False) -> dict:
        """Describe the cluster, the project's bounds, and where this assistant is running.

        Read this before your first remote command. It answers the questions that would
        otherwise be answered by probing the filesystem.

        The paths it reports are on the cluster. Remote paths and the paths on this
        machine are separate filesystems and are never interchangeable.

        It also lists remote.readable_paths: directories outside the project that you may
        read, such as the site's container images. Take those paths from here rather than
        guessing one — anything not listed is refused.

        Args:
            refresh: Re-resolve the cached environment and mount table.
        """
        if refresh:
            cache.clear()
            session.policy.mounts.refresh()

        partitions = cache.get("partitions")
        if partitions is None:
            outcome = await session.execute(
                ["sinfo", "-h", "-o", "%P|%a|%l|%D"], tool="site_info"
            )
            rows = []
            for line in outcome.stdout.splitlines():
                fields = line.split("|")
                if len(fields) == 4:
                    rows.append(
                        {
                            "partition": fields[0].strip(),
                            "available": fields[1].strip(),
                            "time_limit": fields[2].strip(),
                            "nodes": fields[3].strip(),
                        }
                    )
            partitions = rows
            cache["partitions"] = partitions

        workspace_remote = session.policy.workspace_is_remote()
        info: dict = {
            "placement": "agent is local; cluster reached via " + session.transport.description,
            "site": config.site,
            "remote": {
                "host": config.remote.host,
                "project_root": config.remote.project_root,
                # Reported so that a physical path appearing in a job log or in
                # `scontrol show job` is recognisable as the project root rather than as
                # somewhere new.
                "project_root_also_known_as": list(session.policy.root_aliases) or None,
                "account": config.remote.account or "(unset)",
                # Named here because otherwise the only way to find shared software is to
                # guess a path, and a guess that is refused looks the same as one that
                # does not exist. These may be read, not written.
                "readable_paths": list(config.remote.readable_paths) or None,
            },
            "slurm_defaults": {
                "partition": config.slurm.partition,
                "time": config.slurm.time,
                "nodes": config.slurm.nodes,
                "gpus_per_node": config.slurm.gpus_per_node,
                "extra_directives": list(config.slurm.extra_directives) or None,
            },
            "login_commands": list(config.login_commands) or None,
            "partitions": partitions,
            "file_access": {
                "mode": config.file_access.mode,
                "local_root": config.file_access.local_root,
                "sshfs_mountpoint": config.file_access.sshfs_mountpoint or None,
            },
            "limits": {
                "max_read_bytes": config.limits.max_read_bytes,
                "max_output_bytes": config.limits.max_output_bytes,
                "max_list_entries": config.limits.max_list_entries,
                "max_find_depth": config.limits.max_find_depth,
                "command_timeout_seconds": config.limits.command_timeout_seconds,
            },
            "environment": await resolve_environment(),
            "search_guidance": (
                "Search file contents in the local working copy with your own tools; it "
                "costs the cluster nothing. Remote search is by filename only, via "
                "find_files, and is depth-limited."
            ),
        }

        if workspace_remote:
            info["warning"] = (
                f"The local workspace {config.file_access.local_root} is on a network "
                f"filesystem ({session.policy.mounts.describe(config.file_access.local_root)}). "
                f"It looks like local disk but is not: recursive search or bulk reads "
                f"inside it hit the cluster's metadata servers on every entry. Open "
                f"individual files by name and do not traverse it."
            )
        return info
