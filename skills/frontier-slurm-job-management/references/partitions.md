# Frontier Partitions and QOS

Limits below follow the [Frontier scheduling policy](https://docs.olcf.ornl.gov/systems/frontier_user_guide.html#scheduling-policy); policies change, so check the guide and the live configuration:

```bash
sinfo -s
scontrol show partition batch
```

## Partitions (all allocate whole nodes)

| Partition | Max walltime | Max nodes/job | Purpose |
| --- | --- | --- | --- |
| `batch` | 2–12 hours, by node count (below) | 9,472 | Default production queue |
| `extended` | 24 hours | 64 | Smaller, long-running jobs |
| `service` + `--qos=develop` | 2 hours | 4 | Isolated development nodes |

### `batch` limits by node count

| Nodes | Max walltime | Aging boost |
| --- | --- | --- |
| 1–91 | 2 hours | 0 days |
| 92–183 | 6 hours | 0 days |
| 184–1,881 | 12 hours | 0 days |
| 1,882–5,644 | 12 hours | 4 days |
| 5,645–9,472 | 12 hours | 8 days |

`batch` allows four eligible-to-run jobs per user; further jobs are held. `extended` allows one running and one eligible-to-run job per user. Across all partitions a user may have at most 100 jobs queued in any state.

## Debug and development

- **`--partition=batch --qos=debug`:** short non-production debugging, max 2 hours, one job per user. Production runs and job chaining are not allowed.
- **`--partition=service --qos=develop`:** max 4 nodes and 2 hours; one running and one eligible-to-run job per user. Both options are required.

## Small jobs

Charging is requested nodes × elapsed time, so a single-GCD or serial job pays for a whole node. Frontier has no sub-node, large-memory, or visualization partitions. For many small tasks, run several `srun` steps or Flux inside one allocation (see **Ensemble Jobs** in the guide), with non-overlapping CPU/GPU placement.

## Notes

- Backfill and size-based aging affect priority; `squeue --start` is an estimate.
- Reservations and exceptions require OLCF approval (`help@olcf.ornl.gov`).
- Allocation overuse lowers priority: 30 days at 100–125% usage and 365 days above 125%, with additional concurrency and walltime restrictions. Check `showusage` before launching more work.
