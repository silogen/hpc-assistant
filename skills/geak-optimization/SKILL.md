---
name: geak-optimization
description: Install and run GEAK (GPU Energy-Aware Kernel optimizer) on HPC clusters with AMD Instinct GPUs. Covers kernel_workflow (single kernel) and e2e_workflow (whole-model serving). Use only when the user explicitly asks for GEAK, kernel_workflow, e2e_workflow, or to submit a kernel or model to the GEAK optimizer. Do not use for ordinary rocprof, HIP, or Triton performance advice.
---

# GEAK on HPC clusters

GEAK is an autonomous GPU kernel optimizer for AMD Instinct MI GPUs (CDNA — gfx942 / gfx950). It is a
multi-agent system driven by Claude Code that runs the full optimization loop: profile → analyze →
generate improved kernels (Triton, HIP, FlyDSL, CK) → verify speedup → repeat.

It ships two workflows:

| Workflow | Scope | Use when |
|---|---|---|
| `kernel_workflow` | Single kernel | Optimizing one Triton / HIP / CK / FlyDSL kernel |
| `e2e_workflow` | Whole-model serving | Raising sglang / vLLM throughput end-to-end |

## Prerequisites

- AMD Instinct MI GPU (CDNA — MI250x gfx942, MI300X gfx942, MI350X gfx950, etc.)
- ROCm 6+
- A profiler: `rocprofv3` / `rocprof-compute` / `rocprof`
- Python 3.8+
- An Anthropic API key (or compatible gateway)
- For `e2e_workflow`: a running sglang or vLLM backend and model weights on disk

GEAK does **not** install your kernel's toolchain (PyTorch, Triton, HIP compiler, hipBLASLt). Set up
and verify baseline builds before starting a workflow.

## Installation

### A. One-liner (recommended)

```bash
pip install "git+https://github.com/AMD-AGI/GEAK"
```

This installs the `geak` Python package, clones the repo to `./GEAK/`, and installs the Claude Code CLI.

### B. Clone first

```bash
git clone https://github.com/AMD-AGI/GEAK.git
cd GEAK
pip install .
```

After installation `claude` should be on your PATH (check `which claude`). If not, add `~/.local/bin`
to your PATH.

### Installing on HPC (shared filesystem)

On systems with Lustre or similar parallel filesystems, a plain `pip install` creates thousands of
small files and may exhaust your inode quota. Use the site's recommended container-wrapper or
virtual-environment method that bakes packages into a SquashFS image. See the site-specific
reference for your cluster.

## API credentials

Set one of the following before launching GEAK:

```bash
# Option 1: Anthropic API directly
export ANTHROPIC_API_KEY=sk-ant-...

# Option 2: Standard gateway (x-api-key / bearer)
export ANTHROPIC_BASE_URL=https://your-gateway
export ANTHROPIC_AUTH_TOKEN=your-token

# Option 3: Custom header gateway
export ANTHROPIC_BASE_URL=https://your-gateway
export ANTHROPIC_CUSTOM_HEADERS="Your-Header-Name: <your-key>"
```

## Preparing a kernel task directory

For `kernel_workflow`, point GEAK at a directory containing your kernel source. The minimum
requirement is the **kernel source file(s)** themselves. GEAK's `benchmark_engineer` agent will:

1. Discover any existing test harness (`test_*.py`, `config.yaml`, `scripts/task_runner.py`)
2. If none exists, **author `test_harness.py` itself** — correctness check + timing harness
3. Establish the baseline benchmark

You do **not** need to provide a test file or config. Optionally, a `config.yaml` with explicit
`compile_command`, `correctness_command`, and `performance_command` entries saves GEAK some setup
budget. Do not provide one if you want GEAK to discover everything autonomously.

### Example: Triton GEMM kernel

Minimum task directory:
```
matmul_task/
└── matmul.py   ← your Triton kernel source, nothing else needed
```

Kernels with obvious sub-optimalities give GEAK's specialist engineers more to work with:
- Fixed, untuned block sizes (no `tl.autotune`)
- No split-K or persistent/stream-K
- No `eviction_policy` hints on loads
- Wrapper with no layout or transposition checks

## Launching GEAK

GEAK runs **interactively inside a Claude Code session**. It spawns many sub-agents and runs
profiling and build commands on the box, so launch with permissions auto-approved:

```bash
IS_SANDBOX=1 claude --dangerously-skip-permissions
```

Then describe what you want in natural language:

```
use /abs/path/to/GEAK/kernel_workflow to optimize /abs/path/to/matmul_task
```

```
use /abs/path/to/GEAK/kernel_workflow to optimize /abs/path/to/my_kernel, budget 8, focus on memory layout
```

```
use /abs/path/to/GEAK/e2e_workflow to optimize inference for /models/Qwen3-27B, sglang, ISL/OSL=1024, conc=64, gpus 0,1,2,3
```

Or launch non-interactively with `-p`:

```bash
IS_SANDBOX=1 claude --dangerously-skip-permissions -p \
  'use /abs/path/to/GEAK/kernel_workflow to optimize /abs/path/to/matmul_task'
```

### Key `kernel_workflow` arguments

| Argument | Default | Notes |
|---|---|---|
| `kernel_path` | (required) | Absolute path to the kernel task directory |
| `workflow_dir` | (required) | Same as `scriptPath` directory |
| `budget` | 6 | Total optimization directions across all rounds |
| `gpu_ids` | `"0"` | Comma-separated GPU IDs to use |
| `task` | — | Optional natural-language steering hint |
| `mode` | `"optimize"` | `optimize` \| `author` \| `bakeoff` |
| `min_improve` | 0.02 | Min verified geomean gain to commit a round winner |

## Running GEAK in a batch job

On HPC clusters GEAK should run in a batch job on a compute node with GPU access. The job needs:

1. A GPU partition / queue
2. The GEAK environment (Python + `claude` binary) accessible from the compute node
3. The Anthropic API key exported in the job environment
4. The GEAK repo and kernel task directory on a shared filesystem

**General job script template:**

```bash
#!/bin/bash
# scheduler directives here — see your cluster's documentation
# e.g. for Slurm:
#SBATCH --partition=<gpu-partition>
#SBATCH --account=<your-account>
#SBATCH --nodes=1
#SBATCH --gpus=1
#SBATCH --time=02:00:00

export ANTHROPIC_API_KEY=<your-key>

GEAK_WF=/path/to/GEAK/kernel_workflow
KERNEL_PATH=/path/to/matmul_task
RUN_DIR=/scratch/your-project/geak-runs/$SLURM_JOB_ID   # or equivalent
mkdir -p "$RUN_DIR"
cd "$RUN_DIR"

IS_SANDBOX=1 claude --dangerously-skip-permissions -p \
  "use ${GEAK_WF} to optimize ${KERNEL_PATH}"
```

For container-based clusters, wrap the `claude` call in `singularity run` / `apptainer run` and
bind-mount the shared filesystem paths. See the site-specific reference for your cluster.

## Understanding the optimization loop

```
Setup → Analyze + Roadmap → Benchmark (baseline) → Profile →
LOOP[ Plan round → (Optimize ‖ Verify, pipelined) → Integrate → Commit winner → Re-profile → Update memory ] →
Final Report → Director Validation
```

- **Budget** = total optimization directions dispatched to engineers across all rounds. Only
  optimization engineers count; benchmark / profile / verify / integrate do not consume budget.
- **Specialist engineers** run in parallel per round: `algorithm`, `memory`, `compute`,
  `host_runtime`, and optionally `deep_explore`.
- Every patch is **independently verified** by a separate `verify_engineer` before being committed.
- **Cross-round memory**: an insight blackboard threads what was learned into the next round.
- Speedup is always `geomean(baseline_ms / optimized_ms)` vs the true original baseline.

## Output

`kernel_workflow` output lands under `<exp_root>/team_<kernel>_<timestamp>/<kernel>/`:

| Artifact | Contents |
|---|---|
| `COMMANDMENT.md` | Immutable benchmark contract (correctness + timing commands) |
| `baseline_timing.json` | Per-case baseline latencies |
| `profiling_summary.md` | Profiler output and bottleneck analysis |
| `round_N/engineer_i/` | Each engineer's patch, report, and timing |
| `tech_lead_report.md` | Round-by-round narrative + final per-case speedup table |
| `final_patch.diff` | The verified winning patch |
| `optimized/` | Full optimized source tree |
| `director_validation.json` | Independent validation verdict |

## Modes

### `mode=optimize` (default)
Optimize an existing kernel source in-place. The original is never modified unless
`apply_to_original="true"` is passed.

### `mode=author`
No existing source — GEAK writes a fresh implementation in `target_language`
(`triton` | `flydsl` | `hip` | `ck`), then optimizes it. Requires an op task dir with
`meta.json` + `unittest.py`.

### `mode=bakeoff`
Races multiple backend languages against the same frozen baseline. Pass `backends` (e.g.
`["hip","triton","flydsl"]`). The incumbent language always competes as an `optimize` lane.
Useful for finding the fastest language for a given op shape.

## Site-specific references

- **LUMI**: [references/lumi.md](references/lumi.md)

## Resources

- [GEAK repository](https://github.com/AMD-AGI/GEAK)
- [GEAK README](https://github.com/AMD-AGI/GEAK/blob/main/README.md)
- [kernel_workflow README](https://github.com/AMD-AGI/GEAK/blob/main/kernel_workflow/README.md)
- [AMD ROCm docs](https://rocm.docs.amd.com)
