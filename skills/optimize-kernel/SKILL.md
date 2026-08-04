---
name: optimize-kernel
description: Submit a GPU kernel for optimization using GEAK via SLURM. Use when the user wants to optimize a Triton or HIP kernel for AMD GPUs.
allowed-tools: Bash(*/scripts/submit-geak-job.sh *), Read, Write, Grep, Glob
---

# Optimize Kernel

Submit a GPU kernel for GEAK optimization.

## Usage

From the project root directory, run:

```bash
./{.claude,.opencode}/skills/optimize-kernel/scripts/submit-geak-job.sh matmul_kernel.py
```

The exact path depends on the active framework. Claude Code installs skills to
`.claude/skills/`; OpenCode to `.opencode/skills/`. Use whichever exists:

```bash
# Claude Code
./.claude/skills/optimize-kernel/scripts/submit-geak-job.sh matmul_kernel.py

# OpenCode
./.opencode/skills/optimize-kernel/scripts/submit-geak-job.sh matmul_kernel.py
```

The full signature is:

```bash
<skills-dir>/optimize-kernel/scripts/submit-geak-job.sh \
  <kernel-file> [gpu_ids] [task]
```

- `kernel-file`: path to the kernel source (`.py`).
- `gpu_ids`: comma-separated GPU IDs (default `0`).
- `task`: free-text task description for GEAK (default `"Optimize this kernel for performance."`). Pass the user's actual ask through verbatim when they have one -- it shapes GEAK's optimization strategy. Example:

```bash
<skills-dir>/optimize-kernel/scripts/submit-geak-job.sh matmul_kernel.py 0 \
  "Optimize this Triton matmul for MI300X memory bandwidth."
```

If the user provides inline code, first write it to a temporary file (e.g. `kernel.py`) in the project root, then pass that file to the script.

## Output

The script outputs JSON to stdout:
- `ok`: true/false
- `id`: job ID for tracking (timestamp-based, e.g. `20260318-110700-a3f2`)
- `slurm_job_id`: SLURM job ID
- `job_dir`: path to job output directory

Report the job ID and SLURM job ID to the user. They can check status with the `check-geak-job` skill.

## After Submission

After a successful submission, spawn the `geak-monitor` subagent **in the
background** with the job ID. This keeps the primary session free for the
user to continue chatting while the monitor polls.

**Claude Code** (native `background: true` — primary session stays free):
> Use the geak-monitor agent to monitor job <job-id>

**OpenCode >= 1.14.51** (`OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true` set
by `launch.sh` — spawn with `background=true` task metadata):
> Use the geak-monitor agent to monitor job <job-id> in the background

**OpenCode < 1.14.51** (background subagents not supported — primary session
blocks until the monitor completes; user cannot chat during monitoring):
> Use the geak-monitor agent to monitor job <job-id>

With a custom poll cadence:

> Use the geak-monitor agent to monitor job 20260318-142031-9d19 with poll cadence 30 seconds

The monitor uses a bounded `until`-loop that polls the job's status
every 60 seconds by default and exits as soon as the job leaves the
RUNNING state, so completion is reported within roughly one cadence
period.

A hook/plugin prevents duplicate monitors. If the spawn is blocked (Claude Code)
or a collision is logged (OpenCode), a monitor is already running — expected, not an error.

Tell the user the monitor's poll cadence (default 60 seconds, or whatever
cadence was requested) so they know how quickly to expect updates.

CRITICAL rules after the monitor is spawned:

1. Do NOT spawn additional monitors for the same job.
2. Do NOT use the check-geak-job skill to poll the job yourself.
3. IGNORE background task notifications from the monitor between polls.
   Do not react to intermediate status. Do not report status to the user.
4. Only check job status manually if the user EXPLICITLY asks you to.

## Important

- Do NOT modify or recreate the submit script
- If the script fails, report the error JSON as-is
- The script handles SLURM submission automatically
- SLURM jobs run inside the LUMI multitorch image and bootstrap GEAK in-job
  via `setup-geak.sh`. You do not need to configure a separate container image.
