#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# Submit a kernel for GEAK optimization via SLURM.
#
# Usage: ./.claude/skills/optimize-kernel/scripts/submit-geak-job.sh \
#          <kernel-file> [gpu_ids] [task]
#
# Arguments:
#   kernel-file  Path to the kernel source (.py)
#   gpu_ids      GPU IDs to use (default: 0)
#   task         Task description for GEAK (default:
#                "Optimize this kernel for performance.")
#
# Model and cost settings are in commandment-config.yaml.
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

KERNEL_FILE="${1:-}"
GPU_IDS="${2:-0}"
TASK="${3:-Optimize this kernel for performance.}"

if [[ -z "$KERNEL_FILE" ]]; then
  err_json "Usage: submit-geak-job.sh <kernel-file> [gpu_ids] [task]"
fi

PROJECT_ROOT="$(find_project_root)"
if [[ -z "$PROJECT_ROOT" ]]; then
  err_json "Could not find project root (no .claude/ or .opencode/ directory in ancestors)"
fi

if [[ -f "${PROJECT_ROOT}/geak-download-notice.sh" ]]; then
  # shellcheck source=/dev/null
  source "${PROJECT_ROOT}/geak-download-notice.sh"
elif [[ -f "${PROJECT_ROOT}/../geak/download-notice.sh" ]]; then
  GEAK_DIR="$(cd "${PROJECT_ROOT}/../geak" && pwd)"
  # shellcheck source=/dev/null
  source "${GEAK_DIR}/download-notice.sh"
fi
if declare -f require_geak_download_acceptance >/dev/null 2>&1; then
  require_geak_download_acceptance "$PROJECT_ROOT" || \
    err_json "GEAK third-party download notice not accepted (see GEAK_THIRD_PARTY_DOWNLOAD_NOTICE.txt)"
fi

SCRIPT_DIR="$PROJECT_ROOT"

if [[ ! -f "${KERNEL_FILE}" ]]; then
  err_json "Kernel file not found: ${KERNEL_FILE}"
fi

KERNEL_FILENAME="$(basename "${KERNEL_FILE}")"
JOB_ID="$(date +%Y%m%d-%H%M%S)-$(head -c2 /dev/urandom | od -An -tx1 | tr -d ' ')"
JOB_DIR="${SCRIPT_DIR}/geak-output/jobs/${JOB_ID}"
RUN_DIR="${SCRIPT_DIR}/geak-output/runs/${JOB_ID}"

mkdir -p "${JOB_DIR}" "${RUN_DIR}"
cp "${KERNEL_FILE}" "${JOB_DIR}/${KERNEL_FILENAME}"

jq -n \
  --arg gpu_ids "$GPU_IDS" \
  --arg kernel "$KERNEL_FILENAME" \
  --arg task "$TASK" \
  '{gpu_ids: $gpu_ids, kernel_filename: $kernel, task: $task}' > "${JOB_DIR}/config.json"

SBATCH_OUT=$(sbatch \
  --output="${JOB_DIR}/slurm-%j.out" \
  --error="${JOB_DIR}/slurm-%j.err" \
  "${SCRIPT_DIR}/run-geak.sbatch" "${JOB_DIR}" 2>&1) || {
  err_json "sbatch failed: ${SBATCH_OUT}"
}

SLURM_JOB_ID=$(echo "${SBATCH_OUT}" | grep -oP '\d+$' || true)

if [[ -z "$SLURM_JOB_ID" ]]; then
  err_json "Could not parse SLURM job ID from: ${SBATCH_OUT}"
fi

echo "$SLURM_JOB_ID" > "${JOB_DIR}/slurm_job_id"

jq -n \
  --arg id "$JOB_ID" \
  --arg slurm_id "$SLURM_JOB_ID" \
  --arg job_dir "$JOB_DIR" \
  '{ok: true, id: $id, slurm_job_id: $slurm_id, job_dir: $job_dir}'
