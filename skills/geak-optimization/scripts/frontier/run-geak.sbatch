#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#SBATCH --job-name=geak-optimize
#SBATCH --partition=batch
#SBATCH --nodes=1
#SBATCH --time=02:00:00
#SBATCH --gpu-counters=0
#SBATCH --output=slurm-%j.out
#SBATCH --error=slurm-%j.err
#
# GEAK v4 kernel optimization -- Frontier (OLCF), no container.
#
# GEAK v4 (AMD-AGI/GEAK main) is a set of Claude Code Workflows, not a standalone CLI, so
# this job runs Claude Code headless on the compute node and asks it to run
# <GEAK_HOME>/kernel_workflow on a copy of the task. Compute nodes reach the Anthropic API
# through the OLCF proxy (proxy.ccs.ornl.gov:3128, see OLCF "PyTorch on Frontier" > Proxy
# Settings). Account: export SBATCH_ACCOUNT=<project> or pass -A at submission.
#
# Usage (normally via geak_job.py submit, next to this file):
#   sbatch -A <project> --output=<project>/geak-output/jobs/<id>/slurm-%j.out \
#          --error=<project>/geak-output/jobs/<id>/slurm-%j.err \
#          run-geak.sbatch <project>/geak-output/jobs/<id> <project>
#
# Requires (in the project directory, argument 2, default $SLURM_SUBMIT_DIR):
#   .anthropic_api_key (mode 600)         API key (sk-ant-api...) or Claude Code OAuth token
#                                         (or ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN exported)
#   .geak-frontier.env                    GEAK_HOME, GEAK_VENV (from setup-geak.sh)
# Optional environment: GEAK_MODEL (Claude model for the workflow), GEAK_ROCM_MODULE
# (default rocm/7.13.0), CLAUDE_BIN, GEAK_PROFILER_PRIORITY, GEAK_IDLE_EXIT_S.
#
# Safety: GEAK's workflows require --dangerously-skip-permissions, so the agent can run any
# command and modify any file this account can write. The task is copied into the run
# directory and the agent starts there, but that is not a sandbox.

set -euo pipefail

SCRIPT_DIR="$(realpath "${2:-${SLURM_SUBMIT_DIR:?}}")"   # project directory
JOB_DIR="${1:?Usage: sbatch run-geak.sbatch <job_dir> [project_dir]}"
[[ "${JOB_DIR}" == /* ]] || JOB_DIR="${SCRIPT_DIR}/${JOB_DIR}"
JOB_DIR="$(realpath "${JOB_DIR}")"
JOB_ID="$(basename "${JOB_DIR}")"
RUN_DIR="${SCRIPT_DIR}/geak-output/runs/${JOB_ID}"
mkdir -p "${RUN_DIR}"
echo "${SLURM_JOB_ID}" > "${JOB_DIR}/slurm_job_id"

fail() {
  echo "ERROR: $*" >&2
  echo "$*" > "${JOB_DIR}/FAILED"
  exit 1
}
# Any exit without a DONE/FAILED marker (e.g. set -e) must still leave a terminal state,
# or `geak_job.py status` cannot tell how the job ended.
mark_unfinished() {
  local status=$?
  if [[ ! -f "${JOB_DIR}/DONE" && ! -f "${JOB_DIR}/FAILED" ]]; then
    echo "job script exited with status ${status} before finishing (see slurm-${SLURM_JOB_ID}.err)" > "${JOB_DIR}/FAILED"
  fi
}
trap mark_unfinished EXIT

# shellcheck source=/dev/null
[[ -f "${SCRIPT_DIR}/.geak-frontier.env" ]] && source "${SCRIPT_DIR}/.geak-frontier.env"
: "${GEAK_HOME:?GEAK_HOME not set (run scripts/frontier/setup-geak.sh)}"
: "${GEAK_VENV:?GEAK_VENV not set (run scripts/frontier/setup-geak.sh)}"
[[ -f "${GEAK_HOME}/kernel_workflow/kernel_workflow.js" ]] || fail "kernel_workflow not found under ${GEAK_HOME}"

# Credential: an Anthropic API key (sk-ant-api...) or a Claude Code OAuth token
# (`claude setup-token`). Exactly one is exported, since an API key overrides OAuth.
if [[ -z "${ANTHROPIC_API_KEY:-}${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
  KEY_FILE="${SCRIPT_DIR}/.anthropic_api_key"
  [[ -f "${KEY_FILE}" ]] || fail "no credential: set ANTHROPIC_API_KEY/CLAUDE_CODE_OAUTH_TOKEN or create ${KEY_FILE}"
  [[ "$(stat -c %a "${KEY_FILE}")" == 600 ]] || fail "${KEY_FILE} must be mode 600"
  credential="$(tr -d '[:space:]' < "${KEY_FILE}")"
  if [[ "${credential}" == sk-ant-api* ]]; then
    export ANTHROPIC_API_KEY="${credential}"
  else
    export CLAUDE_CODE_OAUTH_TOKEN="${credential}"
  fi
  unset credential
fi
if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
  unset CLAUDE_CODE_OAUTH_TOKEN
  echo "Credential: Anthropic API key"
else
  echo "Credential: Claude Code OAuth token"
fi

CLAUDE_BIN="${CLAUDE_BIN:-$(command -v claude || echo "${HOME}/.local/bin/claude")}"
[[ -x "${CLAUDE_BIN}" ]] || fail "Claude Code CLI not found (set CLAUDE_BIN)"

CONFIG="${JOB_DIR}/config.json"
[[ -f "${CONFIG}" ]] || fail "config.json not found at ${CONFIG}"
cfg() { python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], sys.argv[3]))" "${CONFIG}" "$1" "$2"; }
KERNEL_NAME="$(cfg kernel_filename '')"
TASK="$(cfg task 'Optimize this kernel for performance.')"
BUDGET="$(cfg budget 2)"
GPUS="$(cfg gpus 1)"
OPTIONS="$(cfg options '')"
[[ "${GPUS}" =~ ^[1-8]$ ]] || fail "gpus must be 1-8 (got ${GPUS})"
GPU_IDS="$(seq -s, 0 $((GPUS - 1)))"
[[ -e "${JOB_DIR}/${KERNEL_NAME}" ]] || fail "kernel not found at ${JOB_DIR}/${KERNEL_NAME}"

# The agent works on a copy; the submitted original stays untouched.
WORK="${RUN_DIR}/task"
rm -rf "${WORK}"
if [[ -d "${JOB_DIR}/${KERNEL_NAME}" ]]; then
  cp -r "${JOB_DIR}/${KERNEL_NAME}" "${WORK}"
else
  mkdir -p "${WORK}" && cp "${JOB_DIR}/${KERNEL_NAME}" "${WORK}/"
fi

# GEAK auto-detects gfx90a as "MI300"; give it the real MI250X facts.
HW_NOTE="Hardware note: this node is an AMD Instinct MI250X GCD (gfx90a, CDNA2), not MI300: \
110 CUs, wave64, 64 KB LDS per CU, 8 MB L2, 64 GB HBM2e at 1.6 TB/s, MFMA peaks per GCD \
47.9 TF/s FP32 and 191.5 TF/s FP16/BF16, vector FP32 23.9 TF/s. Use CDNA2 MFMA \
instructions only; do not assume CDNA3 features."
PROMPT="Use the workflow at ${GEAK_HOME}/kernel_workflow to optimize ${WORK}, budget ${BUDGET}, \
gpu_ids ${GPU_IDS}, exp_root ${RUN_DIR}/exp${OPTIONS:+, ${OPTIONS}}. Task: ${TASK} ${HW_NOTE} \
This is a non-interactive batch job: after launching the workflow, wait until it has finished \
before ending your turn (the process exits when your turn ends), then summarize the final report."
printf '%s\n' "${PROMPT}" > "${JOB_DIR}/task.txt"

echo "======================================================================"
echo "GEAK v4 (Frontier) -- Job ${SLURM_JOB_ID}"
echo "  Job dir:  ${JOB_DIR}"
echo "  Run dir:  ${RUN_DIR}"
echo "  Task dir: ${WORK}"
echo "  GEAK:     ${GEAK_HOME} ($(git -C "${GEAK_HOME}" rev-parse --short HEAD 2>/dev/null || echo '?'))"
echo "  Claude:   $("${CLAUDE_BIN}" --version 2>/dev/null)"
echo "  Budget:   ${BUDGET}   GCDs: ${GPUS} (gpu_ids ${GPU_IDS})"
echo "======================================================================"

# GPU tools for the agent's builds/benchmarks/profiles. module load only; no purge.
module load "${GEAK_ROCM_MODULE:-rocm/7.13.0}" >/dev/null 2>&1 || echo "WARNING: could not load ${GEAK_ROCM_MODULE:-rocm/7.13.0}"

export GEAK_HOME GEAK_VENV CLAUDE_BIN PROMPT RUN_DIR WORK
# Print mode otherwise kills GEAK's background workflow after 600 s and still exits 0.
export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0
export GEAK_MODEL="${GEAK_MODEL:-}"
# GEAK's profile_kernel.sh tries the profilers in PROFILER_PRIORITY and falls back to
# benchmark-only when none produces output. On this stack rocprof-compute rejects GEAK's
# profile name ("/" not permitted) and rocprofv3 aborts on pip/TheRock PyTorch builds
# (LLVM 'spirv-expand-step') and then hangs, so the default skips profiling.
export PROFILER_PRIORITY="${GEAK_PROFILER_PRIORITY:-none}"
export CLAUDE_CONFIG_DIR="${JOB_DIR}/claude-config"   # never touch the user's interactive config
mkdir -p "${CLAUDE_CONFIG_DIR}"

set +e
srun -N1 -n1 --cpus-per-task=56 --threads-per-core=1 --gpus-per-task="${GPUS}" --gpu-bind=closest \
  bash -c '
    set -euo pipefail
    # shellcheck source=/dev/null
    source "${GEAK_VENV}/bin/activate"
    # OLCF proxy for outbound HTTPS from the compute node (Claude Code is Node.js and
    # honours HTTPS_PROXY; no socks all_proxy, which httpx-based tools cannot use).
    export https_proxy=http://proxy.ccs.ornl.gov:3128/ http_proxy=http://proxy.ccs.ornl.gov:3128/
    export HTTPS_PROXY=$https_proxy HTTP_PROXY=$http_proxy
    export no_proxy="localhost,127.0.0.0/8,*.ccs.ornl.gov" NO_PROXY="localhost,127.0.0.0/8,*.ccs.ornl.gov"
    export IS_SANDBOX=1 DISABLE_AUTOUPDATER=1
    model_args=()
    [[ -n "${GEAK_MODEL}" ]] && model_args=(--model "${GEAK_MODEL}")
    cd "${RUN_DIR}"
    log="${RUN_DIR}/claude-transcript.jsonl"
    rc_file="${RUN_DIR}/.claude_rc"
    rm -f "${rc_file}" "${RUN_DIR}/.idle_exit"
    # The wrapper records the real exit code; the watchdog below polls for that file
    # (a finished-but-unreaped child still answers kill -0, so that is not a liveness test).
    ( set +e
      "${CLAUDE_BIN}" -p "${PROMPT}" "${model_args[@]}" --dangerously-skip-permissions \
        --output-format stream-json --verbose > "${log}"
      echo $? > "${rc_file}" ) &
    wrapper=$!
    # Idle watchdog: headless Claude Code can stay alive after GEAK has finished (observed
    # more than 10 minutes after the final result). Once a result
    # record exists and the transcript has been silent for GEAK_IDLE_EXIT_S seconds,
    # stop the process and count the run as finished.
    idle_limit="${GEAK_IDLE_EXIT_S:-900}"
    while [[ ! -f "${rc_file}" ]]; do
      sleep 10
      [[ -f "${rc_file}" ]] && break
      if grep -q "\"type\":\"result\"" "${log}" 2>/dev/null \
         && (( $(date +%s) - $(stat -c %Y "${log}") > idle_limit )); then
        echo "[geak] claude idle ${idle_limit}s after its final result; stopping it" >&2
        touch "${RUN_DIR}/.idle_exit"
        pkill -TERM -P "${wrapper}" 2>/dev/null || true
        sleep 10
        pkill -KILL -P "${wrapper}" 2>/dev/null || true
        break
      fi
    done
    wait "${wrapper}" 2>/dev/null || true
    [[ -f "${RUN_DIR}/.idle_exit" ]] && exit 0
    exit "$(cat "${rc_file}" 2>/dev/null || echo 1)"
  '
STATUS=$?

REPORT_MD="$(find "${RUN_DIR}/exp" -name final_report.md 2>/dev/null | head -1 || true)"
python3 - "${RUN_DIR}" "${STATUS}" "${REPORT_MD}" <<'EOF'
import json, sys
run_dir, status, report = sys.argv[1], int(sys.argv[2]), sys.argv[3]
result = None
try:
    for line in open(f"{run_dir}/claude-transcript.jsonl"):
        msg = json.loads(line)
        if msg.get("type") == "result":
            result = {k: msg.get(k) for k in ("subtype", "is_error", "num_turns", "duration_ms", "total_cost_usd", "result")}
except (OSError, ValueError):
    pass
json.dump({"claude_exit_code": status, "final_report_md": report or None, "claude_result": result},
          open(f"{run_dir}/final_report.json", "w"), indent=2)
# Claude Code can exit 0 with an error result (e.g. authentication); report that as failure.
sys.exit(1 if status or not result or result.get("is_error") else 0)
EOF
OUTCOME=$?
set -e

if [[ ${OUTCOME} -eq 0 ]]; then
  touch "${JOB_DIR}/DONE"
  echo "[geak] Optimization complete. Results in ${RUN_DIR}/"
else
  MSG="$(python3 -c "import json,sys; r=(json.load(open(sys.argv[1])).get('claude_result') or {}); print(str(r.get('result') or 'no result')[:300])" "${RUN_DIR}/final_report.json" 2>/dev/null || echo unknown)"
  echo "GEAK optimization failed (claude exit ${STATUS}): ${MSG}; see ${RUN_DIR}/claude-transcript.jsonl" > "${JOB_DIR}/FAILED"
  exit 1
fi
