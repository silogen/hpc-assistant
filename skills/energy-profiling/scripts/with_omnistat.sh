#!/usr/bin/env bash
# Run once on the batch node: with_omnistat.sh srun [binding] APPLICATION [ARGS...]
# Requires the preloaded Frontier omnistat-wrapper module and an unused shared RUN_DIR.
set -euo pipefail
: "${SLURM_JOB_ID:?Run on an allocated compute node}"
: "${RUN_DIR:?Set an absolute, unused shared Orion output directory}"
: "${OMNISTAT_WRAPPER:?Load the Frontier omnistat-wrapper module}"
[[ $# -gt 0 ]] || { printf 'Usage: with_omnistat.sh COMMAND [ARGS...]\n' >&2; exit 2; }
[[ $RUN_DIR = /* ]] || { printf 'RUN_DIR must be absolute\n' >&2; exit 2; }
[[ -x $OMNISTAT_WRAPPER ]] || { printf 'OMNISTAT_WRAPPER must be one executable path\n' >&2; exit 2; }
# In an interactive allocation first srun onto the compute node; an salloc login shell is not enough.
[[ $(hostname) != *login* ]] || { printf 'Do not monitor from a login node\n' >&2; exit 2; }
# The batch shell is the intended caller. Do not launch a daemon per application rank.
[[ ${SLURM_STEP_ID:-batch} = batch ]] || { printf 'Invoke once from the batch shell, not per srun rank\n' >&2; exit 2; }
interval=${OMNISTAT_INTERVAL:-1.0}
[[ $interval =~ ^[0-9]+([.][0-9]+)?$ && $interval =~ [1-9] ]] || { printf 'Invalid sampling interval\n' >&2; exit 2; }
# Refuse an existing run directory to avoid sharing a live DB or overwriting evidence.
mkdir -p "$(dirname "$RUN_DIR")"
mkdir "$RUN_DIR"
cd "$RUN_DIR"
export OMNISTAT_VICTORIA_DATADIR="$RUN_DIR/data_prom"
# Do not export the full environment (may contain credentials).
printf 'job_id=%s\nhost=%s\nmodules=%s\ninterval=%s\n' \
    "$SLURM_JOB_ID" "$(hostname)" "${LOADEDMODULES:-unknown}" "$interval" > run-metadata.txt
printf 'command=' >> run-metadata.txt
printf '%q ' "$@" >> run-metadata.txt
printf '\n' >> run-metadata.txt
"$OMNISTAT_WRAPPER" usermode --help > usermode-help.txt 2>&1
"$OMNISTAT_WRAPPER" query --help > query-help.txt 2>&1
attempted=0
started=0
app_rc=not_run
cleanup() {
    local rc=$? telemetry_rc=0
    trap - EXIT TERM INT
    set +e
    if (( attempted )); then
        "$OMNISTAT_WRAPPER" usermode --stopexporters > stop-exporters.log 2>&1
        if (( $? != 0 )); then telemetry_rc=1; fi
        if (( started )); then
            "$OMNISTAT_WRAPPER" query --job "$SLURM_JOB_ID" --interval "$interval" --export > report.txt 2>&1
            if (( $? != 0 )); then telemetry_rc=1; fi
        fi
        "$OMNISTAT_WRAPPER" usermode --stopserver > stop-server.log 2>&1
        if (( $? != 0 )); then telemetry_rc=1; fi
    fi
    printf 'application_exit=%s\nwrapper_exit=%s\ntelemetry_exit=%s\n' "$app_rc" "$rc" "$telemetry_rc" > status.txt
    # Preserve workload/start failure; make telemetry failure nonzero when app succeeded.
    if (( rc == 0 && telemetry_rc != 0 )); then rc=90; fi
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
attempted=1
"$OMNISTAT_WRAPPER" usermode --start --interval "$interval" > start.log 2>&1
started=1
# Preserve caller cwd so relative application/data paths retain their original meaning.
# Monitoring outputs stay in RUN_DIR; the application command runs from its invocation directory.
set +e
(cd "${OLDPWD:?}" && "$@") > application.out 2> application.err
app_rc=$?
set -e
exit "$app_rc"
