#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# Push this working tree to the remote host.
#
# The bridge does not move files: hpc-mcp reads and writes single bounded files and
# nothing else. file_access.rsync.excludes in .hpc-assistant.json is not applied by any
# code, so the exclude list below is the one that actually takes effect. Keep the two in
# step, or delete one of them.
#
#   ./sync-to-remote.sh          # dry run, shows what would move
#   ./sync-to-remote.sh --go     # actually transfer
#
# --delete is never passed. The remote copy may hold job output that exists nowhere
# else, and mirroring over it would destroy work rather than synchronise it.
set -euo pipefail

REMOTE_HOST="{{REMOTE_HOST}}"
REMOTE_ROOT="{{REMOTE_ROOT}}"
LOCAL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# .hpc-assistant/ holds the batch scripts and job logs run_on_compute writes on the
# remote side; it must never be overwritten from here.
#
# The agent scaffolding (skills, system prompt, MCP registration, bridge config) is
# local by definition in local mode. Shipping it to the remote host would put assistant
# content on shared storage for no benefit, and its paths refer to the local machine.
EXCLUDES=(
  ".git/" ".hpc-assistant/" ".venv/" "__pycache__/" "*.py[cod]"
  "build/" "*.o" "*.so" "*.sif" "slurm-*.out" "slurm-*.err"
  ".opencode/" ".claude/" "AGENTS.md" "CLAUDE.md"
  "opencode.json" ".mcp.json" ".hpc-assistant.json" "sync-to-remote.sh"
)

args=(-a --human-readable --itemize-changes)
for e in "${EXCLUDES[@]}"; do args+=(--exclude="$e"); done

if [[ "${1:-}" == "--go" ]]; then
  echo "transferring $LOCAL_ROOT/ -> $REMOTE_HOST:$REMOTE_ROOT/"
else
  args+=(--dry-run)
  echo "DRY RUN - nothing will be transferred. Re-run with --go to apply."
fi

rsync "${args[@]}" "$LOCAL_ROOT/" "$REMOTE_HOST:$REMOTE_ROOT/"

if [[ "${1:-}" != "--go" ]]; then
  echo
  echo "Lines above are what would change. Re-run with --go to transfer."
fi
