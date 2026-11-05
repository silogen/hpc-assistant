# Running GEAK on LUMI

LUMI-G nodes use AMD MI250x GPUs (8 GCDs per node, gfx942 / CDNA2). Jobs run via Slurm.
Containers use Singularity/Apptainer. The LUMI AI Factory provides curated ROCm + PyTorch
container images with Triton pre-installed.

Throughout, `$PROJECT` is your LUMI project ID (e.g. `project_462xxxxxx`).
Find yours with `lumi-allocations` or `groups`.

## Filesystem layout

| Path | Purpose |
|---|---|
| `/projappl/$PROJECT/$USER/` | Permanent software installs — use this for GEAK env and repo |
| `/scratch/$PROJECT/` | Job I/O and experiment output — use this for GEAK run dirs |
| `$HOME` | Small config only — do not install packages here (inode quota) |

**`/projappl/$PROJECT/` is group-writable and shared with the whole project**, including its
file-count quota (check with `lumi-quota`). Install under `/projappl/$PROJECT/$USER/`, never
at the project root, or two members will overwrite each other's `geak-env`.

**Important:** `/scratch/$PROJECT` and `/projappl/$PROJECT` are symlinks on LUMI.
Always use the full real path in Singularity `-B` bind mounts.

## One-time setup

### 1. Clone / copy the GEAK repo to projappl

```bash
export PROJECT=project_46XXXXXXX
export GEAK_ROOT=/projappl/$PROJECT/$USER/geak
mkdir -p "$GEAK_ROOT"
cd "$GEAK_ROOT"
git clone https://github.com/AMD-AGI/GEAK.git
```

Or copy an existing checkout:
```bash
cp -r /path/to/local/GEAK "$GEAK_ROOT/GEAK"
```

### 2. Install GEAK with pip-containerize (tykky)

Do **not** use plain `pip install` on LUMI — it creates thousands of small files on Lustre
and exhausts your inode quota. Use `pip-containerize` (the LUMI container wrapper / tykky),
which bakes the environment into a SquashFS image (a handful of files on Lustre):

The `geak @ file://...` line installs GEAK from the local checkout (it is not on PyPI);
its `setup.py` bootstraps the Claude Code CLI. Run everything below in a **single shell
invocation** — module loads do not persist between agent tool calls (~2–5 minutes):

```bash
cat > "$GEAK_ROOT/geak-reqs.txt" << EOF
pyyaml
requests
datasets
metrix @ git+https://github.com/AMDResearch/intellikit.git@2f61453a779980b00504ea3b772ff4a1a1c3f4ad#subdirectory=metrix
geak @ file://$GEAK_ROOT/GEAK
EOF

module load LUMI/25.09 partition/G
module load lumi-container-wrapper/0.5.2-cray-python-3.11.7

pip-containerize new \
    --prefix "$GEAK_ROOT/geak-env" \
    "$GEAK_ROOT/geak-reqs.txt"
```

Keep the requirements file in `$GEAK_ROOT`, not `/tmp` — `/tmp` is node-local and cleaned
between sessions.

Verify the install:

```bash
export PATH="$GEAK_ROOT/geak-env/bin:$PATH"
python -c "import importlib.metadata as m; print(m.version('geak'), m.version('metrix'))"
ls $HOME/.claude
```

Note: GEAK setup installs Claude in `$HOME`, not in `$GEAK_ROOT/geak-env/bin/claude`.

Do not add `--slim` (its image has no `git`, which `metrix` needs) and do not remove a
dependency to force the build through — that yields an environment with no GEAK in it.

### 3. Confirm the container image path

GEAK must run inside a ROCm + PyTorch container so that Triton and HIP are available on the
compute node. The LUMI AI Factory provides these images:

```bash
ls /appl/local/laifs/containers/
```

Look for `lumi-multitorch-full-*`. The path changes with each release; always verify before
submitting. Example path (update to current):

```
/appl/local/laifs/containers/lumi-multitorch-u24r64f21m43t29-20260124_092648/lumi-multitorch-full-u24r64f21m43t29-20260124_092648.sif
```

## Slurm batch job script

For a single-kernel `kernel_workflow` run on one GPU (`small-g` partition):

```bash
#!/bin/bash
#SBATCH --job-name=geak-kernel
#SBATCH --account=project_46XXXXXXX
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=7
#SBATCH --gpus-per-node=1
#SBATCH --mem=60G
#SBATCH --time=02:00:00
#SBATCH --output=logs/geak_%j.out
#SBATCH --error=logs/geak_%j.err

set -euo pipefail

PROJECT=project_46XXXXXXX
export ANTHROPIC_API_KEY=<your-key-here>

# Update SIF path if a newer image is available:
#   ls /appl/local/laifs/containers/
SIF=/appl/local/laifs/containers/lumi-multitorch-u24r64f21m43t29-20260124_092648/lumi-multitorch-full-u24r64f21m43t29-20260124_092648.sif

GEAK_ROOT="/projappl/${PROJECT}/${USER}/geak"
GEAK_ENV="${GEAK_ROOT}/geak-env"
GEAK_WF="${GEAK_ROOT}/GEAK/kernel_workflow"
KERNEL_PATH="${GEAK_ROOT}/my_kernel_task"             # edit to your task dir
RUN_DIR="/scratch/${PROJECT}/geak-runs/${SLURM_JOB_ID}"

mkdir -p "${RUN_DIR}"

# `module purge` is correct inside a batch job (fresh job environment).
# Never run it on a login node — it breaks the interactive shell.
module purge
module use /appl/local/laifs/modules
module load lumi-aif-singularity-bindings

# Bind mounts use full real paths (projappl/scratch are symlinks on LUMI).
srun singularity run \
    -B "/projappl/${PROJECT}:/projappl/${PROJECT}" \
    -B "/scratch/${PROJECT}:/scratch/${PROJECT}" \
    "${SIF}" \
    bash -c "
        set -euo pipefail
        export PATH=\"${GEAK_ENV}/bin:\$PATH\"
        export ANTHROPIC_API_KEY=\"${ANTHROPIC_API_KEY}\"
        export IS_SANDBOX=1
        # GEAK runs its workflow as a background task. Claude Code's print mode
        # waits a bounded time for background work and then terminates it --
        # 600s by default, which silently kills the run mid-optimization and
        # still exits 0. Disable the ceiling so SLURM --time is the only bound.
        export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0
        cd \"${RUN_DIR}\"
        claude --dangerously-skip-permissions -p \
            'use ${GEAK_WF} to optimize ${KERNEL_PATH}'
    "

echo "==> GEAK run complete. Artifacts: ${RUN_DIR}"
```

Submit with:
```bash
mkdir -p logs
sbatch run_geak.sh
```

## Monitoring the job

```bash
squeue --me                          # check job state
tail -f logs/geak_<jobid>.out        # follow Claude Code transcript live
scontrol show job <jobid>            # detailed job info
```

To attach interactively to a running job (e.g. to run `rocm-smi`):
```bash
srun --jobid=<jobid> --interactive --pty /bin/bash
```

## Experiment output

By default `kernel_workflow` writes output to `<exp_root>/team_<kernel>_<timestamp>/<kernel>/`.
In the job script above, with `cd "${RUN_DIR}"` before launching Claude, `exp_root` defaults
to `${RUN_DIR}/GEAK/exp/` (the sibling of the `kernel_workflow/` directory).

To set an explicit output root, add `exp_root /scratch/$PROJECT/geak-results` to the GEAK prompt:

```
use $GEAK_ROOT/GEAK/kernel_workflow to optimize $GEAK_ROOT/my_kernel_task, exp_root /scratch/$PROJECT/geak-results
```
