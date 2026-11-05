# Frontier Slurm: Monitoring and Troubleshooting

Check the **Known Issues** section of the [Frontier guide](https://docs.olcf.ornl.gov/systems/frontier_user_guide.html#known-issues) for release-specific problems.

## Monitoring commands

```bash
scontrol show job "$JOB_ID"
squeue -j "$JOB_ID" --start
tail -f job_12345.out
sacct -j "$JOB_ID" --format=JobID,JobName,Elapsed,AllocNodes,CPUTime,MaxRSS,State,ExitCode
seff "$JOB_ID"   # if available; CPU/memory only, not GPU
```

Poll the scheduler sparingly (e.g. every 30–60 seconds). `sacct` can lag; check step rows for failures and `MaxRSS` (per-task host memory, not GPU memory).

Inside a compute allocation, `rocm-smi` (e.g. `rocm-smi --showtoponuma`) reports GPU utilization and topology. The known-issues list may document problems with `amd-smi` on some ROCm versions.

## Common issues and solutions

| Issue | Cause | Solution |
| --- | --- | --- |
| Job violates accounting/QOS policy | Invalid account, time/size limit, job cap, or allocation overuse | Read the exact error; check `showusage` and [partitions.md](partitions.md) |
| Invalid account/partition combination | Unix group or directory name used as account; nonexistent partition | Verify account on myOLCF; use `batch`, `extended`, or `service` + `--qos=develop` |
| Pending `Priority` / `Resources` | Queue priority, backfill, node-count bins | Use a realistic walltime; start estimates can change |
| Pending dependency | Predecessor failed or dependency is invalid | Check predecessor's final state |
| Too many queued jobs | Per-user or QOS job limits | Wait for jobs to finish instead of resubmitting |
| 64 cores/node fails or maps badly | Only 56 application cores by default | Use 8 ranks × 7 cores |
| Wrong rank count | Relying only on `--ntasks-per-node` at allocation | Set `--ntasks` and `srun -n` explicitly (known issue OLCFDEV-1809) |
| Wrong binding with odd cores/task | `--threads-per-core=2` | Keep `--threads-per-core=1` (known issue OLCFDEV-1837) |
| GPU missing or wrong device | Conflicting visibility variables, non-ROCm build | Use `--gpus-per-task=1 --gpu-bind=closest`; each rank uses device 0; check `torch.version.hip` |
| Container cannot see data | Missing bind or wrong path | Bind the absolute Orion path; check it exists inside the container |
| Container MPI hangs or libfabric/GTL errors | Image MPI not ABI-compatible with host Cray MPICH, ROCm/CPE mismatch | Follow the current OLCF container recipe; test one node, then two |
| Container build mount-hook error | `apptainer-enable-*` modules loaded during build | Unload them for `apptainer build`; load them only to run |
| Python environment resets to base | `module load miniforge3` reactivates base | Activate the environment inside the batch script after loading the module |
| `OUT_OF_MEMORY` (host) | Host memory exhausted | Reduce memory per rank or use more nodes; nodes have 512 GB |
| HIP/PyTorch out of memory | GCD HBM exhausted (64 GB per GCD) | Reduce batch/model size per rank; `--mem` does not affect HBM |
| Disk quota exceeded | Home or project quota (bytes or inodes) | Check `quota -Qs` and Project Home; see frontier-resource-management |
| Timeout or requeue | Walltime exceeded, node failure | Check `State`, `ExitCode`, `SLURM_RESTART_COUNT`; restart from checkpoints; use `--open-mode=append` |
| No output file | Output directory did not exist at start | Create log directories before `sbatch` |

`sbatch` succeeding only means the job was accepted. Confirm completion with the final job and step state in `sacct` (`COMPLETED`, exit code `0:0`) and the application's own output.
