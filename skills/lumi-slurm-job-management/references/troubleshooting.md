# LUMI Slurm: Monitoring and Troubleshooting

## Monitoring commands

```bash
scontrol show job <job_id>
watch -n 5 squeue --me
squeue -j <job_id> --start
tail -f job_12345.out
sacct -j <job_id> --format=JobID,JobName,Elapsed,CPUTime,MaxRSS,State
seff <job_id>   # if available
```

On LUMI-G, use `rocm-smi` (e.g. `rocm-smi --showtoponuma`) for GPU utilization.

## Common issues and solutions

| Issue | Cause | Solution |
| ----- | ----- | -------- |
| Job violates accounting/QOS policy | Missing/invalid `--account` or over limits | Set `--account=project_xxx`; check `lumi-allocations` and job/queue limits |
| Invalid account or account/partition combination | Wrong project or partition | Verify project with `groups`/`lumi-allocations`; use valid partition name |
| Job never runs on LUMI-G (64 cores) | Only 56 cores/node available | Request at most 56 cores per node |
| Container cannot see data | Paths not bound or wrong path | Use `-B /scratch/project_xxx:/scratch/project_xxx` (full path) |
| GPU not visible in job | No GPU binding / wrong env | Use `ROCR_VISIBLE_DEVICES=$SLURM_LOCALID` (e.g. via select_gpu wrapper) |
| Out of memory | Too low `--mem` or /tmp usage | Increase `--mem`; note `/tmp` on compute nodes counts against job memory |
| Killed (OOM) | Memory exceeded | Reduce batch size or increase `--mem` |
