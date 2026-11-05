# rocprofv3 on Frontier

Frontier-specific notes for the `profiling` skill: module selection, GCD binding, per-rank output, containers and a full-node job example. The general rocprofv3 options in [SKILL.md](../SKILL.md) apply unchanged.

## Contents

- Modules and ROCm versions
- Node layout and binding
- Per-rank profiling with `profile_rank.py`
- Counter collection
- Python
- Apptainer
- Slurm job examples
- HIP smoke workload and offline tests

All GPU commands run inside a compute allocation. An `salloc` shell is still on the login node, so launch GPU work (including `rocminfo` and `rocprofv3 --list-avail`) with `srun`. Stage software, images and input data before the job.

## Modules and ROCm versions

Load the ROCm module the application was built with, then check what the installed profiler supports:

```bash
module spider rocm
module load rocm             # or the pinned version used to build the app
module list
command -v rocprofv3
rocprofv3 --version
rocprofv3 --help
```

OLCF documents rocprofv3 from ROCm 6.2 onward; available flags differ between releases, so check `--help` rather than the latest online manual. Do not use legacy `rocprof` with ROCm 6.2: OLCF reports an accuracy issue that is fixed in 6.3. If only legacy `rocprof` is available on a newer ROCm, its syntax differs from v3:

```bash
rocprof --stats --timestamp on -o results.csv ./myapp
rocprof -d trace --hip-trace ./myapp
```

Record the module list, profiler version, build revision, input and binding for every run.

## Node layout and binding

A Frontier node has 4 MI250X packages, each with 2 GCDs, so Slurm exposes 8 GPUs (64 GB HBM each). In the default low-noise mode 56 cores are available to applications, 7 per GCD.

Standard full-node binding is one rank per GCD:

```bash
srun --ntasks-per-node=8 -c7 --threads-per-core=1 \
  --distribution=block:cyclic --cpu-bind=threads \
  --gpus-per-task=1 --gpu-bind=closest ./app
```

Allocate all 8 GCDs even for a single-rank test (`--gpus-per-node=8 --gpu-bind=closest`) so Slurm can pick the GCD closest to the rank's cores. With Slurm's GPU mask each rank sees one device as logical device 0. Do not add a `ROCR_VISIBLE_DEVICES=$SLURM_LOCALID` wrapper on top: Frontier's CPU-to-GCD ordering is not identity rank order. Verify the mapping (one visible device per rank, distinct PCI bus IDs per node) with the application's own log or OLCF's `hello_jobstep`.

## Per-rank profiling with profile_rank.py

Run one profiler per rank, inside `srun`, not one around `srun`. Slurm does not expand `%j` or `%t` inside profiler arguments, so per-rank output paths must come from environment variables evaluated in each rank.

[scripts/profile_rank.py](../scripts/profile_rank.py) does this. It:

- refuses to run outside a Slurm compute step;
- picks `rocprofv3` (or legacy `rocprof` if v3 is absent and `CRAY_ROCM_VERSION`/`ROCM_VERSION` is not 6.2) and checks the needed flags in `--help`;
- writes to `$PROFILE_ROOT/job_<id>/step_<id>/rank_<procid>_<host>_<pid>/` and refuses to reuse an existing directory;
- saves the profiler help/version and a `launch.json` manifest (command, host, affinity, GPU visibility variables, modules, exit code);
- keeps the application's working directory and arguments, and returns its exit status.

Environment:

| Variable | Meaning |
| -------- | ------- |
| `PROFILE_ROOT` | Absolute output directory on shared storage (required) |
| `PROFILE_MODE` | `runtime` (kernel + HIP + memory copy trace, default) or `kernel` |
| `PROFILE_TOOL` | `auto` (default), `rocprofv3` or `rocprof` |

```bash
export PROFILE_ROOT="${RUN_DIR:?}/profiles"
srun ... python3 /abs/path/profile_rank.py ./my_gpu_app --arg
```

Keep the helper, binaries, inputs and generated configuration on Orion (`/lustre/orion/...`), not in a `/tmp` that exists only on the batch node.

## Counter collection

Discover counters on a compute task with the same module and GPU visibility as the application:

```bash
srun -N1 -n1 -c7 --gpus-per-node=8 --gpu-bind=closest rocprofv3 --list-avail
```

MI250X is `gfx90a`; validate counter names and `--extra-counters` expressions against the MI200 counter reference. Multiple counter groups replay the whole application, so the input must be repeatable. For MPI, run one counter group per coordinated `srun` rather than replaying ranks independently. Do not run competing counter collectors at the same time; `#SBATCH --gpu-counters=0` disables Frontier's optional counter daemon for the job.

Counter-instrumented runtimes are not comparable with uninstrumented ones; time baseline and profiled runs separately.

## Python

Use a ROCm build of the framework (in PyTorch, `torch.version.hip` is non-empty; the device API is still `torch.cuda`). Each rank sees one GCD, so select `cuda:0`, not `SLURM_LOCALID`.

```bash
export PROFILE_ROOT="${RUN_DIR:?}/profiles"
srun -N1 -n1 -c7 --threads-per-core=1 --cpu-bind=threads \
  --gpus-per-node=8 --gpu-bind=closest \
  python3 "$HELPER" python my_gpu_script.py --epochs 1
```

A quick check that the Python stack reaches the GPU:

```bash
python3 "$HELPER" python -c 'import torch; assert torch.version.hip; assert torch.cuda.device_count() == 1; x=torch.rand(1024,1024,device="cuda:0"); y=x@x; torch.cuda.synchronize(); print(y.isfinite().all().item())'
```

## Apptainer

Frontier supports Apptainer (`module spider apptainer`). Use a pre-staged SIF whose ROCm and profiler match the application and host driver, run the profiler inside the container, and bind Orion paths. `--rocm` can expose host ROCm libraries; avoid mixing incompatible host and container stacks. MPI containers additionally need the Cray MPICH/PMI integration described in the [OLCF container guide](https://docs.olcf.ornl.gov/software/containers/index.html).

```bash
export SHARED="/lustre/orion/${PROJECT:?}/scratch/${USER}"
export RUN_DIR="${SHARED}/runs/${SLURM_JOB_ID}"
export PROFILE_ROOT="${RUN_DIR}/profiles"
export HELPER="${SHARED}/tools/profile_rank.py"
export IMAGE="${SHARED}/images/rocm-profile.sif"
srun -N1 -n1 -c7 --threads-per-core=1 --cpu-bind=threads \
  --gpus-per-node=8 --gpu-bind=closest \
  apptainer exec --rocm --bind "${SHARED}:${SHARED}" "$IMAGE" \
  python3 "$HELPER" python "${SHARED}/app/my_gpu_script.py"
```

The helper runs inside the container and uses the container's profiler. If the image has none, rebuild it; do not install packages during the job.

## Slurm job examples

### One rank, one GCD

Submit from the login node with the variables set; `#SBATCH` lines do not expand shell variables.

```bash
export PROJECT=YOUR_OLCF_PROJECT
export RUN_BASE="/lustre/orion/${PROJECT}/scratch/${USER}/profiling"
export HELPER="/absolute/shared/path/to/profile_rank.py"
mkdir -p "$RUN_BASE"
sbatch -A "$PROJECT" profile.sbatch
```

```bash
#!/bin/bash
#SBATCH --job-name=profile-gpu
#SBATCH --partition=batch
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=8
#SBATCH --cpus-per-task=7
#SBATCH --time=00:10:00
#SBATCH --gpu-counters=0
#SBATCH --output=profile-%j.out
#SBATCH --error=profile-%j.err
set -euo pipefail
: "${RUN_BASE:?Set a shared Orion output directory before sbatch}"
: "${HELPER:?Set an absolute path to profile_rank.py}"
module load rocm                      # same stack used to build the app
export RUN_DIR="${RUN_BASE}/${SLURM_JOB_ID}"
mkdir "$RUN_DIR"
module list >"${RUN_DIR}/modules.txt" 2>&1
export PROFILE_ROOT="${RUN_DIR}/profiles"
export OMP_NUM_THREADS=7 OMP_PLACES=cores OMP_PROC_BIND=close
srun -N1 -n1 -c7 --threads-per-core=1 --cpu-bind=threads \
  --gpus-per-node=8 --gpu-bind=closest \
  --output="${RUN_DIR}/app-%J-%t.out" --error="${RUN_DIR}/app-%J-%t.err" \
  python3 "$HELPER" ./my_gpu_app
```

`%J` and `%t` in `srun --output` are expanded by Slurm (log file names only); the helper builds profiler paths from rank environment variables.

### Full node, 8 MPI ranks

Replace `--ntasks=1` with `--ntasks-per-node=8`, keep 7 CPUs per task, and launch:

```bash
srun --ntasks-per-node=8 -c7 --threads-per-core=1 \
  --distribution=block:cyclic --cpu-bind=threads \
  --gpus-per-task=1 --gpu-bind=closest \
  --output="${RUN_DIR}/app-%J-%t.out" --error="${RUN_DIR}/app-%J-%t.err" \
  python3 "$HELPER" ./my_mpi_gpu_app
```

Each rank gets its own profiler and output directory; MPI stays inside the application. GPU-aware Cray MPICH needs `craype-accel-amd-gfx90a` and `MPICH_GPU_SUPPORT_ENABLED=1`; keep the application's working MPI environment unchanged when adding the profiler. For multiple nodes, set `--nodes` and test on two nodes first.

rocprofv3 records per-rank GPU activity, not MPI communication. For communication analysis use HPE/Cray PAT, HPCToolkit or ROCm Systems Profiler, which OLCF also documents.

## HIP smoke workload and offline tests

[scripts/hip_smoke.cpp](../scripts/hip_smoke.cpp) is a vector-add test that checks every HIP call and all results, and prints the visible device count and PCI bus ID. It expects exactly one visible GCD.

```bash
module load rocm
hipcc --offload-arch=gfx90a -O2 hip_smoke.cpp -o hip_smoke
```

Run it with the one-rank binding above, first as `./hip_smoke`, then as `python3 "$HELPER" ./hip_smoke`. With the full-node binding, 8 independent copies check rank isolation (not MPI). Expected: every rank exits 0 and prints `PASS`, one visible GCD per rank, 8 distinct bus IDs per node, one `launch.json` per rank and non-empty kernel traces.

The helper's launch logic has offline tests that need only the Python standard library (no GPU, ROCm or Slurm). They run it against a fake profiler and check rank isolation, argument passing, exit status and the ROCm 6.2 legacy check:

```bash
python3 -B -m unittest discover -s skills/profiling/tests -v
```

## Resources

- [OLCF Frontier user guide](https://docs.olcf.ornl.gov/systems/frontier_user_guide.html): node layout, GPU binding, profiling tools, ROCm 6.2 `rocprof` note.
- [OLCF container guide](https://docs.olcf.ornl.gov/software/containers/index.html): Apptainer on Frontier.
- [rocprofv3 process attachment](https://rocm.docs.amd.com/projects/rocprofiler-sdk/en/latest/how-to/using-rocprofv3-process-attachment.html): `--pid` and `--attach-duration-msec`, on releases that support them.
- [Legacy rocprof manual](https://rocm.docs.amd.com/projects/rocprofiler/en/latest/how-to/using-rocprof.html).
- [AMD MI250 microarchitecture](https://rocm.docs.amd.com/en/latest/reference/gpu-arch/mi250.html): CDNA2, GCDs and memory hierarchy.
