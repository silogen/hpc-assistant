# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# Building the script that carries one command to the cluster.
#
# A non-interactive SSH session applies no shell profile, so `module` is not a function,
# PATH is minimal, and nothing survives from the previous command — no working directory,
# no environment, no loaded modules. The usual fix is a persistent remote shell, which is
# exactly the long-lived login-node process administrators object to.
#
# So state is declared instead of remembered. Each dispatch is a self-contained script
# built from the project's preamble, and the same declaration renders into batch job
# scripts, which keeps the login-node and in-job environments from drifting apart.

from __future__ import annotations

import posixpath
import shlex

from .config import Preamble

# `set -e` and `pipefail` but deliberately not `-u`. Lmod's init scripts and most module
# files reference unset variables freely; with -u the preamble dies before the command
# runs. This cost us nothing to discover in a batch job and would have been mystifying to
# debug from a tool call.
SCRIPT_HEADER = "set -eo pipefail"

# `module` is a shell function, so a non-interactive shell has to source the module
# system first. The candidate list covers stock Lmod, an Lmod that exports LMOD_PKG,
# the Cray PE layout, Tcl Environment Modules ($MODULESHOME and the distro path), and
# a profile.d drop-in.
MODULE_BOOTSTRAP = """\
if ! command -v module >/dev/null 2>&1; then
  for _mod_init in /usr/share/lmod/lmod/init/bash "${LMOD_PKG}/init/bash" \\
                    /opt/cray/pe/lmod/lmod/init/bash \\
                    "${MODULESHOME}/init/bash" /usr/share/Modules/init/bash \\
                    /etc/profile.d/lmod.sh /etc/profile.d/modules.sh; do
    if [ -n "$_mod_init" ] && [ -r "$_mod_init" ]; then . "$_mod_init"; break; fi
  done
  unset _mod_init
fi"""
# Kept as an alias so existing tests and comments that say Lmod still find the bootstrap.
LMOD_BOOTSTRAP = MODULE_BOOTSTRAP

# Lmod provides these as shell functions, so `exec` cannot run them: exec replaces the
# shell with a program, and a function is not one. They are the environment-inspection
# commands an agent reaches for first — run_command's own documentation offers
# ["module", "list"] as its example — so they are called rather than exec'd. They return
# immediately, which is why they can afford to give up what `exec` buys everywhere else.
SHELL_FUNCTION_BINARIES = frozenset({"ml", "module"})


def preamble_lines(preamble: Preamble) -> list[str]:
    """Render the preamble as script lines, in the order the config declares."""
    lines: list[str] = []
    if preamble.modules:
        lines.append(MODULE_BOOTSTRAP)
        lines.append("module load " + " ".join(shlex.quote(m) for m in preamble.modules))
    for name, value in preamble.env:
        lines.append(f"export {name}={shlex.quote(value)}")
    lines.extend(preamble.commands)
    return lines


def render_inline(preamble: Preamble) -> str:
    """Render the preamble as a single `&&`-chained string.

    For the places that genuinely need a one-liner, such as `sbatch --wrap`. Everywhere
    else prefer the script form, which is readable in an audit log.
    """
    parts = [line for line in preamble_lines(preamble) if "\n" not in line]
    return " && ".join(parts)


def build_script(
    argv: list[str],
    *,
    cwd: str,
    preamble: Preamble,
    include_preamble: bool = True,
) -> str:
    """Build the script for one dispatch: header, cd, preamble, then the command.

    `exec` on the last line makes the script's exit status the command's own, so nothing
    has to be inferred about which step failed. It also means the remote process is the
    one ssh is holding, so killing a timed-out dispatch cannot leave an orphan behind on
    the login node — which is why the exception below is limited to commands that return
    immediately.
    """
    lines = ["#!/usr/bin/env bash", SCRIPT_HEADER, f"cd {shlex.quote(cwd)}"]
    if include_preamble:
        lines.extend(preamble_lines(preamble))

    is_shell_function = posixpath.basename(argv[0]) in SHELL_FUNCTION_BINARIES
    # `module list` on a project that declares no modules still needs Lmod sourced, and
    # preamble_lines only emits the bootstrap when there is something to load.
    if is_shell_function and not (include_preamble and preamble.modules):
        lines.append(MODULE_BOOTSTRAP)

    command = " ".join(shlex.quote(a) for a in argv)
    lines.append(command if is_shell_function else f"exec {command}")
    return "\n".join(lines) + "\n"


def build_batch_script(
    argv: list[str],
    *,
    cwd: str,
    preamble: Preamble,
    job_name: str,
    account: str,
    partition: str,
    time_limit: str,
    nodes: int = 1,
    gpus_per_node: int = 0,
    output_pattern: str = "slurm-%j.out",
    extra_directives: tuple[str, ...] | list[str] = (),
) -> str:
    """Build a batch script for the same command, with the same preamble.

    One declaration rendered into both contexts is the whole point: a command that works
    from a tool call should not fail in a job because a module was missing.
    """
    directives = [
        f"#SBATCH --job-name={job_name}",
        f"#SBATCH --partition={partition}",
        f"#SBATCH --time={time_limit}",
        f"#SBATCH --nodes={nodes}",
        f"#SBATCH --output={output_pattern}",
    ]
    if account:
        directives.insert(1, f"#SBATCH --account={account}")
    if gpus_per_node > 0:
        directives.append(f"#SBATCH --gpus-per-node={gpus_per_node}")
    for extra in extra_directives:
        directives.append(f"#SBATCH {extra}")

    lines = ["#!/usr/bin/env bash", *directives, "", SCRIPT_HEADER, f"cd {shlex.quote(cwd)}"]
    lines.extend(preamble_lines(preamble))
    lines.append("")
    lines.append(" ".join(shlex.quote(a) for a in argv))
    return "\n".join(lines) + "\n"
