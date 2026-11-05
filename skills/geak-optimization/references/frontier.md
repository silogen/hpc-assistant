# Running GEAK on Frontier (OLCF)

Frontier nodes have 4 AMD MI250X packages, i.e. 8 GCDs per node (gfx90a, CDNA2), and run
Slurm. GEAK runs **without a container**: Claude Code headless on a compute node, inside a
Python venv layered on your existing ROCm PyTorch + Triton environment. Compute nodes reach
the Anthropic API through the OLCF proxy (`proxy.ccs.ornl.gov:3128`).

Throughout, `$PROJECT` is your OLCF charge account (`showusage`) and `$PROJECT_DIR` the
project directory name under `/lustre/orion` (case may differ from the account).

Helper scripts live next to this file in [`scripts/frontier/`](../scripts/frontier/):

| Script | Where it runs | Purpose |
|---|---|---|
| `setup-geak.sh` | login node, once | Clone GEAK, build the venv, write `.geak-frontier.env` |
| `geak_job.py` | login node | `submit`, `status`, `watch`, `cancel`, `list` (JSON output) |
| `run-geak.sbatch` | compute node (submitted by `geak_job.py`) | Run `kernel_workflow` with Claude Code, record the result |

Below, `S=<skills-dir>/geak-optimization/scripts/frontier` (for example
`.claude/skills/geak-optimization/scripts/frontier` or `.opencode/skills/...`).

## Limits to tell the user first

- **MI250X is not a validated GEAK target.** GEAK validates MI300/MI350 (gfx942/gfx950) and
  treats gfx90a as "MI300". The job adds a CDNA2 hardware note to the prompt, but results
  are not validated: re-verify any speedup yourself on a fresh node, including edge cases
  the task harness may not cover, before using a patch.
- **No sandbox.** GEAK requires `--dangerously-skip-permissions`; the agent can run any
  command and edit any file the account can write. The task is copied into the run
  directory first, but that is not isolation.
- **Cost.** Every optimization direction spawns Claude sub-agents billed to the credential.
  Keep `--budget` small (default 2).
- **Profiling is off by default** (`GEAK_PROFILER_PRIORITY=none`, benchmark-only): on Frontier
  `rocprof-compute` rejects GEAK's profile names and `rocprofv3` aborts, then hangs, with
  pip/TheRock PyTorch builds (LLVM `spirv-expand-step`). Set `GEAK_PROFILER_PRIORITY=rocprofv3`
  in the environment at submission to retry.

## Filesystem layout

| Path | Purpose |
|---|---|
| `/lustre/orion/$PROJECT_DIR/scratch/$USER/<project>` | Project directory: submit from here; `geak-output/` lands here |
| `<project>/../geak-frontier/` (default `GEAK_FRONTIER_HOME`) | GEAK checkout (`deps/GEAK`) and venv |
| `$HOME` | Claude Code install (`~/.local/bin/claude`) only; NFS, small quota |

Orion scratch is purged after 90 days; copy results you keep elsewhere.

## One-time setup (login node)

1. **Claude Code CLI** (>= 2.1.177) on the login node: `claude --version`.
2. **A ROCm PyTorch + Triton environment** that runs your kernel (for example the one from
   OLCF's "PyTorch on Frontier" guide). GEAK does not install your kernel's toolchain.
3. **Install GEAK** (clones `AMD-AGI/GEAK` `main`, installs `pyyaml requests datasets` and the
   pinned `metrix` into a venv with `--system-site-packages`, writes `.geak-frontier.env`):

   ```bash
   cd /lustre/orion/$PROJECT_DIR/scratch/$USER/myproject
   bash $S/setup-geak.sh "$PWD" /path/to/env/bin/python
   ```

   `GEAK_FRONTIER_HOME` changes the install location and `GEAK_REF` the GEAK branch or tag.
   Run it on a login node: compute nodes have no direct internet.
4. **Credential**, created by the user (never ask for, print or handle the key):

   ```bash
   umask 077; printf '%s\n' 'sk-ant-api...' > .anthropic_api_key   # or a `claude setup-token` OAuth token
   chmod 600 .anthropic_api_key
   ```

   Alternatively export `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` before submitting
   (`sbatch` exports the environment). An `sk-ant-api` key is used as an API key, anything
   else as an OAuth token; only one is passed to Claude Code.

## Preparing the task

Pass a **task directory** in GEAK format: the kernel source plus whatever runs it (test and
benchmark harness, `config.yaml`, inputs), like `<GEAK_HOME>/examples/tasks/*`. A single
file is accepted and copied into a directory, but GEAK then has to author its own harness
from the budget. See "Preparing a kernel task directory" in the main SKILL.md.

## Submitting

```bash
python3 $S/geak_job.py submit ./matmul_task --account "$PROJECT" --time 02:00:00 \
    --task "Optimize this Triton matmul for MI250X memory bandwidth." --budget 2
```

| Option | Default | Notes |
|---|---|---|
| `TASK` (positional) | — | Task directory or kernel file |
| `--task TEXT` | "Optimize this kernel for performance." | Pass the user's request verbatim |
| `--budget N` | 2 (`$GEAK_BUDGET`) | Optimization directions across all rounds |
| `--gpus N` | 1 | GCDs (1–8) given to the job; GEAK sees `gpu_ids 0..N-1` |
| `--options TEXT` | — | Extra `kernel_workflow` arguments appended to the prompt, e.g. `"mode bakeoff, backends [hip, triton]"`, `"min_improve 0.05"`, `"mode author, target_language hip"` |
| `--account`, `--time` | — / 02:00:00 | Slurm account and walltime |
| `--sbatch-args "..."` | `$GEAK_SBATCH_ARGS` | Other sbatch options, e.g. `"-q debug"` |
| `--project-dir DIR` | discovered | Before the subcommand; default: nearest ancestor with `.geak-frontier.env`, `.claude`, `.opencode` or `.hpc-assistant` |

Environment read by the job: `GEAK_MODEL` (Claude model for the workflow),
`GEAK_ROCM_MODULE` (default `rocm/7.13.0`), `GEAK_PROFILER_PRIORITY`, `GEAK_IDLE_EXIT_S`
(default 900), `CLAUDE_BIN`.

Output (JSON): `ok`, `id` (e.g. `20261105-142031-9d19`), `slurm_job_id`, `job_dir`, `run_dir`.
Report the `id` and `slurm_job_id` to the user.

## Monitoring and cancelling

```bash
python3 $S/geak_job.py status <id>                          # one check
python3 $S/geak_job.py watch  <id> --interval 60 --timeout 1200   # poll until it ends or 20 min pass
python3 $S/geak_job.py cancel <id>
python3 $S/geak_job.py list
```

States: `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`. A job that left Slurm
without writing `DONE`/`FAILED` (walltime kill, node failure) is reported as `FAILED` with
the Slurm state.

Monitoring rules for the agent:

- Run `watch` in the background when the framework supports it (Claude Code background
  Bash task or subagent; OpenCode >= 1.14.51 background subagent), so the user can keep
  working. Run one watcher per job; if `watch` returns a timeout, start it again.
- Do not poll with `status` in a tight loop yourself, and do not report intermediate
  states unless the user asks.
- After `cancel`, stop any watcher for that job.
- Do not edit or recreate the helper scripts; report their JSON errors as they are.
- Use `geak_job.py` instead of raw `sbatch`/`scancel` for GEAK jobs, so the job and run
  directories stay consistent.

## Experiment output

```
geak-output/jobs/<id>/   config.json, task copy, task.txt (prompt), slurm-<jobid>.out/.err,
                         claude-config/, DONE or FAILED
geak-output/runs/<id>/   task/ (working copy edited by GEAK), exp/ (kernel_workflow output:
                         final_report.md, final_patch.diff, optimized/, round_N/...),
                         claude-transcript.jsonl, final_report.json
```

`final_report.json` holds Claude's exit code, the path of GEAK's `final_report.md`, and the
Claude result record (turns, duration, cost in USD, final message). `status` on a completed
job returns it together with the `final_report.md` and `final_patch.diff` paths.

## What the job does

`run-geak.sbatch` (one node, `--gpu-counters=0`):

1. Loads `.geak-frontier.env`, checks the credential (file mode 600) and the Claude CLI.
2. Copies the task to `runs/<id>/task/` (the submitted copy stays untouched).
3. Builds the prompt: `kernel_workflow` path, task, `budget`, `gpu_ids`, `exp_root`, extra
   options, the user's text and the MI250X hardware note.
4. Loads the ROCm module (no `module purge`), activates the venv, sets the OLCF proxy,
   `IS_SANDBOX=1`, a job-private `CLAUDE_CONFIG_DIR`, and
   `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` (otherwise print mode kills GEAK's background
   workflow after 600 s and still exits 0).
5. Runs `claude -p ... --dangerously-skip-permissions --output-format stream-json` with
   `srun -n1 -c56 --gpus-per-task=<gpus> --gpu-bind=closest`.
6. Idle watchdog: headless Claude Code can stay alive after GEAK finishes; once a result
   record exists and the transcript has been silent for `GEAK_IDLE_EXIT_S` seconds, the job
   stops it and counts the run as finished.
7. Writes `final_report.json` and `DONE`, or `FAILED` with the reason. A zero exit code
   with an error result (for example a rejected credential) counts as a failure.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `.geak-frontier.env not found` | Setup not run for this project | Run `setup-geak.sh` from the project directory |
| `must be mode 600` | Credential file readable by others | `chmod 600 .anthropic_api_key` |
| `FAILED`: authentication / invalid key in the error | Wrong or expired credential | Replace the key or token |
| Job ends at once, connection errors in the transcript | Proxy not reachable | Check `proxy.ccs.ornl.gov:3128` from a compute node; see OLCF "PyTorch on Frontier" proxy settings |
| `FAILED ... without a DONE/FAILED marker`, state `TIMEOUT` | Walltime reached | Raise `--time` or lower `--budget` |
| GEAK report but no profiling data | Profiling disabled on purpose | Expected; see the limits above |
| `kernel_workflow not found` | Incomplete clone or wrong `GEAK_HOME` | Rerun `setup-geak.sh` |

## Testing the helpers

```bash
python3 -B -m unittest discover -s skills/geak-optimization/tests -v
```

The tests use fake `sbatch`/`squeue`/`sacct`/`scancel`; they need no Slurm, GPU or credential.
