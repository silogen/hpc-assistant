# LUMI Partitions

Use `sinfo` or `sinfo -s` to list partitions; `scontrol show partition <name>` for details.

## Full-node (allocatable by node)

| Partition     | Max walltime | Max resources/job | Hardware        |
| ------------- | ------------ | ----------------- | --------------- |
| `standard-g`  | 2 days       | 1024 nodes        | LUMI-G          |
| `standard`    | 2 days       | 512 nodes         | LUMI-C          |
| `bench`       | 6 hours      | All nodes         | Special request |

## Sub-node (allocatable by resources)

| Partition | Max walltime | Max resources/job | Hardware | Purpose |
| --------- | ------------ | ----------------- | -------- | ------- |
| `small-g` | 3 days | 4 nodes | LUMI-G | Small GPU jobs |
| `small` | 3 days | 4 nodes | LUMI-C | Small or large-memory jobs |
| `debug` | 30 minutes | 4 nodes | LUMI-C | Debugging, testing |
| `dev-g` | 1–3 hours | 8–32 nodes | LUMI-G | Debugging GPU (max 16 node-hours) |
| `largemem` | 1 day | 1 node | LUMI-D | Memory-intensive (4 TB/node) |
| `lumid` | 4 hours | 1 GPU | LUMI-D | Visualization only (Nvidia for graphics, not compute) |

## Notes

- **Debug partitions** (`debug`, `dev-g`): for quick testing; abuse may lead to account suspension.
- **Small partitions** (`small`, `small-g`): for jobs that do not need a full node (e.g. single-GPU or serial pre/post-processing).
- **Large memory:** request more memory than standard LUMI-C nodes on `small` for 512GB/1TB nodes; use `largemem` for 4 TB nodes (LUMI-D).
