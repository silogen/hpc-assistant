# GEAK Kernel Optimization on LUMI

GEAK integration for automated GPU kernel optimization via Claude Code on the
LUMI supercomputer.

## Scope

GEAK is supported on both **Claude Code** and **OpenCode**. The agent-agnostic
skills under `skills/` and the framework-neutral runtime files in this directory
are shared. Framework-specific assets live in:

```
geak/claude/    — Claude Code hooks and agent definition
geak/opencode/  — OpenCode plugin and agent definition
```

## Quick start

GEAK is opt-in. SLURM jobs use the LUMI-curated **lumi-multitorch** Singularity
image and bootstrap GEAK in-job via [`setup-geak.sh`](setup-geak.sh) (first run
is slower; requires compute-node access to GitHub — see
[Execution](#execution) below).

From a LUMI login node:

```bash
bash setup/setup.sh        # select Claude Code or OpenCode at the framework
                           # prompt; answer "yes" at the GEAK prompt; provide
                           # an Anthropic API key
bash setup/launch.sh
```

Then, inside Claude Code or OpenCode:

> Optimize the kernel in `geak/matmul_kernel.py`

Claude will load the `optimize-kernel` skill, submit a SLURM job, and spawn a
background `geak-monitor` subagent that polls until the job finishes.

## Execution

[`run-geak.sbatch`](run-geak.sbatch) runs each job inside the LUMI-curated
`lumi-multitorch-latest.sif` (override with `LUMI_MULTITORCH_SIF`) and calls
[`setup-geak.sh`](setup-geak.sh) inside the container. That script clones
`AMD-AGI/GEAK` (branch from `GEAK_BRANCH`, default `dev-silo`) and the Metrix /
MCP-tools dependencies into `<project>/.deps/`, builds a venv at
`/tmp/geak-venv`, and installs everything with `pip`. The project is
bind-mounted at `/workspace` so [`setup-geak.sh`](setup-geak.sh) can resolve
its `/workspace/.deps` and `/workspace/.git-credentials` paths.

This requires compute-node outbound access to GitHub. On LUMI this typically
means using a `<project>/.git-credentials` file (the helper paths in
[`setup-geak.sh`](setup-geak.sh) point at `/workspace/.git-credentials` inside
the container). The first job is materially slower because of the clone +
install; subsequent jobs reuse `<project>/.deps` where possible, but the venv
at `/tmp/geak-venv` is node-local and will be recreated on a new node.

## Architecture

```text
LUMI login node
  `- Claude Code  OR  OpenCode
       |- LUMI skills (lumi-slurm-job-management, lumi-resource-management, profiling)
       |    `- direct SLURM / rocprofv3 commands
       |- GEAK skills (optimize-kernel, check-geak-job, cancel-geak-job)
       |    `- .{claude,opencode}/skills/<skill>/scripts/*.sh
       |         `- run-geak.sbatch        (SLURM, scheduled on a GPU node)
       |              `- singularity exec lumi-multitorch.sif \
       |                     setup-geak.sh + geak --kernel-url /workspace/...
       |- Security guards
       |    Claude Code: hooks in .claude/hooks/
       |    OpenCode:    plugin  .opencode/plugins/geak-guards.ts
       `- geak-monitor subagent (background child session)
            `- polls check-geak-job.sh every 10 min
            `- returns a summary to the main agent
```

## Skills and subagents

| Name              | Type      | Description                                                  |
| ----------------- | --------- | ------------------------------------------------------------ |
| `optimize-kernel` | skill     | Submit a kernel for GEAK optimization                        |
| `check-geak-job`  | skill     | Check GEAK job status by ID                                  |
| `cancel-geak-job` | skill     | Cancel a running GEAK job                                    |
| `geak-monitor`    | subagent  | Background monitor that polls a GEAK job and reports results |

## Configuration

| Setting                | Default                                                   | Where configured                                       |
| ---------------------- | --------------------------------------------------------- | ------------------------------------------------------ |
| GEAK branch            | `dev-silo`                                                | `GEAK_BRANCH` env var, read by `geak/setup-geak.sh`    |
| Multitorch SIF         | `/appl/local/laifs/containers/lumi-multitorch-latest.sif` | `LUMI_MULTITORCH_SIF` env var (override only)          |
| Agent model            | `claude-haiku-4-5`                                        | `geak/commandment-config.yaml` (`model.model_name`)    |
| MCP tools model        | `claude-haiku-4-5`                                        | `geak/commandment-config.yaml` (`mcp.default_model`)   |
| Cost limit             | disabled (`0`)                                            | `geak/commandment-config.yaml` (`agent.cost_limit`)    |
| SLURM partition        | `small-g`                                                 | `geak/run-geak.sbatch`                                 |
| SLURM account          | Your LUMI project (`SBATCH_ACCOUNT` env or `--account`)   | `geak/run-geak.sbatch`                                 |
| Max rounds             | 1                                                         | `geak/run-geak.sbatch`                                 |

GEAK calls Anthropic directly from inside the SLURM container, so an Anthropic
API key is required *even when the agent itself runs on Aitta or another
backend*. The setup script collects the key, writes it to
`<project>/.anthropic_api_key` (gitignored) for `run-geak.sbatch` to read, and
also stores it in `~/.config/hpc-assistant/credentials.env` as
`GEAK_ANTHROPIC_API_KEY`.

### Caveats

- **`anthropic_model` is required.** [`commandment-config.yaml`](commandment-config.yaml)
  pins `model.model_class: anthropic_model`. dev-silo's standalone
  `AnthropicModel` filters `model_kwargs` through an SDK-level allow-list
  (`temperature`, `max_tokens`, `top_p`, `top_k`, `stop_sequences`, `stream`,
  `metadata`, `system`, `tools`, `tool_choice`), which is what makes the
  GPT-only `reasoning` / `text` keys inherited from `mini.yaml` harmless. Do
  not switch to `litellm_model` (or `amd_llm`) without verifying that those
  base-config keys won't reach the Anthropic API.

## Security guards

Both frameworks enforce the same four policies. Implementation differs by framework.

| Policy | Claude Code | OpenCode |
|--------|-------------|----------|
| Block raw cluster commands | `enforce-geak-bash.sh` (PreToolUse/Bash) | `geak-guards.ts` `tool.execute.before` |
| Protect workflow files | `protect-scripts.sh` (PreToolUse/Edit\|Write) | `geak-guards.ts` `tool.execute.before` |
| Prevent duplicate monitors | `guard-monitor-spawn.sh` (PreToolUse/Agent, **blocks**) | `geak-guards.ts` `session.created` (**observational only** — see note) |
| Clean up stale locks | `cleanup-monitor.sh` (SubagentStop) | `geak-guards.ts` `session.deleted` |

> **OpenCode note:** In OpenCode, subagents are child sessions forked after the
> model decides to spawn them. There is no pre-fork hook, so the plugin cannot
> block a duplicate spawn outright. Instead it writes a `.monitoring` lock on
> `session.created` and logs collisions. The `geak-monitor` agent checks for the
> lock at startup and self-terminates if it finds one already present.

Denials are logged to:
- Claude Code: `.claude/logs/geak-hook-denials.log`
- OpenCode: `.opencode/logs/geak-hook-denials.log`

## Files in this directory

```text
geak/
  run-geak.sbatch          SLURM batch script (lumi-multitorch + setup-geak.sh)
  setup-geak.sh            In-container bootstrap (clone GEAK + deps, build venv)
  commandment-config.yaml  GEAK agent workflow config
  matmul_kernel.py         Sample Triton kernel
  GEAK.md                  This file
  claude/
    agents/
      geak-monitor.md      Background job-monitor subagent (Claude Code)
    hooks/
      enforce-geak-bash.sh
      protect-scripts.sh
      guard-monitor-spawn.sh
      cleanup-monitor.sh
  opencode/
    agents/
      geak-monitor.md      Subagent definition (OpenCode)
    plugins/
      geak-guards.ts       Security plugin (OpenCode)
```

`setup.sh` copies framework-specific assets into the project:

- **Claude Code:** `claude/agents/geak-monitor.md` → `.claude/agents/`,
  `claude/hooks/*.sh` → `.claude/hooks/`
- **OpenCode:** `opencode/agents/geak-monitor.md` → `.opencode/agents/`,
  `opencode/plugins/geak-guards.ts` → `.opencode/plugins/`

Both frameworks receive the four runtime files (`run-geak.sbatch`,
`setup-geak.sh`, `commandment-config.yaml`, `matmul_kernel.py`) at the
project root, and the GEAK skills under `.{claude,opencode}/skills/`.
