#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# Cancel a running GEAK optimization job.
#
# Usage: ./.claude/skills/cancel-geak-job/scripts/cancel-geak-job.sh <job-id>
#
# Looks up the SLURM job ID from the job directory and runs scancel.
# Outputs JSON to stdout. Errors go to stderr as JSON.

set -euo pipefail

err_json() {
  jq -n --arg msg "$1" '{ok: false, error: $msg}' >&2
  exit 1
}

find_project_root() {
  local dir="$(cd "$(dirname "$0")" && pwd)"
  while [[ "$dir" != "/" ]]; do
    [[ -d "$dir/.claude" || -d "$dir/.opencode" ]] && { echo "$dir"; return; }
    dir="$(dirname "$dir")"
  done
}

JOB_ID="${1:-}"
if [[ -z "$JOB_ID" ]]; then
  err_json "Usage: cancel-geak-job.sh <job-id>"
fi

PROJECT_ROOT="$(find_project_root)"
if [[ -z "$PROJECT_ROOT" ]]; then
  err_json "Could not find project root (no .claude/ or .opencode/ directory in ancestors)"
fi
JOB_DIR="${PROJECT_ROOT}/geak-output/jobs/${JOB_ID}"

if [[ ! -d "${JOB_DIR}" ]]; then
  err_json "Job directory not found: ${JOB_DIR}"
fi

if [[ -f "${JOB_DIR}/DONE" ]]; then
  err_json "Job ${JOB_ID} has already completed"
fi

if [[ ! -f "${JOB_DIR}/slurm_job_id" ]]; then
  err_json "No SLURM job ID found for ${JOB_ID}"
fi

SLURM_ID=$(cat "${JOB_DIR}/slurm_job_id" 2>/dev/null | tr -d '[:space:]')
if [[ -z "$SLURM_ID" ]]; then
  err_json "Empty SLURM job ID for ${JOB_ID}"
fi

if scancel "$SLURM_ID" 2>/dev/null; then
  echo "CANCELLED by user" > "${JOB_DIR}/FAILED"
  jq -n \
    --arg id "$JOB_ID" \
    --arg slurm_id "$SLURM_ID" \
    '{ok: true, id: $id, slurm_job_id: $slurm_id, state: "CANCELLED"}'
else
  CURRENT_STATE=$(squeue -j "$SLURM_ID" -h -o "%T" 2>/dev/null || echo "UNKNOWN")
  if [[ -z "$CURRENT_STATE" || "$CURRENT_STATE" == "UNKNOWN" ]]; then
    jq -n \
      --arg id "$JOB_ID" \
      --arg slurm_id "$SLURM_ID" \
      '{ok: false, id: $id, slurm_job_id: $slurm_id, error: "Job is no longer in the SLURM queue (may have already finished)"}'
  else
    jq -n \
      --arg id "$JOB_ID" \
      --arg slurm_id "$SLURM_ID" \
      --arg state "$CURRENT_STATE" \
      '{ok: false, id: $id, slurm_job_id: $slurm_id, error: "scancel failed", slurm_state: $state}'
  fi
fi
