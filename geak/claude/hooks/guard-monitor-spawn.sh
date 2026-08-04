#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

set -euo pipefail

# PreToolUse hook for Agent: prevent duplicate geak-monitor subagents.
# Creates a .monitoring lock file on first spawn; blocks subsequent spawns
# for the same job. Exit code 2 blocks the tool call.

INPUT="$(cat)"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(echo "$INPUT" | jq -r '.cwd // "."')}"
AGENT_TYPE="$(echo "$INPUT" | jq -r '.tool_input.subagent_type // .tool_input.agentName // ""')"

if [[ "$AGENT_TYPE" != "geak-monitor" ]]; then
  exit 0
fi

JOBS_DIR="$PROJECT_DIR/geak-output/jobs"
if [[ ! -d "$JOBS_DIR" ]]; then
  exit 0
fi

# Clean up stale locks for completed/failed jobs
for lock in "$JOBS_DIR"/*/.monitoring; do
  [[ -f "$lock" ]] || continue
  job_dir="$(dirname "$lock")"
  if [[ -f "$job_dir/DONE" || -f "$job_dir/FAILED" ]]; then
    rm -f "$lock"
  fi
done

# Extract job ID from the prompt
PROMPT="$(echo "$INPUT" | jq -r '.tool_input.prompt // ""')"
JOB_ID="$(echo "$PROMPT" | grep -oE '[0-9]{8}-[0-9]{6}-[a-f0-9]{4}' | head -1)" || true

# Block only if THIS specific job already has an active monitor
if [[ -n "$JOB_ID" && -f "$JOBS_DIR/$JOB_ID/.monitoring" ]]; then
  echo "A geak-monitor is already running for job $JOB_ID. Use TaskList/TaskStop to manage it." >&2
  exit 2
fi

# Create lock for the new job
if [[ -n "$JOB_ID" && -d "$JOBS_DIR/$JOB_ID" ]]; then
  echo "$(date -Is 2>/dev/null || date)" > "$JOBS_DIR/$JOB_ID/.monitoring"
fi

exit 0
