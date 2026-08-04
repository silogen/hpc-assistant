#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

set -euo pipefail

# PreToolUse hook for Bash: block raw cluster commands, allow only approved wrappers.

INPUT="$(cat)"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(echo "$INPUT" | jq -r '.cwd // "."')}"
COMMAND="$(echo "$INPUT" | jq -r '.tool_input.command // ""')"
LOG_DIR="$PROJECT_DIR/.claude/logs"
LOG_FILE="$LOG_DIR/geak-hook-denials.log"

mkdir -p "$LOG_DIR"

deny() {
  local reason="$1"
  printf '%s | Bash | %s | %s\n' "$(date -Is)" "$reason" "$COMMAND" >> "$LOG_FILE"
  jq -n --arg reason "$reason" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
  exit 0
}

# Block raw cluster/container commands unless they appear inside an approved wrapper call.
if echo "$COMMAND" | grep -Eq '(^|[[:space:]]|;|&&|\|)(sbatch|srun|squeue|sacct|scancel|singularity)([[:space:]]|$|;)'; then
  # Allow if it's an approved wrapper script. Block direct invocations from Claude.
  if ! echo "$COMMAND" | grep -Eq '\.claude/skills/[^/]+/scripts/(submit-geak-job|check-geak-job|cancel-geak-job)\.sh'; then
    deny "Raw cluster commands (sbatch/srun/squeue/sacct/scancel/singularity) are blocked. Use the approved wrapper scripts."
  fi
fi

# Block direct execution of run-geak.sbatch
if echo "$COMMAND" | grep -Eq '(^|[[:space:]]|/)run-geak\.sbatch([[:space:]]|$)'; then
  if ! echo "$COMMAND" | grep -Eq '\.claude/skills/[^/]+/scripts/(submit-geak-job|check-geak-job|cancel-geak-job)\.sh'; then
    deny "Direct execution of run-geak.sbatch is blocked. Use the approved wrapper scripts."
  fi
fi

exit 0
