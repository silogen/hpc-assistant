#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

set -euo pipefail

# SubagentStop hook for geak-monitor: remove .monitoring lock files
# for jobs that have completed or failed.

INPUT="$(cat)"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(echo "$INPUT" | jq -r '.cwd // "."')}"
JOBS_DIR="$PROJECT_DIR/geak-output/jobs"

[[ -d "$JOBS_DIR" ]] || exit 0

for lock in "$JOBS_DIR"/*/.monitoring; do
  [[ -f "$lock" ]] || continue
  job_dir="$(dirname "$lock")"
  if [[ -f "$job_dir/DONE" || -f "$job_dir/FAILED" ]]; then
    rm -f "$lock"
  fi
done

exit 0
