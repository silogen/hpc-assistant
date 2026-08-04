---
name: slurm
description: Submits and manages jobs on LUMI via Slurm (sbatch, srun, squeue, scancel, sinfo). Covers project account, partitions, LUMI-G (AMD MI250x, 8 GPUs/node), Singularity/Apptainer, and Python and PyTorch jobs. Use when the user mentions LUMI, Slurm, partitions, project account, LUMI-G, MI250x, container jobs, or Python jobs on LUMI.
---

# Slurm on LUMI

LUMI uses Slurm as job scheduler and resource manager. GPU nodes (LUMI-G) use **AMD MI250x** (8 GPUs per node); containers run via **Singularity** or **Apptainer**.

## When to Use This Skill

- Submitting batch or interactive jobs to LUMI (CPU or GPU)
- Choosing partitions and specifying project account
- Managing running or queued jobs and examining the queue
- Writing Slurm job scripts with Singularity/Apptainer containers
- Debugging job failures (account, partition, binding, GPU visibility)
- Checking GPU availability and utilization on LUMI-G (ROCm/rocm-smi)
- Submitting Python jobs (serial, container MPI, or array)

## Project Account (Mandatory)

You must set your project ID in the job script or on the command line.

- **Find your project:** `lumi-allocations` or `groups` (e.g. `project_xxxxxxxxx`).
- **In scripts:** `#SBATCH --account=project_<<id>>`
- **Optional:** In `~/.bashrc`: `export SBATCH_ACCOUNT=project_<<id>>` and `export SALLOC_ACCOUNT=project_<<id>>`

Omit `--account` and you get: `Unable to allocate resources: Job violates accounting/QOS policy (job submit limit, user's size and/or time limits)`.

## Partitions

Main partitions: `standard-g` / `standard` (full-node), `small-g` / `small` (sub-node GPU/CPU), `debug`, `dev-g`, `largemem`, `lumid`. See [references/partitions.md](references/partitions.md) for limits and purposes.

## LUMI-G and GPUs

- **GPUs:** 8 per node. Use `--gpus` or `--gpus-per-node` (e.g. `--gpus-per-node=8`), not `--gres=gpu:...`.
- **Cores:** 56 per node (not 64); jobs requesting 64 cores/node will not run.
- **Full-node GPU jobs:** Pin each rank to a GPU: wrapper setting `ROCR_VISIBLE_DEVICES=$SLURM_LOCALID` and CPU binding (see [references/examples.md](references/examples.md)). Use `rocm-smi --showtoponuma` for topology. Set `MPICH_GPU_SUPPORT_ENABLED=1` for GPU-aware MPI.

## Essential Commands

```bash
sbatch job_script.sh
squeue --me
squeue --me --start
scontrol show job <job_id>
scancel <job_id>
sinfo -p small-g
sacct --starttime=today
```

`ST`: `PD` = pending, `R` = running. Last column = reason (e.g. `Priority`, `Resources`).

## Batch Script Structure

1. Shebang (e.g. `#!/bin/bash`)
2. `#SBATCH` directives (name, account, time, nodes, ntasks, cpus-per-task, mem, partition, GPUs if needed)
3. Environment (e.g. `module load`, exports)
4. `srun` to run the application

**Time:** `hh:mm:ss` or `d-hh:mm:ss`. **Requeue:** `--no-requeue` to disable; `--open-mode=append` to avoid truncated output; `SLURM_RESTART_COUNT` in script. **Email:** not supported on LUMI.

Critical SBATCH options: `--account`, `--partition`, `--time`, `--gpus`/`--gpus-per-node`. Full list: [references/examples.md](references/examples.md).

### Minimal batch example

```bash
#!/bin/bash
#SBATCH --job-name=exampleJob
#SBATCH --account=project_<<id>>
#SBATCH --time=02:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --partition=small

module load MyApp/1.2.3
srun myapp -i input -o output
```

For LUMI-G MPI, small-GPU, container, Python, and PyTorch examples see [references/examples.md](references/examples.md).

## Containers

Singularity/Apptainer available (no module load). Bind storage with `-B` using **full path** (`/scratch/project_xxx`, `/project/project_xxx`) because those paths are symlinks.

```bash
srun --partition=small --account=project_xxx \
  singularity exec -B /scratch/project_xxx:/scratch/project_xxx \
  -B /project/project_xxx:/project/project_xxx \
  myimage.sif mycommand
```

MPI: host MPI (LUMI docs) or `srun --mpi=pmi2`. Do not use `--nv` (NVIDIA); for AMD, use ROCm-capable images or bind host ROCm.

## Storage, Interactive, Dependencies

- **Storage:** `/scratch/project_xxx`, `/project/project_xxx`
- **Interactive:** `salloc` then `srun`; or `srun --pty bash`. Attach to running job: `srun -n 1 --overlap --pty --jobid=<job_id> $SHELL` (add `--gpus=<num>` if the job requested GPUs).
- **Dependencies:** `sbatch --dependency=afterok:123456 job2.sh` (types: `after`, `afterany`, `afterok`, `afternotok`).

## Best Practices

1. Always set `--account=project_xxx`.
2. Request only what you need; use `small`/`small-g` for sub-node jobs.
3. LUMI-G: max 56 cores/node; use `--gpus`/`--gpus-per-node=8` for full node.
4. Full-node GPU: use `select_gpu` wrapper and CPU binding (see examples).
5. Containers: bind full paths for scratch and project.
6. Set a realistic time limit; jobs over limit are killed.
7. Consider `--open-mode=append` and `SLURM_RESTART_COUNT` for requeues.

## Slurm job workflow

- [ ] Requirements (CPU/GPU, nodes, time, data paths)
- [ ] Project ID (`lumi-allocations` / `groups`)
- [ ] Partition (see [references/partitions.md](references/partitions.md))
- [ ] Script with `--account`, resources; for containers use `-B` full paths
- [ ] Submit: `sbatch script.sh`
- [ ] Monitor: `squeue --me`, logs, `sacct`/`seff`
- [ ] Debug: [references/troubleshooting.md](references/troubleshooting.md)

## Monitoring and troubleshooting

For monitoring commands and common errors see [references/troubleshooting.md](references/troubleshooting.md).

## Quick reference

```bash
sbatch job.sh
squeue --me
squeue --me --start
scontrol show job <job_id>
scancel <job_id>
sinfo -p small-g
sacct --starttime=today
```

## Documentation links

LUMI and Slurm documentation: [references/resources.md](references/resources.md).
