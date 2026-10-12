# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

from .. import remote_fs
from ..session import Session


def register(server, session: Session) -> None:
    @server.tool()
    async def read_file(path: str, offset: int = 0, max_bytes: int | None = None) -> str:
        """Read a file on the cluster. Reads are capped; use offset to continue.

        Args:
            path: Path inside the project root.
            offset: Byte offset to start from, for reading a large file in pieces.
            max_bytes: Cap for this read. Silently lowered to the configured limit.
        """
        result = await remote_fs.read_file(
            session, path, offset=offset, max_bytes=max_bytes
        )
        if result.complete and result.offset == 0:
            return result.content
        remaining = max(0, result.total_bytes - (result.offset + result.returned_bytes))
        note = (
            f"[bytes {result.offset}–{result.offset + result.returned_bytes} of "
            f"{result.total_bytes}; {remaining} remaining — call again with "
            f"offset={result.offset + result.returned_bytes}]"
        )
        return f"{note}\n{result.content}"

    @server.tool()
    async def tail_file(path: str, max_bytes: int | None = None) -> str:
        """Read the end of a file on the cluster. Use this for logs.

        A job log's useful part is the end, and reading it from the start wastes the read
        cap on the parts that were already fine.

        Args:
            path: Path inside the project root.
            max_bytes: How much of the end to read. Lowered to the configured limit.
        """
        return await remote_fs.tail_file(session, path, max_bytes=max_bytes)

    @server.tool()
    async def write_file(path: str, content: str) -> str:
        """Write a small file on the cluster, such as a batch script.

        For source code, prefer editing locally and moving it with your file-access mode;
        this is for the short generated files a job needs.

        Args:
            path: Path inside the project root. Parent directories are created.
            content: File contents.
        """
        return await remote_fs.write_file(session, path, content)

    @server.tool()
    async def list_dir(path: str = ".", depth: int = 1) -> str:
        """List a directory on the cluster, depth-limited and entry-capped.

        Args:
            path: Directory inside the project root.
            depth: How many levels to descend. Bounded by limits.max_find_depth.
        """
        return await remote_fs.list_dir(session, path, depth=depth)

    @server.tool()
    async def find_files(
        name: str, path: str = ".", depth: int | None = None
    ) -> str:
        """Find files on the cluster by name pattern. Never searches file contents.

        To search contents, search your local working copy instead — that costs the
        cluster nothing, which is the reason the working copy is there.

        Args:
            name: Filename pattern, such as '*.py'. No slashes.
            path: Directory to search under, inside the project root.
            depth: Maximum depth. Bounded by limits.max_find_depth.
        """
        return await remote_fs.find_files(session, name, path=path, depth=depth)
