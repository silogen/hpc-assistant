# rocprofv3 scenario examples

## Contents

- Profiling a Python script
- Profiling a Singularity/Apptainer container
- Profiling a SLURM job

---

## 1. Profiling a Python script

Use when the GPU workload runs in a Python process that uses HIP/ROCm (e.g. PyTorch with ROCm, or another ROCm-backed library). The executable passed to rocprofv3 is `python`; the script and its arguments go after `--`.

**Prerequisite:** Python environment with ROCm/HIP (e.g. PyTorch built with ROCm on LUMI). Without a ROCm-backed GPU stack, trace files will not show kernel activity.

**Basic command:**

```bash
rocprofv3 --runtime-trace -S --output-format csv -d ./prof_py -- python my_gpu_script.py
```

**Other invocations:**

```bash
# With script arguments
rocprofv3 --runtime-trace -S --output-format csv -d ./prof_py -- python my_script.py --epochs 10

# Using a module
rocprofv3 --runtime-trace -S --output-format csv -d ./prof_py -- python -m mypackage.train

# Inline script
rocprofv3 --kernel-trace --output-format csv -d ./prof_py -- python -c "import torch; x = torch.rand(1000, 1000, device='cuda'); y = x @ x"
```

Output is written under the directory given by `-d` (e.g. `./prof_py`), with subdirs/files using the `%pid%` placeholder by default.

---

## 2. Profiling a Singularity/Apptainer container

Run rocprofv3 **inside** the container so the profiler uses the container’s ROCm and attaches to the containerized process. **Prerequisite:** the container image includes ROCm and rocprofv3 (e.g. LUMI AI Factory images).

Use full paths for bind mounts (`/scratch/project_xxx`, `/project/project_xxx`) as in the Slurm skill.

**Interactive or batch (single run):**

```bash
singularity exec \
  -B /scratch/project_xxx:/scratch/project_xxx \
  -B /project/project_xxx:/project/project_xxx \
  myimage.sif rocprofv3 --runtime-trace --output-format csv -d ./prof_container -- mycommand
```

**SLURM job that runs a container:** combine with the appropriate partition and account; rocprofv3 still runs inside the container:

```bash
srun --partition=small-g --account=project_xxx --gpus=1 -- \
  singularity exec \
  -B /scratch/project_xxx:/scratch/project_xxx \
  -B /project/project_xxx:/project/project_xxx \
  myimage.sif rocprofv3 --runtime-trace -S --output-format csv -d ./prof_%j -o prof_%j -- mycommand
```

If the container does not include rocprofv3, some setups run rocprofv3 on the host and wrap the container invocation; on LUMI, prefer images that include ROCm and rocprofv3 and run the profiler inside.

---

## 3. Profiling a SLURM job

Run rocprofv3 inside the job script so that the application is executed under the profiler. Use `-d` and `-o` with the `%j` placeholder so each job gets its own output directory and file prefix. Set `--account` and partition (e.g. `small-g` for GPU) as required by LUMI.

### Minimal GPU job (one task, one GPU)

SBATCH style matches the Slurm skill; the only change is wrapping the application with rocprofv3.

```bash
#!/bin/bash
#SBATCH --job-name=profile-gpu
#SBATCH --account=project_<<id>>
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=02:00:00
#SBATCH --output=job_%j.out
#SBATCH --error=job_%j.err

# Optional: ensure rocprofv3 is on PATH (e.g. on LUMI)
export PATH=$PATH:/opt/rocm/bin

srun rocprofv3 --runtime-trace -S --output-format csv \
  -d ./prof_%j -o prof_%j \
  -- ./my_gpu_app
```

Output goes to `./prof_<jobid>/` with files prefixed by `prof_<jobid>`. Summary is printed to stderr (captured in `job_%j.err`).

### Multi-GPU / full-node MPI job

For full-node GPU jobs with one rank per GPU, use the same `select_gpu` wrapper and CPU binding as in the Slurm skill ([references/examples.md](../slurm/references/examples.md)). Run rocprofv3 inside that wrapper so each rank is profiled:

```bash
srun --cpu-bind=${CPU_BIND} ./select_gpu rocprofv3 --runtime-trace -S -d ./prof_%j_%r -o prof_%j_%r -- ./my_mpi_gpu_app
```

Here `%r` can be used if your environment sets a rank identifier; otherwise use distinct output dirs per rank (e.g. `-d ./prof_%j/rank_${SLURM_PROCID}`) to avoid overwriting. For the exact `select_gpu` and `CPU_BIND` setup, see [../../slurm/references/examples.md](../../slurm/references/examples.md).

