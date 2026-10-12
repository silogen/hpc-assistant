# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from hpc_mcp.mounts import MountTable, read_mount_table

ENTRIES = [
    ("/home/me/mnt/lumi/scratch", "fuse.sshfs"),
    ("/home/me/mnt/lumi", "fuse.sshfs"),
    ("/home/me/data", "nfs4"),
    ("/home/me", "ext4"),
    ("/", "ext4"),
]


@pytest.fixture
def table():
    return MountTable(entries=sorted(ENTRIES, key=lambda e: len(e[0]), reverse=True))


class TestClassification:
    @pytest.mark.parametrize(
        "path,expected",
        [
            ("/home/me/mnt/lumi/scratch/project/src", True),
            ("/home/me/mnt/lumi", True),
            ("/home/me/data/results.csv", True),
            ("/home/me/code/project", False),
            ("/tmp/scratch", False),
        ],
    )
    def test_network_filesystems_are_recognised(self, table, path, expected):
        assert table.is_remote(path) is expected

    def test_the_longest_matching_mount_wins(self, table):
        # /home/me is ext4 and /home/me/mnt/lumi is sshfs; a shortest-match or
        # first-match implementation would call the sshfs path local.
        assert table.containing_mount("/home/me/mnt/lumi/x")[0] == "/home/me/mnt/lumi"

    def test_a_mountpoint_is_not_confused_with_a_sibling_of_the_same_prefix(self, table):
        assert table.containing_mount("/home/me/mnt/lumi-backup")[0] == "/home/me"

    @pytest.mark.parametrize(
        "fstype", ["fuse.sshfs", "fuse", "macfuse", "osxfuse", "nfs", "cifs", "lustre"]
    )
    def test_the_fuse_family_and_network_types_all_count_as_remote(self, fstype):
        assert MountTable(entries=[("/mnt/x", fstype)]).is_remote("/mnt/x/file")

    def test_an_unknown_fstype_is_treated_as_local(self):
        assert not MountTable(entries=[("/mnt/x", "btrfs")]).is_remote("/mnt/x/file")

    def test_an_empty_table_describes_paths_as_unknown(self):
        assert MountTable(entries=[]).describe("/anywhere") == "unknown filesystem"

    def test_the_description_names_the_filesystem_and_mountpoint(self, table):
        described = table.describe("/home/me/mnt/lumi/src")
        assert "fuse.sshfs" in described and "/home/me/mnt/lumi" in described


class TestRealMountTable:
    def test_reading_the_live_table_returns_something_containing_the_root(self):
        # A smoke test, because the parsing differs per platform and a silent failure here
        # would make every sshfs workspace look like local disk.
        entries = read_mount_table()
        assert entries, "no mounts could be read on this platform"
        assert any(mountpoint == "/" for mountpoint, _ in entries)

    def test_entries_are_ordered_longest_first(self):
        lengths = [len(mountpoint) for mountpoint, _ in read_mount_table()]
        assert lengths == sorted(lengths, reverse=True)
