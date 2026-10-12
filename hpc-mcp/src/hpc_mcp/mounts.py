# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# Deciding whether a local path is really local disk.
#
# This exists because the biggest win of running the agent on the user's own machine is
# that content search stops generating metadata traffic on the cluster — and an sshfs
# mount silently gives that win back. A mounted directory is indistinguishable from
# local disk to an ordinary file-search tool, so every stat and readdir inside it
# becomes a network round trip and a metadata operation on the parallel filesystem.
#
# Users may also have their own mounts that predate any configuration we write, so this
# check reads the live mount table rather than trusting the project config.

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# Filesystem types whose operations cross a network. FUSE is included wholesale because
# almost every FUSE filesystem is either remote or slow enough to behave like one, and
# guessing wrong in the permissive direction is the expensive mistake here.
REMOTE_FSTYPES = frozenset(
    {
        "afpfs",
        "afs",
        "beegfs",
        "ceph",
        "cifs",
        "davfs",
        "fuse",
        "fuseblk",
        "fusefs",
        "fuse.rclone",
        "fuse.sshfs",
        "gpfs",
        "lustre",
        "macfuse",
        "nfs",
        "nfs4",
        "osxfuse",
        "panfs",
        "smbfs",
        "sshfs",
        "webdav",
    }
)

_MACOS_MOUNT_RE = re.compile(r"^.*? on (?P<path>.+?) \((?P<opts>[^)]*)\)\s*$")


def _fstype_is_remote(fstype: str) -> bool:
    fstype = fstype.strip().lower()
    if fstype in REMOTE_FSTYPES:
        return True
    # Linux reports FUSE filesystems as "fuse.<name>"; treat the whole family as remote.
    return fstype.startswith("fuse")


def _read_linux_mounts() -> list[tuple[str, str]]:
    mounts: list[tuple[str, str]] = []
    try:
        content = Path("/proc/self/mounts").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return mounts
    for line in content.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        # Mount points are escaped octal-style in /proc; \040 is the common case.
        mountpoint = fields[1].replace("\\040", " ").replace("\\011", "\t")
        mounts.append((mountpoint, fields[2]))
    return mounts


def _read_macos_mounts() -> list[tuple[str, str]]:
    mounts: list[tuple[str, str]] = []
    try:
        proc = subprocess.run(
            ["/sbin/mount"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return mounts
    for line in proc.stdout.splitlines():
        match = _MACOS_MOUNT_RE.match(line)
        if not match:
            continue
        # `mount` renders options as "macfuse, nodev, nosuid" — the first is the type.
        opts = match.group("opts").split(",")
        fstype = opts[0] if opts else ""
        mounts.append((match.group("path"), fstype))
    return mounts


def read_mount_table() -> list[tuple[str, str]]:
    """Return (mountpoint, fstype) pairs, longest mountpoint first.

    Ordering matters: /home/me/mnt/lumi must win over / when both match.
    """
    mounts = _read_macos_mounts() if sys.platform == "darwin" else _read_linux_mounts()
    return sorted(mounts, key=lambda m: len(m[0]), reverse=True)


class MountTable:
    """Cached view of the mount table.

    Cached because it is consulted on every path check and mounts rarely change inside a
    session. `refresh()` exists for the case that matters: a mount appearing or going
    stale mid-session.
    """

    def __init__(self, entries: list[tuple[str, str]] | None = None) -> None:
        self._entries = entries if entries is not None else read_mount_table()

    def refresh(self) -> None:
        self._entries = read_mount_table()

    def _candidate_forms(self, path: str | Path) -> list[Path]:
        """The forms of a path that might match a mount table entry.

        Both the lexical form and the symlink-resolved form are checked, because either
        can be the one that matches. Resolving is needed when a mountpoint is reached
        through a symlink; not resolving is needed because resolution can move a path out
        from under its own entry — macOS rewrites /home to /System/Volumes/Data/home, and
        the mount table still says /home.
        """
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = Path.cwd() / target
        lexical = Path(os.path.normpath(str(target)))
        forms = [lexical]
        try:
            resolved = target.resolve()
        except OSError:
            resolved = None
        if resolved is not None and resolved != lexical:
            forms.append(resolved)
        return forms

    def _most_specific_match(self, form: Path) -> tuple[str, str] | None:
        # Entries are longest-first, so the first hit is the most specific one.
        for mountpoint, fstype in self._entries:
            mount = Path(mountpoint)
            if form == mount or mount in form.parents:
                return mountpoint, fstype
        return None

    def _matches(self, path: str | Path) -> list[tuple[str, str]]:
        found = (self._most_specific_match(form) for form in self._candidate_forms(path))
        return [match for match in found if match]

    def containing_mount(self, path: str | Path) -> tuple[str, str] | None:
        """Return the (mountpoint, fstype) the path sits on, if it can be determined."""
        matches = self._matches(path)
        if not matches:
            return None
        return max(matches, key=lambda match: len(match[0]))

    def is_remote(self, path: str | Path) -> bool:
        """True if any form of the path lands on a network or FUSE filesystem.

        Deliberately biased: if one form of the path says remote, treat it as remote. A
        false "local" costs the cluster a metadata storm, while a false "remote" only
        costs a refused traversal.
        """
        return any(_fstype_is_remote(fstype) for _, fstype in self._matches(path))

    def describe(self, path: str | Path) -> str:
        remote = [match for match in self._matches(path) if _fstype_is_remote(match[1])]
        found = remote[0] if remote else self.containing_mount(path)
        if not found:
            return "unknown filesystem"
        return f"{found[1]} mounted at {found[0]}"
