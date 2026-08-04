---
name: profiling
description: Profiles HIP/ROCm applications on AMD GPUs using rocprofv3. Covers application tracing (HIP, HSA, kernel, memory), kernel counter collection (--pmc), and Perfetto visualization. Use when the user asks about profiling AMD GPUs, rocprofv3, ROCm profiling, kernel tracing, GPU performance counters, profiling a Python GPU script, Singularity, Apptainer, SLURM job profiling, or LUMI.
---

# ROCm profiling with rocprofv3

`rocprofv3` is a CLI tool for profiling and tracing HIP/ROCm applications on AMD GPUs without source changes. It supports application tracing (API and kernel execution) and kernel counter collection. It is backward compatible with `rocprof` and is installed with ROCm (typically under `/opt/rocm/bin`).

## When to Use This Skill

- Profiling HIP or ROCm applications on AMD GPUs (e.g. LUMI-G MI250x)
- Tracing API calls, kernel dispatches, or memory operations
- Collecting GPU performance counters (PMCs) for specific kernels
- Generating Perfetto or CSV traces for analysis
- Attaching to a running process to profile it dynamically
- Profiling GPU workloads run via Python, Singularity/Apptainer, or SLURM (e.g. on LUMI)

## Example patterns

See [references/examples.md](references/examples.md) for `rocprofv3` usage patterns (runtime/kernel trace, performance counters, MPI, and SLURM).

## Basic usage

General form:

```bash
rocprofv3 [options] -- <application_path> [application_args]
```

Options come before `--`; everything after `--` is the application and its arguments.

**Help and version:**

```bash
rocprofv3 -h
rocprofv3 --version
```

## Application tracing

Tracing records execution times of API calls and GPU activities (kernels, memory copies). Use one or more trace options before `--`.

**Common trace options:**

| Option | What is traced |
| ------ | ----------------- |
| `--hip-trace` | HIP API only (runtime + compiler); does not enable kernel or memory tracing |
| `--kernel-trace` | Kernel dispatches |
| `--memory-copy-trace` | Memory copies (e.g. hipMemcpy) |
| `--memory-allocation-trace` | Allocations/frees |
| `--marker-trace` | ROCTx/roctx markers |
| `--runtime-trace` | HIP runtime, markers, kernels, memory (no HSA, no HIP compiler API) |
| `--sys-trace` | All of the above plus HIP compiler API and full HSA API |

**Examples:**

```bash
# HIP API trace only (CSV output)
rocprofv3 --hip-trace --output-format csv -- ./myapp

# Kernel and memory copy trace
rocprofv3 --kernel-trace --memory-copy-trace --output-format csv -- ./myapp

# Shorthand: runtime trace (typical first-step profiling)
rocprofv3 --runtime-trace --output-format csv -- ./myapp

# Full system trace (HIP + HSA + kernels + memory)
rocprofv3 --sys-trace --output-format csv -- ./myapp
```

Default output format is `rocpd` (SQLite). Other formats: `csv`, `json`, `pftrace` (Perfetto), `otf2`.

**Post-processing:** Use `--stats` for per-API statistics; use `-S` or `--summary` to print a summary after the run. Combine with a trace option, e.g. `rocprofv3 --stats --hip-trace --output-format csv -- ./myapp`.

## Kernel counter collection

Collect hardware performance counters for kernel executions. First list available counters for your GPU:

```bash
rocprofv3 --list-avail
```

Then request counters by name. Counters are GPU-specific; requesting too many at once may require multi-pass (multiple runs).

**Command line:**

```bash
rocprofv3 --pmc SQ_WAVES GRBM_COUNT GRBM_GUI_ACTIVE --output-format csv -- ./myapp
```

**Input file (YAML/JSON):** Use `-i input.yaml` to specify counters and other options. Text input (e.g. `pmc: SQ_WAVES` per line) can define multiple `pmc` rows for multi-pass collection.

**Extra counters:** Define custom counters in a YAML file and pass it with `-E` / `--extra-counters`, then reference them in `--pmc`.

## I/O options

| Option | Purpose |
| ------ | ------- |
| `-i FILE` / `--input FILE` | Input config (YAML/JSON for full config; text for PMC lists) |
| `-o FILE` / `--output-file FILE` | Output file name |
| `-d DIR` / `--output-directory DIR` | Output directory (default uses `%hostname%/%pid%`) |
| `--output-format csv\|json\|pftrace\|otf2\|rocpd` | Output format (default: rocpd) |

Output names can use placeholders such as `%pid%`, `%hostname%`, `%job%` (SLURM_JOB_ID), `%rank%` (MPI/SLURM rank).

## Process attachment

Profile an already running process by PID without restarting it:

```bash
rocprofv3 -p <PID> --hip-trace --output-format csv -- <application_path>
# or
rocprofv3 --attach <PID> --kernel-trace -- ./myapp
```

Data is collected for the configured duration (see collection period in docs).

## Perfetto visualization

Generate a trace viewable in Perfetto (e.g. [ui.perfetto.dev](https://ui.perfetto.dev)):

```bash
rocprofv3 --sys-trace --output-format pftrace -- ./myapp
```

For counter data over time:

```bash
rocprofv3 --pmc SQ_WAVES GRBM_COUNT --output-format pftrace -- ./myapp
```

## Disabling specific traces

With aggregate options like `--runtime-trace` or `--sys-trace`, turn off individual trace types by setting them to `False`:

```bash
rocprofv3 --runtime-trace --scratch-memory-trace=False -- ./myapp
rocprofv3 --sys-trace --hsa-trace=False -- ./myapp
```

## Configuration via input file

YAML or JSON input can define one or more jobs with tracing and counter options. Example (YAML):

```yaml
jobs:
  - runtime_trace: true
    kernel_rename: true
    summary: true
    summary_per_domain: true
    output_format: [pftrace, json]
    output_directory: "out"
```

Run with:

```bash
rocprofv3 -i input.yaml -- ./myapp
```

## Summary of common workflows

1. **First look (runtime trace + summary):**  
   `rocprofv3 --runtime-trace -S --output-format csv -- ./myapp`
2. **Kernel timing only:**  
   `rocprofv3 --kernel-trace --output-format csv -- ./myapp`
3. **Kernel counters:**  
   `rocprofv3 --list-avail` then `rocprofv3 --pmc <counters> --output-format csv -- ./myapp`
4. **Perfetto timeline:**  
   `rocprofv3 --sys-trace --output-format pftrace -- ./myapp`
5. **Attach to running process:**  
   `rocprofv3 -p <pid> --kernel-trace -- ./myapp`

## Scenario examples

Short patterns for using rocprofv3 with Python, containers, and SLURM on LUMI-style systems. Full runnable examples: [references/examples.md](references/examples.md).

### Python script

When the GPU workload runs in a Python process that uses HIP/ROCm (e.g. PyTorch with ROCm). The executable is `python`; script and args go after `--`. The environment must provide a ROCm/HIP-backed GPU stack for kernels to appear.

```bash
rocprofv3 --runtime-trace -S --output-format csv -d ./prof_out -- python script.py
```

### Singularity/Apptainer container

Run rocprofv3 **inside** the container (image must include ROCm and rocprofv3). Bind project/scratch with full paths.

```bash
singularity exec -B /scratch/project_xxx:/scratch/project_xxx -B /project/project_xxx:/project/project_xxx image.sif rocprofv3 --runtime-trace --output-format csv -d ./prof_container -- mycommand
```

### SLURM job

When profiling inside a batch or interactive job. Use `-d` and `-o` with `%j` so output is per job; set `--account` and partition (e.g. `small-g` for GPU) as in the Slurm skill.

```bash
srun rocprofv3 --runtime-trace -S --output-format csv -d ./prof_%j -o prof_%j -- ./my_gpu_app
```

## Resources

- [Using rocprofv3 (AMD ROCm)](https://rocm.docs.amd.com/projects/rocprofiler-sdk/en/latest/how-to/using-rocprofv3.html) – Full option reference, tracing types, counter collection, input schema, and advanced options (collection period, signal handlers, ROCm root, agent index, kernel filtering, etc.).
