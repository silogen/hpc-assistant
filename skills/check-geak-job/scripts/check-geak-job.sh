#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# Check status of a GEAK optimization job.
#
# Usage: ./.claude/skills/check-geak-job/scripts/check-geak-job.sh <job-id>
#
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
  err_json "Usage: check-geak-job.sh <job-id>"
fi

PROJECT_ROOT="$(find_project_root)"
if [[ -z "$PROJECT_ROOT" ]]; then
  err_json "Could not find project root (no .claude/ or .opencode/ directory in ancestors)"
fi
JOB_DIR="${PROJECT_ROOT}/geak-output/jobs/${JOB_ID}"
RUN_DIR="${PROJECT_ROOT}/geak-output/runs/${JOB_ID}"

if [[ ! -d "${JOB_DIR}" ]]; then
  err_json "Job directory not found: ${JOB_DIR}"
fi

if [[ -f "${JOB_DIR}/DONE" ]]; then
  REPORT=""
  KERNEL=""
  RESULT_FILES=""

  if [[ -f "${RUN_DIR}/final_report.json" ]]; then
    REPORT=$(cat "${RUN_DIR}/final_report.json" 2>/dev/null || echo "")
  fi

  for f in "${RUN_DIR}/"*.py; do
    if [[ -f "$f" ]]; then
      KERNEL=$(cat "$f" 2>/dev/null || echo "")
      break
    fi
  done

  if [[ -d "${RUN_DIR}" ]]; then
    RESULT_FILES=$(ls "${RUN_DIR}/" 2>/dev/null | tr '\n' ',' | sed 's/,$//')
  fi

  jq -n \
    --arg id "$JOB_ID" \
    --arg report "$REPORT" \
    --arg kernel "$KERNEL" \
    --arg files "$RESULT_FILES" \
    '{ok: true, id: $id, state: "COMPLETED", result_files: (if $files == "" then [] else ($files | split(",")) end), report: (if $report != "" then ($report | try fromjson catch $report) else null end), optimized_kernel: (if $kernel != "" then $kernel else null end)}'

elif [[ -f "${JOB_DIR}/FAILED" ]]; then
  FAIL_MSG=$(cat "${JOB_DIR}/FAILED" 2>/dev/null || echo "unknown error")
  STDERR_TAIL=""
  for f in "${JOB_DIR}"/slurm-*.err; do
    if [[ -f "$f" ]]; then
      STDERR_TAIL=$(tail -30 "$f" 2>/dev/null || echo "")
      break
    fi
  done

  jq -n \
    --arg id "$JOB_ID" \
    --arg fail_msg "$FAIL_MSG" \
    --arg stderr "$STDERR_TAIL" \
    '{ok: true, id: $id, state: "FAILED", error: $fail_msg, slurm_stderr_tail: $stderr}'

else
  SLURM_ID=""
  SLURM_STATUS=""
  if [[ -f "${JOB_DIR}/slurm_job_id" ]]; then
    SLURM_ID=$(cat "${JOB_DIR}/slurm_job_id" 2>/dev/null || echo "")
    SLURM_STATUS=$(squeue -j "${SLURM_ID}" -h -o "%T %M %R" 2>/dev/null || \
      sacct -j "${SLURM_ID}" --format=State,Elapsed,ExitCode --noheader 2>/dev/null || \
      echo "unable to query SLURM")
  fi

  jq -n \
    --arg id "$JOB_ID" \
    --arg slurm_id "$SLURM_ID" \
    --arg slurm_status "$SLURM_STATUS" \
    '{ok: true, id: $id, state: "RUNNING", slurm_job_id: (if $slurm_id != "" then $slurm_id else null end), slurm_status: (if $slurm_status != "" then $slurm_status else null end)}'
fi
