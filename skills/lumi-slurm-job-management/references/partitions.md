# LUMI Partitions

Use `sinfo` or `sinfo -s` to list partitions; `scontrol show partition <name>` for details.

## Full-node (allocatable by node)

Jobs get whole nodes and are billed for the full node regardless of resources used.

| Partition     | Max walltime | Max jobs (running) | Max resources/job | Hardware |
| ------------- | ------------ | ------------------ | ----------------- | -------- |
| `standard-g`  | 2 days       | 210 (200)          | 1024 nodes        | LUMI-G   |
| `standard`    | 2 days       | 120 (100)          | 512 nodes         | LUMI-C   |

## Sub-node (allocatable by resources)

| Partition | Max walltime | Max jobs (running) | Max resources/job | Hardware | Purpose |
| --------- | ------------ | ------------------ | ----------------- | -------- | ------- |
| `small-g` | 3 days       | 210 (200)          | 4 nodes           | LUMI-G   | Small GPU jobs |
| `small`   | 3 days       | 220 (200)          | 4 nodes           | LUMI-C   | Small or memory-intensive jobs |
| `debug`   | 30 minutes   | 2 (2)              | 4 nodes           | LUMI-C   | Debugging, testing |

### `dev-g` (GPU debugging)

Max walltime depends on the number of nodes requested: at most 16 node-hours per job, and at most 3 hours.

| Max walltime | Max jobs (running) | Max resources/job |
| ------------ | ------------------ | ----------------- |
| 30 minutes   | 1 (1)              | 32 nodes          |
| 1 hour       | 1 (1)              | 16 nodes          |
| 2 hours      | 1 (1)              | 8 nodes           |

Only **one `dev-g` job per user and one per project** at a time (running or queued); both limits apply together. If another member of your project already has a `dev-g` job, you cannot submit one until theirs finishes.

## LUMI-D (data analytics and visualization)

**`lumid` is for visualization only.** Its Nvidia GPUs are for graphical acceleration (e.g. ParaView), not for compute; run GPU compute on LUMI-G (AMD). Repeated abuse may result in account suspension or project termination.

| Partition  | Allocation   | Max walltime | Max jobs (running) | Max resources/job | Purpose |
| ---------- | ------------ | ------------ | ------------------ | ----------------- | ------- |
| `largemem` | By node      | 2 days       | 30 (20)            | 1 node            | Memory-intensive pre/post-processing (4 TB/node) |
| `lumid`    | By resources | 12 hours     | 16 (8)             | 8 GPUs (total)    | Visualization |

## Notes

- **Debug partitions** (`debug`, `dev-g`): for quick testing; abuse may lead to account suspension.
- **Small partitions** (`small`, `small-g`): for jobs that do not need a full node (e.g. single-GPU or serial pre/post-processing).
- **Large memory:** request more memory than standard LUMI-C nodes on `small` for 512GB/1TB nodes; use `largemem` for 4 TB nodes (LUMI-D).
