---
name: frontier-slurm-job-management
description: Submit, monitor, and debug Slurm jobs on Frontier.
---

# Slurm on Frontier

Frontier uses Slurm. A compute node has 4 AMD MI250X accelerators, each with 2 GPU compute dies (GCDs), so **8 logical GPUs** with 64 GB HBM2E each, plus 512 GB host memory. By default 8 of the 64 CPU cores are reserved for the system, leaving **56 application cores**. Containers run with **Apptainer**.

## When to Use This Skill

- Submitting batch or interactive Frontier jobs (CPU or GPU)
- Choosing partitions/QOS and specifying a charge account
- Managing running or queued jobs and examining the queue
- Writing Apptainer, Python, MPI, array, or PyTorch jobs
- Debugging account, partition, binding, and GPU-visibility failures
- Checking GPU availability/utilization on an allocated compute node

## Project Account (Mandatory)

Every job must be charged to an OLCF project account. Find yours with `showusage` or on myOLCF; `id`/`groups` show Unix groups, which are not necessarily valid charge accounts. The project directory name can differ in case from the account, so keep `PROJECT_DIR` separate from `PROJECT`.

```bash
showusage
sbatch --account="$PROJECT" job.sh
```

Shell variables are not expanded in `#SBATCH` lines, so either write the account literally (`#SBATCH --account=abc123`) or pass it on the command line as the examples here do. `SBATCH_ACCOUNT` and `SALLOC_ACCOUNT` can set a default.

## Partitions

Use `batch` for production and `extended` for smaller long-running jobs. `debug` is a **QOS** on `batch`, not a partition. Development nodes need both `--partition=service --qos=develop`. Frontier has no sub-node partitions. See [references/partitions.md](references/partitions.md).

## Frontier and GPUs

- Whole nodes are allocated and charged. A one-GCD job is charged for the full node.
- Map one rank per GCD with `srun -N1 -n8 -c7 --gpus-per-task=1 --gpu-bind=closest`. Give the total task count explicitly; the guide lists a known issue with relying only on `--ntasks-per-node`.
- With this binding each rank sees one GPU as device **0**. Do not also set `ROCR_VISIBLE_DEVICES=$SLURM_LOCALID`; the closest mapping is not in local-rank order.
- 56 cores is the default; `-S 0` frees the reserved cores but OLCF does not recommend it.
- Set `MPICH_GPU_SUPPORT_ENABLED=1` for GPU-aware MPI (requires a build against Cray MPICH with GPU support).
- Run `rocm-smi` (e.g. `rocm-smi --showtoponuma`) inside a compute allocation; check `module avail rocm` for versions.

## Essential Commands

```bash
sbatch --account="$PROJECT" job.sh
squeue --me
squeue --me --start
scontrol show job <job_id>
scancel <job_id>
sinfo -s
sacct --starttime=today
```

`ST`: `PD` = pending, `R` = running. Last column = reason (e.g. `Priority`, `Resources`, dependency, QOS limit).

## Batch Script Structure

1. Shebang (`#!/bin/bash`), then all `#SBATCH` directives before any command
2. Directives: name, partition (and QOS), walltime, nodes, tasks, CPUs per task, output
3. Environment (`module load`, exports), `cd` to an Orion work directory
4. `srun` to run the application

**Time:** `hh:mm:ss` or `d-hh:mm:ss`, within the partition and node-count limits. **Requeue:** `--no-requeue` to disable; `--open-mode=append` to keep earlier output; `SLURM_RESTART_COUNT` in the script. **Email:** supported via `--mail-user` and `--mail-type=END,FAIL`.

### Minimal batch example

```bash
#!/bin/bash
#SBATCH --job-name=frontier-smoke
#SBATCH --partition=batch
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=00:05:00
#SBATCH --output=%x-%j.out
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?}"
srun -N1 -n1 hostname
```

Submit with `sbatch --account="$PROJECT" job.sh`. For full-node MPI, single-GCD, container, Python, array, and PyTorch examples see [references/examples.md](references/examples.md).

## Containers

Apptainer is available (check `apptainer --version`). Bind Orion work paths with `--bind`:

```bash
srun -N1 -n1 apptainer exec --bind "$WORKDIR:$WORKDIR" "$IMAGE" /bin/hostname
```

For GPU and MPI inside containers, OLCF provides the experimental modules `olcf-container-tools`, `apptainer-enable-mpi`, and `apptainer-enable-gpu`. Load them when running containers, not for `apptainer build`. The image's MPI must be ABI-compatible with the host Cray MPICH; see the [container guide](https://docs.olcf.ornl.gov/software/containers_on_frontier.html). Do not use `--nv` (NVIDIA).

## Storage, Interactive, Dependencies

- **Storage:** run jobs from `/lustre/orion/PROJECT_DIR/scratch/USER` or `/lustre/orion/PROJECT_DIR/proj-shared` (not backed up, 90-day purge). `/ccs/home` and `/ccs/proj` are NFS; avoid heavy job I/O there. See the frontier-resource-management skill.
- **Interactive:** `salloc --account="$PROJECT" --partition=service --qos=develop --nodes=1 --time=00:15:00`, then `srun`. Do not append `/bin/bash` to `salloc`; the guide warns the shell may then stay on the login node.
- **Attach to a running job:** `srun --jobid=<job_id> --overlap -N1 -n1 --pty /bin/bash` (shares the job's resources).
- **Dependencies:** `sbatch --account="$PROJECT" --dependency=afterok:123456 job2.sh` (types: `after`, `afterany`, `afterok`, `afternotok`). Job chaining is not allowed in the debug QOS.

## Best Practices

1. Always pass `--account` explicitly.
2. Use whole nodes efficiently; pack small independent tasks into one allocation.
3. Keep the default core specialization and one thread per core for the 8 ranks × 7 cores layout.
4. Let `--gpu-bind=closest` handle GPU mapping.
5. Load the same module stack used to build the application; do not mix CPE, ROCm, and container MPI versions arbitrarily.
6. Set a realistic walltime, checkpoint, and copy results off scratch before the purge.
7. Test on one node before scaling; never run GPU work on login nodes.

## Slurm job workflow

- [ ] Requirements (CPU/GCD, nodes, walltime, data paths)
- [ ] Charge account (`showusage`, myOLCF)
- [ ] Partition/QOS (see [references/partitions.md](references/partitions.md))
- [ ] Script with resources; output directories exist before submission
- [ ] Submit: `sbatch --account="$PROJECT" script.sh`
- [ ] Monitor: `squeue --me`, logs, `sacct` (job and step state/exit codes)
- [ ] Debug: [references/troubleshooting.md](references/troubleshooting.md)

## Monitoring and troubleshooting

For monitoring commands and common errors see [references/troubleshooting.md](references/troubleshooting.md).

## Quick reference

```bash
squeue --me
squeue --me --start
sinfo -p batch
scontrol show partition batch
sacct --starttime=today --format=JobID,JobName,State,ExitCode,Elapsed,AllocNodes
```

## Validation

Run `python3 scripts/validate.py` to check links, frontmatter, and bash syntax in both Frontier skills. [templates/gpu-smoke.sbatch](templates/gpu-smoke.sbatch) is a one-node, eight-GCD GPU smoke test (see [references/examples.md](references/examples.md)).

## Documentation links

Frontier, AMD MI250X, and Slurm documentation: [references/resources.md](references/resources.md).
