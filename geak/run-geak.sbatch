#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#SBATCH --job-name=geak-optimize
# LUMI project account: set your own before submitting, e.g.
#   export SBATCH_ACCOUNT=project_XXXXXXXXX
# (Slurm honors SBATCH_ACCOUNT; otherwise it uses your default account.)
# Alternatively, add your own directive here: #SBATCH --account=project_XXXXXXXXX
#SBATCH --partition=small-g
#SBATCH --gpus=1
#SBATCH --time=2:00:00
#SBATCH --output=slurm-%j.out
#SBATCH --error=slurm-%j.err
#
# GEAK kernel optimization -- LUMI (singularity)
#
# Usage (pass output/error paths when submitting):
#   sbatch --output=geak-output/jobs/<job-id>/slurm-%j.out \
#          --error=geak-output/jobs/<job-id>/slurm-%j.err \
#          run-geak.sbatch geak-output/jobs/<job-id>
#
# Requires:
#   - ANTHROPIC_API_KEY in the environment (or in <project>/.anthropic_api_key).
#
# Execution: runs inside the LUMI-curated lumi-multitorch SIF and bootstraps
# GEAK in-job via setup-geak.sh (clones AMD-AGI/GEAK, builds a venv under /tmp).
# Override the multitorch image with LUMI_MULTITORCH_SIF and the GEAK branch
# with GEAK_BRANCH (default dev-silo). See geak/GEAK.md.

set -euo pipefail

MARKER="${SLURM_SUBMIT_DIR:-.}/.geak-third-party-download-accepted"
if [[ ! -f "${MARKER}" ]]; then
  echo "ERROR: GEAK third-party download notice not accepted."
  echo "       See ${SLURM_SUBMIT_DIR}/GEAK_THIRD_PARTY_DOWNLOAD_NOTICE.txt"
  echo "       Run setup with GEAK enabled or: bash geak/accept-download-notice.sh"
  exit 1
fi

JOB_DIR="${1:?Usage: sbatch run-geak.sbatch <job_dir> [config.yaml]}"
CONFIG_YAML="${2:-commandment-config.yaml}"
SCRIPT_DIR="$(realpath "${SLURM_SUBMIT_DIR}")"

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  KEY_FILE="${SCRIPT_DIR}/.anthropic_api_key"
  if [[ -f "${KEY_FILE}" ]]; then
    export ANTHROPIC_API_KEY="$(tr -d '[:space:]' < "${KEY_FILE}")"
  else
    echo "ERROR: ANTHROPIC_API_KEY not set and ${KEY_FILE} not found"
    exit 1
  fi
fi

# LUMI bindings module exposes AMD GPU drivers and HPE/Cray libs into the
# container.
module purge
module use /appl/local/laifs/modules
module load lumi-aif-singularity-bindings

SIF="${LUMI_MULTITORCH_SIF:-/appl/local/laifs/containers/lumi-multitorch-latest.sif}"
if [[ ! -f "${SIF}" ]]; then
  echo "ERROR: multitorch SIF not found at ${SIF}"
  echo "       Set LUMI_MULTITORCH_SIF. See geak/GEAK.md for details."
  exit 1
fi

# Resolve job dir to absolute and normalize
if [[ "${JOB_DIR}" != /* ]]; then
  JOB_DIR="${SCRIPT_DIR}/${JOB_DIR}"
fi
JOB_DIR="$(realpath "${JOB_DIR}")"

# Parse config.json
CONFIG="${JOB_DIR}/config.json"
if [[ ! -f "${CONFIG}" ]]; then
  echo "ERROR: config.json not found at ${CONFIG}"
  exit 1
fi

GPU_IDS=$(python3 -c "import json; print(json.load(open('${CONFIG}'))['gpu_ids'])")
KERNEL_FILENAME=$(python3 -c "import json; print(json.load(open('${CONFIG}'))['kernel_filename'])")
TASK=$(python3 -c "import json; print(json.load(open('${CONFIG}')).get('task', 'Optimize this kernel for performance.'))")

KERNEL_PATH="${JOB_DIR}/${KERNEL_FILENAME}"
if [[ ! -f "${KERNEL_PATH}" ]]; then
  echo "ERROR: kernel file not found at ${KERNEL_PATH}"
  exit 1
fi

# Persist the task to a file so we can pass --task <path> to geak; this avoids
# shell-quoting issues when the task contains quotes, newlines, or $ signs.
TASK_FILE="${JOB_DIR}/task.txt"
printf '%s\n' "${TASK}" > "${TASK_FILE}"

JOB_DIR_REL="${JOB_DIR#${SCRIPT_DIR}/}"
JOB_ID="$(basename "${JOB_DIR}")"
RUN_DIR="${SCRIPT_DIR}/geak-output/runs/${JOB_ID}"
mkdir -p "${RUN_DIR}"

echo "======================================================================"
echo "GEAK optimize -- Job ${SLURM_JOB_ID}"
echo "  Job dir:   ${JOB_DIR}"
echo "  Run dir:   ${RUN_DIR}"
echo "  GPU IDs:   ${GPU_IDS}"
echo "  Config:    ${CONFIG_YAML}"
echo "  SIF:       ${SIF}"
echo "  Task:      ${TASK}"
echo "  GEAK Branch: ${GEAK_BRANCH:-dev-silo}"
echo "======================================================================"

echo "${SLURM_JOB_ID}" > "${JOB_DIR}/slurm_job_id"

# Project bind mount at /workspace — setup-geak.sh uses /workspace/.deps and
# /workspace/.git-credentials.
PROJECT_MNT=/workspace

INNER_SCRIPT="
  set -euo pipefail
  export ANTHROPIC_API_KEY
  export GEAK_BRANCH=${GEAK_BRANCH:-dev-silo}
  ${PROJECT_MNT}/setup-geak.sh
  source /tmp/geak-venv/bin/activate
  CONFIG_ARG=''
  if [ '${CONFIG_YAML}' != 'none' ]; then
    CONFIG_ARG='--config ${PROJECT_MNT}/${CONFIG_YAML}'
  fi
  geak --kernel-url ${PROJECT_MNT}/${JOB_DIR_REL}/${KERNEL_FILENAME} \
       --gpu-ids ${GPU_IDS} \
       -o /geak-output \
       -y --exit-immediately \
       --max-rounds 1 \
       --task ${PROJECT_MNT}/${JOB_DIR_REL}/task.txt \
       \$CONFIG_ARG
"

if srun singularity exec \
  --bind "${SCRIPT_DIR}:${PROJECT_MNT}" \
  --bind "${RUN_DIR}:/geak-output" \
  "${SIF}" \
  bash -c "${INNER_SCRIPT}"; then
  touch "${JOB_DIR}/DONE"
  echo "[geak] Optimization complete. Results in ${RUN_DIR}/"
else
  EXIT_CODE=$?
  echo "GEAK optimization failed (exit ${EXIT_CODE})" > "${JOB_DIR}/FAILED"
  exit "${EXIT_CODE}"
fi
