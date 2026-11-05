# Frontier Slurm Job Examples

Submit every script with `sbatch --account="$PROJECT" FILE` from an Orion work directory. Application binaries, images, and Python environments are placeholders for your own. Check syntax with `bash -n FILE` before submitting.

## Minimal batch script

```bash
#!/bin/bash
#SBATCH --job-name=frontier-hostname
#SBATCH --partition=batch
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=00:05:00
#SBATCH --output=%x-%j.out
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?}"
srun -N1 -n1 hostname
```

## Frontier GPU (MPI, one rank per GPU)

Two nodes, 16 ranks, one GCD per rank. `./my_gpu_mpi_app` should be built with Cray MPICH and ROCm for GPU target `gfx90a`; load the same modules used for the build.

```bash
#!/bin/bash
#SBATCH --job-name=frontier-mpi
#SBATCH --output=%x-%j.out
#SBATCH --error=%x-%j.err
#SBATCH --partition=batch
#SBATCH --nodes=2
#SBATCH --ntasks=16
#SBATCH --ntasks-per-node=8
#SBATCH --cpus-per-task=7
#SBATCH --threads-per-core=1
#SBATCH --time=00:15:00
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?}"
test -x ./my_gpu_mpi_app
module list
export OMP_NUM_THREADS=7
export MPICH_GPU_SUPPORT_ENABLED=1
srun -N2 -n16 -c7 --gpus-per-task=1 --gpu-bind=closest ./my_gpu_mpi_app
```

With default placement, `--gpu-bind=closest` assigns physical GCDs 4,5,2,3,6,7,0,1 to local ranks 0–7. Each rank sees only its own GCD, as device 0.

## Small GPU job (one GCD, whole-node allocation)

The whole node is allocated and charged; use this for tests, or pack several such tasks into one allocation for production.

```bash
#!/bin/bash
#SBATCH --job-name=frontier-one-gcd
#SBATCH --partition=batch
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=7
#SBATCH --threads-per-core=1
#SBATCH --time=00:10:00
#SBATCH --output=%x-%j.out
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?}"
test -x ./my_gpu_app
export OMP_NUM_THREADS=7
srun -N1 -n1 -c7 --gpus-per-task=1 --gpu-bind=closest ./my_gpu_app
```

## Container job with storage binding

Inside a compute allocation, with `IMAGE` set to a SIF file and `WORKDIR` to an Orion directory:

```bash
: "${IMAGE:?Set the absolute SIF path}"
: "${WORKDIR:?Set an existing Orion directory}"
test -f "$IMAGE"
test -d "$WORKDIR"
srun -N1 -n1 apptainer exec --bind "$WORKDIR:$WORKDIR" "$IMAGE" /bin/hostname
```

For GPU work use a ROCm image and the OLCF modules `olcf-container-tools`, `apptainer-enable-gpu`, and `apptainer-enable-mpi` (experimental). Do not use `--nv` (NVIDIA).

## Python jobs

### Python serial job (Frontier Miniforge)

Use the `miniforge3` module (`module avail miniforge3` for versions) with your own conda environment. Activate the environment inside the job, after loading the module, because loading it resets to base. Do not install packages into the base environment.

```bash
#!/bin/bash
#SBATCH --job-name=python-serial
#SBATCH --partition=batch
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=00:15:00
#SBATCH --output=%x-%j.out
set -eo pipefail
cd "${SLURM_SUBMIT_DIR:?}"
module load miniforge3
: "${PYTHON_ENV:?Set the existing conda environment path}"
conda activate "$PYTHON_ENV"
command -v python3
python3 your_application.py
```

Serial Python can run directly in the batch shell; use `srun` for parallel Python.

### Container Python MPI job

The image's `mpi4py` must be built against an MPI that is ABI-compatible with the host Cray MPICH. Build it following one recipe from the [container guide](https://docs.olcf.ornl.gov/software/containers_on_frontier.html) (the guide has an MPICH 3.4.2 recipe and newer CPE-based images; do not mix them). The `apptainer-enable-*` modules bind the host MPI and ROCm libraries at runtime; the [OLCF container examples](https://github.com/olcf/olcf_containers_examples) also show explicit binding.

```bash
#!/bin/bash
#SBATCH --job-name=python-mpi
#SBATCH --partition=batch
#SBATCH --nodes=2
#SBATCH --ntasks=16
#SBATCH --ntasks-per-node=8
#SBATCH --cpus-per-task=7
#SBATCH --threads-per-core=1
#SBATCH --time=00:15:00
#SBATCH --output=%x-%j.out
set -eo pipefail
cd "${SLURM_SUBMIT_DIR:?}"
: "${IMAGE:?Set the validated MPI/ROCm SIF path}"
: "${WORKDIR:?Set the existing absolute Orion work directory}"
test -f "$IMAGE"
test -d "$WORKDIR"
module load olcf-container-tools
module load apptainer-enable-mpi
module load apptainer-enable-gpu
module list
export MPICH_GPU_SUPPORT_ENABLED=1
export OMP_NUM_THREADS=7
srun -N2 -n16 -c7 --gpus-per-task=1 --gpu-bind=closest \
  apptainer exec --bind "$WORKDIR:$WORKDIR" "$IMAGE" python3 "$WORKDIR/your_application.py"
```

### Python array job

Each array element is a separate one-node job and is charged as such; `%2` limits how many run at once. List ten absolute input paths, one per line, in `inputs.txt` (spaces in names are fine).

```bash
#!/bin/bash
#SBATCH --job-name=python-array
#SBATCH --partition=batch
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=00:15:00
#SBATCH --array=1-10%2
#SBATCH --output=%x-%A_%a.out
set -eo pipefail
cd "${SLURM_SUBMIT_DIR:?}"
module load miniforge3
: "${PYTHON_ENV:?Set the existing conda environment path}"
conda activate "$PYTHON_ENV"
mapfile -t INPUTS < inputs.txt
if (( ${#INPUTS[@]} != 10 )); then
  printf '%s\n' 'inputs.txt must have exactly ten entries' >&2
  exit 2
fi
DATA=${INPUTS[$((SLURM_ARRAY_TASK_ID - 1))]}
test -f "$DATA"
srun -N1 -n1 python3 your_application.py "$DATA"
```

`%A` and `%a` are the array job ID and element index. For element-wise dependencies between arrays, see `--dependency=aftercorr`.

### PyTorch GPU job (Frontier ROCm container)

Use a ROCm PyTorch image, e.g. AMD's `rocm/pytorch` images as described in the [container guide](https://docs.olcf.ornl.gov/software/containers_on_frontier.html#amd-dockerhub-ai-ml-workload-containers-on-frontier). Pin a specific tag rather than `latest`.

```bash
#!/bin/bash
#SBATCH --job-name=pytorch-one-gcd
#SBATCH --partition=batch
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=7
#SBATCH --threads-per-core=1
#SBATCH --time=00:15:00
#SBATCH --output=%x-%j.out
set -eo pipefail
cd "${SLURM_SUBMIT_DIR:?}"
: "${IMAGE:?Set the validated PyTorch ROCm SIF path}"
: "${WORKDIR:?Set the existing absolute Orion work directory}"
module load olcf-container-tools
module load apptainer-enable-gpu
export OMP_NUM_THREADS=7
srun -N1 -n1 -c7 --gpus-per-task=1 --gpu-bind=closest \
  apptainer exec --bind "$WORKDIR:$WORKDIR" "$IMAGE" python3 "$WORKDIR/myprog.py"
```

PyTorch on ROCm still uses the `torch.cuda` API; check `torch.version.hip` and `torch.cuda.device_count()`. With one GCD per rank, use device 0. For DDP, launch one Slurm task per GCD and set rank/world size from Slurm variables (see [PyTorch on Frontier](https://docs.olcf.ornl.gov/software/analytics/pytorch_frontier.html)); do not combine 8 Slurm tasks with `torchrun --nproc-per-node=8`, which starts 64 workers.

### One-node, eight-GCD validation

Copy [gpu_smoke.py](../scripts/gpu_smoke.py) and [gpu-smoke.sbatch](../templates/gpu-smoke.sbatch) to an Orion run directory, set `PYTHON_ENV` to a ROCm PyTorch conda environment, and submit:

```bash
: "${PROJECT:?Set the charge account}"
: "${PYTHON_ENV:?Set the prepared ROCm PyTorch environment}"
export PYTHON_ENV
sbatch --account="$PROJECT" gpu-smoke.sbatch
```

The job runs eight tasks with one GCD each. A successful run writes eight `gpu_smoke_rank*.json` files, each with `"ok": true`, `device_count` 1, and a HIP version. It tests GPU launch and arithmetic only, not MPI or multi-node communication.

## Key SBATCH parameters

| Parameter | Description | Frontier example |
| --- | --- | --- |
| `--account` | Project charge account (required) | `sbatch --account="$PROJECT" job.sh` |
| `--job-name` | Job name | `my-job` |
| `--partition` | Partition, not QOS | `batch`, `extended`, `service` |
| `--qos` | Optional service class | `debug`, or `develop` with `service` |
| `--time` | Max runtime within node-bin policy | `00:15:00` |
| `--nodes` | Whole nodes | `1`, `2` |
| `--ntasks` | Total MPI/process tasks, specify explicitly | `16` |
| `--ntasks-per-node` | Rank distribution (do not rely on it alone) | `8` |
| `--cpus-per-task` | Physical cores with default one thread/core | `7` |
| `--threads-per-core` | One by default; if using 2, request at allocation and step | `1` |
| `-S` | Reserved cores/node; default 8 | Keep default |
| `--gpus`, `--gpus-per-node` | GPUs per job/node; nodes are still allocated whole | `--gpus-per-node=8` |
| `--gpus-per-task`, `--gpu-bind` | `srun` step placement in these examples | `1`, `closest` |
| `--mem` | Host memory, not GPU HBM; at most 512 GB/node | — |
| `--output`, `--error` | Existing output location | `%x-%j.out` |
| `--open-mode=append` | Preserve output after requeue | Application still needs restart logic |
| `--mail-type`, `--mail-user` | Email notifications supported on Frontier | `END,FAIL`, your address |
| `--constraint=nvme` | Request node-local NVMe | Stage results back before job ends |

**Log placeholders:** `%x` job name, `%j` job ID, `%A`/`%a` array job/element; `%s`, `%t`, `%N` for step, task, node.
