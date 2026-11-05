#!/bin/bash
#SBATCH --job-name=cap-run
#SBATCH --partition=batch
#SBATCH --nodes=1
#SBATCH --time=00:30:00
#SBATCH --output=%x-%j.out
#SBATCH --gpu-power-cap=560
#SBATCH --gpu-srange=500-1700
#SBATCH --gpu-counters=0
#
# One power cap / sclk range per job (header). Max sclk can also change at runtime
# with set_gpu_max_sclk through CAP_FREQS. See SKILL.md.
#
# Usage:
#   sbatch --account="$PROJECT" --export=ALL,CAP_SKILL_DIR=/abs/skill,RUN_ROOT=/abs/runs \
#     cap-run.sbatch srun -N1 -n8 -c7 --gpus-per-task=1 --gpu-bind=closest ./app args
#
# Environment: CAP_SKILL_DIR, RUN_ROOT (required, absolute); CAP_FREQS (MHz list, ":" or " ");
# SET_SCLK_BIN; CAP_SRANGE, CAP_POWER (cross-check of the header); CAP_REP; SAMPLE_INTERVAL;
# IDLE_SECONDS; CAP_STRICT; CAP_SAMPLER; CAP_SYSFS_ROOT (tests).
set -euo pipefail

: "${SLURM_JOB_ID:?submit with sbatch}"
: "${CAP_SKILL_DIR:?set CAP_SKILL_DIR to the skill directory}"
: "${RUN_ROOT:?set RUN_ROOT to an absolute Orion results directory}"
if (( $# == 0 )); then
    echo "usage: sbatch cap-run.sbatch COMMAND [ARGS...]  (caps in the #SBATCH header)" >&2
    exit 2
fi
[[ $RUN_ROOT = /* && $CAP_SKILL_DIR = /* ]] || { echo "RUN_ROOT and CAP_SKILL_DIR must be absolute" >&2; exit 2; }

CAP_REP=${CAP_REP:-0}
SAMPLE_INTERVAL=${SAMPLE_INTERVAL:-0.5}
IDLE_SECONDS=${IDLE_SECONDS:-10}
CAP_STRICT=${CAP_STRICT:-1}
CAP_SAMPLER=${CAP_SAMPLER:-1}
SYSFS_ROOT=${CAP_SYSFS_ROOT:-/}
SET_SCLK_BIN=${SET_SCLK_BIN:-/usr/bin/set_gpu_max_sclk}
SCRIPTS="$CAP_SKILL_DIR/scripts"
NNODES=${SLURM_JOB_NUM_NODES:-1}

job_info=$(scontrol show job "$SLURM_JOB_ID" 2>/dev/null || true)
admin_comment=$(grep -o 'AdminComment=[^ ]*' <<<"$job_info" || true)
adm_srange=$(sed -n 's/.*GPU_SRANGE=\([0-9-]*\).*/\1/p' <<<"$admin_comment")
adm_power=$(sed -n 's/.*GPU_POWER_CAP=\([0-9]*\).*/\1/p' <<<"$admin_comment")
if [[ -z $adm_srange || -z $adm_power ]]; then
    echo "cap-run: WARNING no GPU_SRANGE/GPU_POWER_CAP in AdminComment; using CAP_SRANGE/CAP_POWER or defaults" >&2
fi
req_srange=${CAP_SRANGE:-${adm_srange:-default}}
req_power=${CAP_POWER:-${adm_power:-default}}
read -r exp_lo exp_hi exp_w < <(python3 "$SCRIPTS/capspec.py" expect "$req_srange" "$req_power")
if [[ -n $adm_srange && -n $adm_power ]]; then
    read -r adm_lo adm_hi adm_w < <(python3 "$SCRIPTS/capspec.py" expect "$adm_srange" "$adm_power")
    if [[ "$adm_lo $adm_hi $adm_w" != "$exp_lo $exp_hi $exp_w" ]]; then
        echo "cap-run: CAP_SRANGE/CAP_POWER ($exp_lo-$exp_hi MHz, $exp_w W) disagree with the job" \
             "($adm_srange MHz, $adm_power W from AdminComment); fix the #SBATCH header or the variables" >&2
        exit 5
    fi
fi
job_lo=$exp_lo; job_hi=$exp_hi
CAP_FREQS=${CAP_FREQS:-}
read -r -a FREQS <<< "${CAP_FREQS//:/ }"
for f in "${FREQS[@]}"; do
    if ! [[ $f =~ ^[0-9]+$ ]] || (( f <= job_lo || f > 1700 )); then
        echo "cap-run: invalid CAP_FREQS value '$f' (need $job_lo < MHz <= 1700; sclk min stays $job_lo)" >&2
        exit 2
    fi
done
if (( ${#FREQS[@]} )) && [[ ! -x $SET_SCLK_BIN ]] && ! command -v "$SET_SCLK_BIN" >/dev/null; then
    echo "cap-run: $SET_SCLK_BIN not found on $(hostname -s)" >&2
    exit 2
fi

per_node() {
    srun --overlap -N "$NNODES" --ntasks-per-node=1 -c 1 "$@"
}

set_max_sclk() {
    srun -N "$NNODES" --ntasks-per-node=1 --exclusive "$SET_SCLK_BIN" "$1"
}

verify() {  # $1 = pre|post; srun returns the highest task rc (0 ok, 3 unverified, 4 mismatch)
    per_node bash -c 'exec python3 "$1" --expect-srange "$2" --expect-power-cap "$3" --sysfs-root "$6" --json "$4/verify-$5-$(hostname -s).json"' \
        _ "$SCRIPTS/verify_caps.py" "$exp_lo-$exp_hi" "$exp_w" "$RUN_DIR" "$1" "$SYSFS_ROOT" >> "$RUN_DIR/verify.log" 2>&1
}

RUN_DIR=
app_rc=not_run
verify_pre=unknown
verify_post=unknown
sampler_pid=
freq_changed=0
worst_rc=0

stop_sampler() {
    [[ -n $RUN_DIR ]] && touch "$RUN_DIR/.stop"
    if [[ -n $sampler_pid ]]; then
        for _ in $(seq 1 60); do kill -0 "$sampler_pid" 2>/dev/null || break; sleep 1; done
        kill "$sampler_pid" 2>/dev/null || true
        wait "$sampler_pid" 2>/dev/null || true
        sampler_pid=
    fi
}

close_point() {
    stop_sampler
    if [[ -n $RUN_DIR ]]; then
        printf 'application_exit=%s\nverify_pre=%s\nverify_post=%s\n' \
            "$app_rc" "$verify_pre" "$verify_post" > "$RUN_DIR/status.txt"
        echo "cap-run: results in $RUN_DIR"
    fi
    RUN_DIR=
}

finish() {
    local rc=$?
    trap - EXIT TERM INT
    set +e
    close_point
    if (( freq_changed )); then
        set_max_sclk "$job_hi" > /dev/null 2>&1 || echo "cap-run: WARNING could not restore max sclk $job_hi" >&2
    fi
    exit "$rc"
}
trap finish EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

run_point() {  # $1 = runtime max sclk or "", $2 = index, then the command
    local f=$1 idx=$2 suffix=
    shift 2
    exp_lo=$job_lo; exp_hi=${f:-$job_hi}
    if [[ -n $f ]]; then
        echo "cap-run: setting max sclk to $f MHz on $NNODES node(s) with $SET_SCLK_BIN"
        freq_changed=1
        set_max_sclk "$f"
    fi
    (( ${#FREQS[@]} )) && suffix="-i$idx"
    local label
    label=$(python3 "$SCRIPTS/capspec.py" label "$exp_lo-$exp_hi" "$exp_w")
    RUN_DIR="$RUN_ROOT/$label/rep${CAP_REP}-job${SLURM_JOB_ID}${suffix}"
    app_rc=not_run; verify_pre=unknown; verify_post=unknown
    mkdir -p "$(dirname "$RUN_DIR")"
    mkdir "$RUN_DIR"
    {
        echo "label=$label"
        echo "sclk_min_mhz=$exp_lo"
        echo "sclk_max_mhz=$exp_hi"
        echo "power_cap_w=$exp_w"
        echo "freq_control=$([[ -n $f ]] && echo "set_gpu_max_sclk" || echo "sbatch-header")"
        echo "point_index=$idx"
        echo "rep=$CAP_REP"
        echo "job_id=$SLURM_JOB_ID"
        echo "nodes=$NNODES"
        echo "nodelist=${SLURM_JOB_NODELIST:-unknown}"
        echo "partition=${SLURM_JOB_PARTITION:-unknown}"
        echo "qos=${SLURM_JOB_QOS:-unknown}"
        echo "submit_dir=${SLURM_SUBMIT_DIR:-$PWD}"
        echo "sample_interval_s=$SAMPLE_INTERVAL"
        echo "idle_seconds=$IDLE_SECONDS"
        echo "admin_comment=${admin_comment#AdminComment=}"
        echo "modules=${LOADEDMODULES:-unknown}"
        echo "rocm_version=${CRAY_ROCM_VERSION:-${ROCM_PATH:-unknown}}"
    } > "$RUN_DIR/run.env"
    printf '%q ' "$@" > "$RUN_DIR/command.txt"; echo >> "$RUN_DIR/command.txt"
    printf '%s\n' "$job_info" > "$RUN_DIR/scontrol-job.txt"

    verify_pre=0; verify pre || verify_pre=$?
    case $verify_pre in
        0) echo "cap-run: caps verified ($label)";;
        3) echo "cap-run: WARNING caps could not be read back; see $RUN_DIR/verify.log" >&2;;
        *) echo "cap-run: caps do NOT match the request ($label); see $RUN_DIR/verify.log" >&2
           if [[ $CAP_STRICT = 1 ]]; then exit 4; fi;;
    esac

    if [[ $CAP_SAMPLER = 1 ]]; then
        per_node bash -c 'exec python3 "$1" --interval "$2" --stop-file "$3/.stop" --sysfs-root "$4" --out "$3/samples-$(hostname -s).csv"' \
            _ "$SCRIPTS/gpu_sampler.py" "$SAMPLE_INTERVAL" "$RUN_DIR" "$SYSFS_ROOT" > "$RUN_DIR/sampler.log" 2>&1 &
        sampler_pid=$!
        local n
        for _ in $(seq 1 120); do
            n=$(find "$RUN_DIR" -maxdepth 1 -name 'samples-*.csv' | wc -l)
            (( n >= NNODES )) && break
            kill -0 "$sampler_pid" 2>/dev/null || { echo "cap-run: sampler exited early; see sampler.log" >&2; break; }
            sleep 1
        done
    fi

    sleep "$IDLE_SECONDS"

    local app_start app_end
    app_start=$(date +%s.%N)
    set +e
    (cd "${SLURM_SUBMIT_DIR:-$PWD}" && exec "$@") > "$RUN_DIR/application.out" 2> "$RUN_DIR/application.err"
    app_rc=$?
    set -e
    app_end=$(date +%s.%N)
    printf 'app_start_s=%s\napp_end_s=%s\napp_rc=%s\n' "$app_start" "$app_end" "$app_rc" > "$RUN_DIR/timing.env"
    (( app_rc != 0 && worst_rc == 0 )) && worst_rc=$app_rc

    sleep "$IDLE_SECONDS"
    verify_post=0; verify post || verify_post=$?
    close_point
}

if (( ${#FREQS[@]} )); then
    i=0
    for f in "${FREQS[@]}"; do
        i=$((i + 1))
        run_point "$f" "$i" "$@"
    done
else
    run_point "" 0 "$@"
fi
exit "$worst_rc"
