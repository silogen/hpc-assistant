#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

set -euo pipefail

# PreToolUse hook: block Edit/Write to protected workflow files.
# Uses structured JSON deny (exit 0 + permissionDecision: "deny").

INPUT="$(cat)"
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(echo "$INPUT" | jq -r '.cwd // "."')}"
FILE="$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // ""')"
LOG_DIR="$PROJECT_DIR/.claude/logs"
LOG_FILE="$LOG_DIR/geak-hook-denials.log"

mkdir -p "$LOG_DIR"

deny() {
  local reason="$1"
  printf '%s | Edit/Write | %s | %s\n' "$(date -Is)" "$reason" "$FILE" >> "$LOG_FILE"
  jq -n --arg reason "$reason" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
  exit 0
}

PROTECTED_PATTERNS="run-geak.sbatch setup-geak.sh .claude/hooks/ .claude/settings.json .claude/skills/"
for pattern in $PROTECTED_PATTERNS; do
  if [[ "$FILE" == *"$pattern"* ]]; then
    deny "'$FILE' is a protected workflow file and must not be modified."
  fi
done

exit 0
