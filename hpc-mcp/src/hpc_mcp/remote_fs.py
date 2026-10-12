# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# Bounded remote filesystem operations.
#
# These are script templates rather than argv dispatches, because they need pipelines and
# because every bound belongs on this side of the boundary. The agent asks for "the file",
# and what actually runs is a read of a specific byte range with a hard cap.
#
# The scripts use `set -e` without `pipefail` on purpose. Capping output means closing a
# pipe early, which sends SIGPIPE to the producer; with pipefail that reads as a failed
# command when in fact it is the cap working as designed.

from __future__ import annotations

import shlex
import uuid
from dataclasses import dataclass

from .errors import PolicyViolation
from .session import Session

SIZE_MARKER = "hpc-mcp-size"
_HEADER = "set -e"


def _raise_if_only_errors(outcome, path: str) -> None:
    """Distinguish "nothing there" from "the command did not work".

    The traversal scripts end in a pipeline, so the exit status belongs to `head` and is
    almost always zero — a failing `find` would otherwise look exactly like an empty
    directory. Judging by output instead means a permission problem or an unsupported
    option surfaces as an error rather than as a confidently wrong empty listing.
    """
    if outcome.stdout.strip():
        return
    if outcome.stderr.strip():
        raise PolicyViolation(
            f"Could not traverse {path}: {outcome.stderr.strip()}"
        )
    if outcome.timed_out:
        raise PolicyViolation(
            f"Traversing {path} exceeded the command timeout.",
            "That is usually a sign the directory is far larger than expected; try a "
            "narrower path.",
        )


@dataclass
class FileSlice:
    path: str
    total_bytes: int
    returned_bytes: int
    offset: int
    content: str

    @property
    def complete(self) -> bool:
        return self.offset + self.returned_bytes >= self.total_bytes


async def read_file(
    session: Session, path: str, *, offset: int = 0, max_bytes: int | None = None
) -> FileSlice:
    """Read a byte range of a remote file, capped by limits.max_read_bytes."""
    if offset < 0:
        raise PolicyViolation("offset must not be negative.")
    resolved = session.policy.normalize_remote_path(path)
    cap = session.config.limits.max_read_bytes
    limit = min(max_bytes or cap, cap)
    quoted = shlex.quote(resolved)

    script = f"""{_HEADER}
target={quoted}
if [ -d "$target" ]; then
  echo "hpc-mcp: $target is a directory" >&2
  exit 65
fi
if [ ! -r "$target" ]; then
  echo "hpc-mcp: cannot read $target" >&2
  exit 66
fi
printf '{SIZE_MARKER} %s\\n' "$(wc -c < "$target")"
dd if="$target" iflag=skip_bytes,count_bytes skip={int(offset)} count={int(limit)} status=none
"""
    outcome = await session.run_template(
        script, tool="read_file", detail={"path": resolved, "offset": offset, "limit": limit}
    )
    if not outcome.ok:
        raise PolicyViolation(
            (outcome.stderr or outcome.stdout).strip()
            or f"Could not read {resolved} (exit {outcome.exit_code})."
        )

    first_line, _, body = outcome.stdout.partition("\n")
    total = 0
    if first_line.startswith(SIZE_MARKER):
        try:
            total = int(first_line.split()[1])
        except (IndexError, ValueError):
            total = 0
    else:
        body = outcome.stdout

    return FileSlice(
        path=resolved,
        total_bytes=total,
        returned_bytes=len(body.encode()),
        offset=offset,
        content=body,
    )


async def tail_file(session: Session, path: str, *, max_bytes: int | None = None) -> str:
    """Read the end of a remote file, which is where a job log says how it went."""
    resolved = session.policy.normalize_remote_path(path)
    cap = session.config.limits.max_read_bytes
    limit = min(max_bytes or cap, cap)

    script = f"""{_HEADER}
target={shlex.quote(resolved)}
if [ ! -r "$target" ]; then
  echo "hpc-mcp: cannot read $target" >&2
  exit 66
fi
total=$(wc -c < "$target")
if [ "$total" -gt {int(limit)} ]; then
  printf '[showing the last {int(limit)} bytes of %s]\\n' "$total"
fi
tail -c {int(limit)} -- "$target"
"""
    outcome = await session.run_template(
        script, tool="tail_file", detail={"path": resolved, "limit": limit}
    )
    if not outcome.ok:
        raise PolicyViolation(
            (outcome.stderr or outcome.stdout).strip() or f"Could not read {resolved}."
        )
    return outcome.stdout


async def write_file(session: Session, path: str, content: str) -> str:
    """Write a file on the cluster, creating parent directories inside the project root."""
    resolved = session.policy.normalize_remote_path(path, write=True)
    cap = session.config.limits.max_read_bytes
    encoded = len(content.encode())
    if encoded > cap:
        raise PolicyViolation(
            f"Refusing to write {encoded} bytes; the cap is {cap}.",
            "Large inputs belong in the repository and should reach the cluster through "
            "your file-access mode, not through a tool argument.",
        )

    # A random delimiter, checked against the payload, so content containing the literal
    # word EOF cannot terminate its own heredoc.
    delimiter = f"HPC_MCP_{uuid.uuid4().hex.upper()}"
    if delimiter in content:
        raise PolicyViolation("Could not find a safe heredoc delimiter; try again.")
    if not content.endswith("\n"):
        content += "\n"

    quoted = shlex.quote(resolved)
    script = f"""{_HEADER}
target={quoted}
mkdir -p "$(dirname "$target")"
cat > "$target" <<'{delimiter}'
{content}{delimiter}
printf 'hpc-mcp: wrote %s bytes to %s\\n' "$(wc -c < "$target")" "$target"
"""
    outcome = await session.run_template(
        script,
        tool="write_file",
        detail={"path": resolved, "bytes": encoded},
        check=True,
    )
    return outcome.stdout.strip()


async def list_dir(session: Session, path: str, *, depth: int = 1) -> str:
    """List a directory to a bounded depth with a bounded number of entries."""
    resolved = session.policy.normalize_remote_path(path)
    limits = session.config.limits
    if depth < 1:
        raise PolicyViolation("depth must be at least 1.")
    if depth > limits.max_find_depth:
        raise PolicyViolation(
            f"depth {depth} exceeds the configured limit of {limits.max_find_depth}.",
            "List a narrower subtree, or raise limits.max_find_depth in "
            ".hpc-assistant.json if you accept the metadata cost.",
        )

    quoted = shlex.quote(resolved)
    script = f"""{_HEADER}
target={quoted}
if [ ! -d "$target" ]; then
  echo "hpc-mcp: not a directory: $target" >&2
  exit 65
fi
find "$target" -mindepth 1 -maxdepth {int(depth)} \\
  -printf '%y %10s  %P\\n' | sort -k3 | head -n {int(limits.max_list_entries)}
"""
    outcome = await session.run_template(
        script,
        tool="list_dir",
        detail={"path": resolved, "depth": depth},
    )
    _raise_if_only_errors(outcome, resolved)

    lines = outcome.stdout.splitlines()
    header = f"{resolved} (depth {depth}, type/size/name):"
    if len(lines) >= limits.max_list_entries:
        header += (
            f"\n[capped at {limits.max_list_entries} entries — narrow the path or the "
            f"depth to see the rest]"
        )
    if outcome.stderr.strip():
        header += f"\n[partial: {outcome.stderr.strip()}]"
    return header + "\n" + outcome.stdout


async def find_files(
    session: Session, name: str, *, path: str | None = None, depth: int | None = None
) -> str:
    """Find files by name, never by content.

    Content search stays on the user's own machine. Running it here is the single most
    damaging thing this bridge could offer: recursive grep over a parallel filesystem
    degrades it for everyone on the machine.
    """
    resolved = session.policy.normalize_remote_path(path or ".")
    limits = session.config.limits
    effective_depth = min(depth or limits.max_find_depth, limits.max_find_depth)
    if not name or "/" in name:
        raise PolicyViolation(
            "name must be a filename pattern without slashes, such as '*.py'.",
            "Pass the directory to search in `path` instead.",
        )

    script = f"""{_HEADER}
find {shlex.quote(resolved)} -mindepth 1 -maxdepth {int(effective_depth)} \\
  -name {shlex.quote(name)} -print | head -n {int(limits.max_list_entries)}
"""
    outcome = await session.run_template(
        script,
        tool="find_files",
        detail={"path": resolved, "name": name, "depth": effective_depth},
    )
    _raise_if_only_errors(outcome, resolved)

    matches = outcome.stdout.strip()
    if not matches:
        return (
            f"No files matching {name!r} under {resolved} within depth "
            f"{effective_depth}."
        )
    if outcome.stderr.strip():
        matches += f"\n[partial: {outcome.stderr.strip()}]"
    return matches
