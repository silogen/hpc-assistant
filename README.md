# HPC Assistant

> Agent-agnostic HPC Assistant for AMD GPU systems — site-specific skills, a RAG knowledge base, and an inference proxy for HPC login nodes.

The HPC Assistant brings safe, site-aware AI coding agents to HPC login nodes where direct LLM API access and `pip install` are typically unavailable. It ships site-specific skills (Slurm job management, resource and quota commands, AMD GPU profiling with `rocprofv3`), a RAG knowledge base built from HPC documentation and served over MCP, and an Anthropic-to-OpenAI inference proxy so users can route through alternative backends (e.g. Aitta on LUMI). Skills are stored in a generic markdown format that works with Claude Code, OpenCode, and other agent frameworks. Targets AMD Instinct GPU workloads on AMD-equipped HPC systems such as LUMI.

## Features

- **RAG Knowledge Base** — pluggable backend: a managed remote MCP (e.g. the
  [LUMI AI Factory MCP](https://docs.lumi-supercomputer.eu/laif/software/agent-infrastructure/#mcp-server),
  the recommended default for LUMI) or a local ChromaDB built from cloned HPC docs and served via MCP
- **Agent Skills** — site-specific and generic skills for resource management, Slurm job management, and GPU profiling
- **Aitta Proxy** — [LiteLLM](https://docs.litellm.ai/) proxy (Anthropic-to-OpenAI translation) for using alternative inference backends (e.g. Aitta service on LUMI, or your own OpenAI-compatible endpoint)
- **Agent-Agnostic** — skills are stored in a generic format; supports Claude Code and OpenCode, with the option to add other frameworks
- **Login-Node Friendly** — no `pip install` required; all Python dependencies run inside Singularity (or Apptainer) containers

## Supported HPC Sites

| Site | Skills                                                 | Knowledge base                                                                                                                                              | Documentation                                                          |
| ---- | ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| LUMI | Profiling, Resource Management, Slurm Job Management   | Remote MCP ([LUMI AI Factory](https://docs.lumi-supercomputer.eu/laif/software/agent-infrastructure/#mcp-server), default) **or** local ChromaDB (optional) | [LUMI User Guide](https://github.com/Lumi-supercomputer/lumi-userguide)|

## Directory Structure

Layout at a glance:

```text
hpc-assistant/
├── skills/                  # Agent skills (copied into your project by setup)
├── geak/                    # Optional GEAK workflow — see geak/GEAK.md
│   ├── GEAK.md, run-geak.sbatch, setup-geak.sh, commandment-config.yaml, matmul_kernel.py
│   ├── THIRD_PARTY_DOWNLOAD_NOTICE.txt, download-notice.sh, accept-download-notice.sh
│   ├── claude/              # hooks + geak-monitor agent
│   └── opencode/            # geak-guards plugin + geak-monitor agent
├── setup/                   # setup.sh, teardown.sh, launch.sh, config.sh, build_db.py, templates/, dockerfiles/, requirements/
├── bin/hpc-assistant-init     # Project init (with module load)
├── modulefiles/hpc-assistant/ # Lmod module for site installs
├── LICENSE, LICENSE.TXT         # MIT + AMD license summary
├── THIRD-PARTY-NOTICES.txt
├── SECURITY.md
└── .github/                   # CI, issue/PR templates, CODEOWNERS
```

**Created in your project directory by setup** (not at the repository root):

- OpenCode: `.opencode/skills/`, `opencode.json`, `AGENTS.md`
- Claude Code: `.claude/skills/`, `CLAUDE.md` (and `.claude/hooks/`, `agents/` when GEAK is enabled)
- GEAK (opt-in): copies `run-geak.sbatch`, `setup-geak.sh`, etc. to the project root; writes `.geak-third-party-download-accepted` after you accept the download notice

## Prerequisites

- **Singularity** (or **Apptainer** — same CLI on most sites) available on the HPC system
- One of:
  - Aitta API token (recommended for LUMI)
  - Anthropic API key
  - Your own OpenAI-compatible endpoint (base URL, model id, and an API key if it
    requires one) — see [Custom OpenAI-Compatible Endpoint](#custom-openai-compatible-endpoint)

If using Claude Code as the agent framework:

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed:

```bash
curl -fsSL https://claude.ai/install.sh | bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
```

If using OpenCode as the agent framework:

- [OpenCode](https://opencode.ai/docs) installed:

```bash
curl -fsSL https://opencode.ai/install | bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
```

## Quick Start

First, clone the repository (all setup options below run from this checkout):

```bash
git clone https://github.com/silogen/hpc-assistant.git $HOME/hpc-assistant
cd $HOME/hpc-assistant
```

There are three ways to set up the assistant:

- [**LUMI module + init**](#option-a--lumi-environment-module) — `module load` + `hpc-assistant-init` (no full wizard)
- [**Setup wizard**](#option-b--setup-wizard) (recommended for first-time / custom options)
- [**Manual setup**](#option-c--manual-setup) if you prefer to configure files yourself

### Option A — LUMI Environment Module

For LUMI (or any site that installs this repository under `/appl` or a shared path), use the included **Lmod** module and init script instead of running the full setup wizard. This installs skills and copies the LUMI OpenCode templates into your **project directory**; it does not prompt for credentials or start containers.

```bash
# From the cloned repository (see Quick Start above), register its modulefiles
module use $HOME/hpc-assistant/modulefiles
module load hpc-assistant

cd ~/projects/myapp

# Default: OpenCode + Anthropic
hpc-assistant-init
export ANTHROPIC_API_KEY=your-key-here
opencode

# Or select the Aitta backend (OpenCode talks to Aitta directly)
hpc-assistant-init --inference aitta
export AITTA_TOKEN=your-token-here
opencode

# Or point it at your own OpenAI-compatible endpoint
hpc-assistant-init --inference custom-openai \
  --api-base http://localhost:8000/v1 --model meta-llama/Llama-3.3-70B-Instruct
export CUSTOM_API_KEY=your-key-here   # skip if the endpoint ignores it
opencode
```

> **Using Aitta:** the target model must be launched before you can use it. If it
> isn't already running, the first request triggers Aitta to allocate compute and
> load the model, which can take several minutes. See the
> [Aitta docs](https://docs.lumi-supercomputer.eu/laif/inference/aitta/) for details.

`hpc-assistant-init` uses the same logic as the setup wizard for skills and templates (`configure_opencode_project` in `setup/config.sh`). By default it writes `AGENTS.md`, `opencode.json`, and `.opencode/skills/` under the project directory. Existing `AGENTS.md` / `opencode.json` files are left unchanged unless you pass `--force`.

| Flag | Purpose |
| ---- | ------- |
| `-d DIR` | Project directory (default: current directory) |
| `--force` | Overwrite existing agent config files |
| `--framework opencode\|claude-code` | Agent framework (default: `opencode`) |
| `--inference anthropic\|aitta\|custom-openai` | Inference backend (default: `anthropic` for both frameworks) |
| `--api-base URL` | Base URL of your OpenAI-compatible endpoint (`custom-openai` only) |
| `--model ID` | Model id served by that endpoint (`custom-openai` only) |
| `--sif-path PATH` | Path to `aitta-proxy.sif` (claude-code + aitta/custom-openai only) |
| `--geak` | LUMI only: add GEAK skills + install hooks (Claude) or plugin (OpenCode) + `run-geak.sbatch` etc. |
| `--no-save-config` | Skip writing `~/.config/hpc-assistant/config.env` |

**GEAK + module path:** run `hpc-assistant-init --geak` from your project directory. Export `GEAK_ANTHROPIC_API_KEY` before init if you want `.anthropic_api_key` written in the project (GEAK jobs call Anthropic from the compute node, independent of Aitta/OpenCode login-node inference). SLURM jobs use the LUMI multitorch image at `LUMI_MULTITORCH_SIF` or the default under `/appl/local/laifs/containers/`. `run-geak.sbatch` loads `lumi-aif-singularity-bindings` — align with your site modules if paths differ.

> **Claude Code + Aitta:** OpenCode talks to Aitta directly, but Claude Code
> only speaks the Anthropic API, so it must go through the LiteLLM proxy
> (`aitta-proxy.sif`). For that combination, run `hpc-assistant-init --framework
> claude-code --inference aitta --sif-path <…>/aitta-proxy.sif`, then use
> `setup/setup.sh` (credentials) and `setup/launch.sh` (starts the proxy) — the
> module init alone does not start containers.

Site operators can install the tree under e.g. `/appl/local/laifs/hpc-assistant` and register `modulefiles/` in the site module path; the module resolves `HPC_ASSISTANT_ROOT` from the modulefile location automatically.

The module also sets **`OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true`**, matching `setup/launch.sh` when launching OpenCode (non-blocking background subagents, e.g. GEAK’s `geak-monitor`; OpenCode ≥ 1.14.51). Unload the module if you need that variable unset.

---

### Option B — Setup Wizard

#### 1. Pull Container Images

> **Skip this step entirely if you are on LUMI using the default configuration** (remote MCP + Aitta + OpenCode). No containers are needed for that path.

Images are published to **Docker Hub** in the public
[`amdenterpriseai/hpc-assistant`](https://hub.docker.com/r/amdenterpriseai/hpc-assistant)
repository. All images share that one repository and are distinguished by a tag
prefix (e.g. `amdenterpriseai/hpc-assistant:aitta-proxy-latest`). The repository
is public, so no Docker Hub login is required to pull.

Pull them on the HPC login node (into your SIF directory, e.g. `~/.local/share/hpc-assistant/sif/`):

```bash
mkdir -p ~/.local/share/hpc-assistant/sif/
cd ~/.local/share/hpc-assistant/sif/

# Aitta proxy (LiteLLM) — only needed when using Aitta inference with Claude Code.
# OpenCode speaks OpenAI API natively and connects to Aitta directly; no proxy needed.
# This image is the official LiteLLM proxy with our Aitta config baked in; see
# setup/dockerfiles/aitta-proxy.dockerfile.
singularity build aitta-proxy.sif docker://docker.io/amdenterpriseai/hpc-assistant:aitta-proxy-latest

# The two images below are ONLY needed if you choose the "local ChromaDB"
# knowledge-base backend during setup. If you use a managed remote MCP (the
# default for LUMI), you can skip both.
singularity build build-db.sif docker://docker.io/amdenterpriseai/hpc-assistant:build-db-latest
singularity build chroma-mcp.sif docker://docker.io/amdenterpriseai/hpc-assistant:chroma-mcp-latest
```

#### 2. Run Setup on the Login Node

```bash
bash setup/setup.sh
```

The setup wizard will:

1. Ask you to select your HPC site (LUMI)
2. Use the site's **managed remote MCP** as the knowledge base automatically (LUMI ships one, so there's nothing to build or host and no question is asked)
3. Offer to use **default LUMI settings** (Aitta + `openai/gpt-oss-120b`) — accept with Enter to skip the remaining configuration questions
4. If not using defaults, choose the inference backend (Aitta, Anthropic API, or your own OpenAI-compatible endpoint)
5. Select the agent framework (Claude Code, OpenCode, or Generic)
6. (LUMI only) Optionally enable **GEAK** kernel optimization (Claude Code or OpenCode; prompts for an Anthropic API key)
7. Prompt for **SIF paths** when needed (`aitta-proxy.sif` for Claude Code in front of an OpenAI-compatible endpoint; `build-db` / `chroma-mcp` for local-ChromaDB)
8. (Local-ChromaDB only) Clone HPC documentation and build the knowledge base
9. Start the relevant services as Singularity instances (skipped when not needed — e.g. OpenCode + Aitta uses no proxy)
10. Install skills and configure MCP for the selected agent framework

#### 3. Launch the Assistant

After initial setup, use the launch script to start (or restart) the assistant:

```bash
bash setup/launch.sh
```

The launch script checks that services are running (restarting them if needed — handles login node changes), then starts the agent.

---

### Option C — Manual Setup

If you prefer to configure the assistant without the wizard, follow these steps. The repository includes pre-filled templates for LUMI under `setup/templates/`.

#### 1. Copy the agent config template

**OpenCode (recommended for LUMI + Aitta):**

```bash
# Using Aitta inference (default for LUMI)
cp setup/templates/lumi-opencode-aitta.json opencode.json

# Or using Anthropic API
cp setup/templates/lumi-opencode-anthropic.json opencode.json
```

The `lumi-opencode-aitta.json` template is pre-configured with:
- Aitta provider pointing to `https://aitta-api.csc.fi/openai/v1`
- Model: `openai/gpt-oss-120b`
- LUMI AI Factory MCP (`lumi-aif`) registered
- HPC-safe permission rules

If you need a different Aitta URL or model, edit `opencode.json` directly after copying.

**Your own OpenAI-compatible endpoint:**

```bash
cp setup/templates/opencode-custom.json opencode.json
```

Then replace the `{{CUSTOM_API_BASE}}` and `{{CUSTOM_MODEL}}` placeholders with
your endpoint and model, and `"{{OC_MCP_BLOCK}}"` with an MCP block (e.g. the
`lumi-aif` block from `lumi-opencode-aitta.json`) or `{}` for no knowledge base.
The API key is read from `CUSTOM_API_KEY` in the environment.

**Claude Code:**

```bash
cp setup/templates/lumi-system-prompt.md CLAUDE.md
```

Then register the LUMI AI Factory MCP:

```bash
claude mcp add --transport http lumi-aif https://lumi-aif-agents.2.rahtiapp.fi/mcp
```

#### 2. Copy the system prompt

**OpenCode** — the system prompt path is declared inside `opencode.json` as `"instructions": ["AGENTS.md"]`:

```bash
cp setup/templates/lumi-system-prompt.md AGENTS.md
```

#### 3. Install skills

Copy the LUMI skills into the agent's skills directory:

```bash
# OpenCode
mkdir -p .opencode/skills
cp -r skills/profiling .opencode/skills/
cp -r skills/lumi-resource-management .opencode/skills/
cp -r skills/lumi-slurm-job-management .opencode/skills/

# Claude Code
mkdir -p .claude/skills
cp -r skills/profiling .claude/skills/
cp -r skills/lumi-resource-management .claude/skills/
cp -r skills/lumi-slurm-job-management .claude/skills/
```

#### 4. Set your API token and launch

**OpenCode + Aitta:**

```bash
export AITTA_TOKEN=your-token-here
opencode
```

**Claude Code + Aitta:**

Start the Aitta proxy (the LiteLLM proxy; requires `aitta-proxy.sif`). LiteLLM
routes by provider prefix, so the model is passed with a `hosted_vllm/` prefix
(`LITELLM_MODEL=hosted_vllm/$TARGET_MODEL`) — it strips `hosted_vllm/` to select
the OpenAI-compatible provider and forwards `openai/gpt-oss-120b` to Aitta
verbatim. (`hosted_vllm` is used instead of `openai` because the `openai`
provider routes `/v1/messages` through the OpenAI Responses API — which Aitta
doesn't implement — and also strips a leading `openai/` from the model name.)

```bash
export AITTA_API_BASE=https://aitta-api.csc.fi/openai/v1
export AITTA_TOKEN=your-token-here
export LITELLM_MODEL=hosted_vllm/openai/gpt-oss-120b
# Use `singularity` or `apptainer` depending on what is on your PATH (same subcommands).
singularity instance start \
  --env AITTA_API_BASE="$AITTA_API_BASE" \
  --env AITTA_TOKEN="$AITTA_TOKEN" \
  --env LITELLM_MODEL="$LITELLM_MODEL" \
  aitta-proxy.sif aitta-proxy
singularity exec \
  --env AITTA_API_BASE="$AITTA_API_BASE" \
  --env AITTA_TOKEN="$AITTA_TOKEN" \
  --env LITELLM_MODEL="$LITELLM_MODEL" \
  instance://aitta-proxy \
  litellm --config /app/config.yaml --host 0.0.0.0 --port 8001 &

# Point Claude Code at the proxy. The proxy exposes the Aitta backend as the
# model name `aitta` (see setup/aitta-proxy/config.yaml).
export ANTHROPIC_BASE_URL=http://localhost:8001
export ANTHROPIC_API_KEY="$AITTA_TOKEN"
export ANTHROPIC_AUTH_TOKEN="$AITTA_TOKEN"
export ANTHROPIC_MODEL=aitta
export ANTHROPIC_SMALL_FAST_MODEL=aitta
claude
```

**Anthropic API:**

```bash
export ANTHROPIC_API_KEY=your-key-here
opencode   # or: claude
```

## Knowledge Base Backends

The assistant queries a RAG knowledge base of HPC documentation through MCP. You
pick the backend during setup; the choice is persisted to `~/.config/hpc-assistant/config.env`
as `KB_BACKEND` and changes which tools are exposed to the agent.

### Remote MCP (Recommended for LUMI)

For sites that expose a managed MCP server, the assistant simply registers it
with your agent framework. No data is cloned to the login node, no index is
built locally, and no `chroma-mcp` instance is started.

LUMI uses the LUMI AI Factory MCP server, maintained by CSC and continuously
updated from the LUMI Docs and the LUMI AI Guide:

- URL: `https://lumi-aif-agents.2.rahtiapp.fi/mcp`
- Tool: `retrieve_docs`
- Source: [LUMI AIF — Infrastructure for AI agents](https://docs.lumi-supercomputer.eu/laif/software/agent-infrastructure/#mcp-server)

What setup wires up:

- **Claude Code** — `claude mcp add --transport http lumi-aif https://lumi-aif-agents.2.rahtiapp.fi/mcp`
- **OpenCode** — adds an `mcp.lumi-aif` block of `{ "type": "remote", "url": "..." }` to `opencode.json`
- **Generic** — point your own client at the URL; e.g. `fastmcp call https://lumi-aif-agents.2.rahtiapp.fi/mcp retrieve_docs 'query=...' 'k=2'`

The agent system prompt (`CLAUDE.md` / `AGENTS.md`) is generated to instruct
the model to call `retrieve_docs` from the `lumi-aif` MCP server before
falling back to general knowledge.

### Local ChromaDB

Clones the site's documentation repository, chunks and embeds it with the
`build-db.sif` container, and serves it through the `chroma-mcp.sif`
Singularity instance over MCP. Useful when:

- Working on a new site that does not yet have a managed MCP, or
- You want to extend or replace the knowledge base with private/internal docs.

This path requires the extra `build-db` and `chroma-mcp` SIF images from
[Pull Container Images](#1-pull-container-images).

## Inference Backends

### Aitta (Recommended for LUMI)

For Claude Code, the Aitta proxy translates Anthropic API requests to
OpenAI-compatible format. It is the official [LiteLLM](https://docs.litellm.ai/)
proxy (the well-known gateway for exactly this translation) with our Aitta
config baked in, pinned to a vetted stable release — see
`setup/dockerfiles/aitta-proxy.dockerfile`. It runs as a Singularity instance on
the login node in detached mode — no separate terminal needed.

> OpenCode speaks the OpenAI API natively and connects to Aitta directly, so it
> does not use the proxy.

**Configuration:** LiteLLM reads everything from environment variables at start
(`AITTA_API_BASE`, `AITTA_TOKEN`, and `LITELLM_MODEL`), so the published image
contains no secrets, URLs, or model names. It exposes the Aitta backend under
the model name `aitta`; Claude Code is pointed at it with `ANTHROPIC_MODEL=aitta`.

**Choosing a model:** Set `TARGET_MODEL` (via the setup wizard or your
credentials file) to pick which Aitta model the proxy serves — the launch
scripts inject it at runtime, so no rebuild is needed. The list of known
default models lives in [`setup/aitta-proxy/config.yaml`](setup/aitta-proxy/config.yaml);
edit that file (and rebuild the SIF) if you want to change the baked-in default.

**Login node handling:** The proxy binds to `0.0.0.0` so it's accessible from any login node. The launch script detects which node the proxy is on and reconnects or restarts as needed.

### Anthropic API

Direct connection to Anthropic's API. Requires a valid API key and network access from the login node.

### Custom OpenAI-Compatible Endpoint

Select **`custom-openai`** to use any endpoint that serves the OpenAI
`/chat/completions` API — a vLLM or llama.cpp server you started yourself
(including one running in a SLURM job), Ollama, TGI, a site-hosted gateway, or a
commercial OpenAI-compatible provider.

You supply three things; nothing about the endpoint is baked into this repository
or the container images:

| Value | Where it is stored | Notes |
| ----- | ------------------ | ----- |
| Base URL (`CUSTOM_API_BASE`) | `config.env` | Include the version path, e.g. `http://localhost:8000/v1` |
| Model id (`CUSTOM_MODEL`) | `config.env` | Must match what the endpoint serves — there is no default |
| API key (`CUSTOM_API_KEY`) | `credentials.env` (mode 600) | Optional; a placeholder is stored for servers that don't check it |

Set it up either way:

```bash
# Wizard: answer "n" to the defaults prompt, then pick option 3
bash setup/setup.sh

# Module path (no prompts):
hpc-assistant-init --inference custom-openai \
  --api-base http://localhost:8000/v1 --model my-model
```

**OpenCode** connects to the endpoint directly through the
`@ai-sdk/openai-compatible` provider — no proxy and no container. The URL and
model are written into `opencode.json`, and only the key is passed at launch, so
change the URL or model by editing `opencode.json` (or re-running setup).

**Claude Code** goes through the same LiteLLM proxy used for Aitta, because it
only speaks the Anthropic API. This needs `aitta-proxy.sif`, and requests are
routed with the `hosted_vllm/` provider prefix — correct for vLLM and most
self-hosted OpenAI-compatible servers. The proxy reads your URL, key, and model
from the environment at start, so the published image needs no rebuild.

> **Login-node reachability:** the endpoint must be reachable from the login node.
> A model served inside a SLURM job needs the compute node's hostname (and an
> open port), not `localhost`.

## Skills

Skills are stored in `skills/` in a generic markdown format compatible with multiple agent frameworks. Each skill has a `SKILL.md` file with YAML frontmatter and instruction content, plus optional `references/` for supporting documentation.

| Skill                        | Scope                | Description                                                       |
| ---------------------------- | -------------------- | ----------------------------------------------------------------- |
| `profiling`                  | Generic              | AMD GPU profiling with rocprofv3 (tracing, counters, Perfetto)    |
| `lumi-resource-management`   | LUMI                 | Project quotas, billing units, storage allocations                |
| `lumi-slurm-job-management`  | LUMI                 | Slurm job submission, partitions, containers, Python/PyTorch jobs |
| `optimize-kernel`            | LUMI (GEAK, opt-in)  | Submit a Triton/HIP kernel for GEAK optimization via SLURM        |
| `check-geak-job`             | LUMI (GEAK, opt-in)  | Check the status / results of a GEAK optimization job             |
| `cancel-geak-job`            | LUMI (GEAK, opt-in)  | Cancel a running GEAK job                                         |

### Adding Skills for a New Site

1. Create a skill directory under `skills/` (e.g. `skills/mysite-slurm/`)
2. Write `SKILL.md` with frontmatter and instructions
3. Add the skill name to the site config in `setup/config.sh` (the `get_site_config` function)
4. Re-run `setup.sh` to install the new skills

## Agent Framework Support

### Claude Code

When Claude Code is selected during setup:

- Skills from `skills/` are copied to `.claude/skills/`
- The knowledge-base MCP is registered: **remote MCP** (LUMI default) or **chroma** (local-ChromaDB path)
- A `CLAUDE.md` is created with assistant instructions
- Launch script sets environment variables and starts `claude`

Use from any project directory:

```bash
claude --add-dir /path/to/hpc-assistant
```

### OpenCode

When OpenCode is selected during setup:

- Skills from `skills/` are copied to `.opencode/skills/`
- An `AGENTS.md` is created with assistant instructions
- An `opencode.json` is generated with:
  - Provider configuration (Aitta via `@ai-sdk/openai-compatible` or Anthropic native)
  - MCP registration (remote `lumi-aif` on LUMI, or local chroma when using local-ChromaDB)
  - Model selection
- Launch script exports the API token and starts `opencode`

**Key advantage with Aitta:** OpenCode speaks OpenAI API natively via the `@ai-sdk/openai-compatible` provider, so it connects directly to the Aitta endpoint — no proxy needed. This means one fewer container to manage.

Use from the project directory:

```bash
cd /path/to/hpc-assistant && opencode
```

### Generic / Other Frameworks

Skills are available as markdown files in `skills/`. Services (chroma-mcp, aitta-proxy) run independently. Configure your agent framework to:

- Use the chroma MCP server for RAG queries
- Read skill files for instruction context
- Point API requests at the aitta-proxy if using Aitta inference. The proxy is a
  LiteLLM gateway: send Anthropic-format requests to `/v1/messages` (model name
  `aitta`) or OpenAI-format requests to `/v1/chat/completions`.

## GEAK Kernel Optimization (LUMI, opt-in)

GEAK is an automated GPU-kernel optimization workflow that runs the
[GEAK](https://github.com/AMD-AGI/GEAK) CLI inside a Singularity container on a
LUMI GPU node. From the assistant, you describe the kernel you want optimized,
the agent submits the SLURM job, and a background `geak-monitor` subagent polls
for results.

GEAK is **opt-in** and supported on **Claude Code** and **OpenCode** (hooks on
Claude Code; `geak-guards.ts` plugin on OpenCode).

### What gets installed when GEAK is enabled

- The three GEAK skills (`optimize-kernel`, `check-geak-job`, `cancel-geak-job`)
  are copied into `.{claude,opencode}/skills/` alongside the LUMI skills.
- The `geak-monitor` background subagent is copied to `.{claude,opencode}/agents/`.
- **Claude Code:** four PreToolUse / SubagentStop hooks in `.claude/hooks/`, with
  entries written into `.claude/settings.json`.
- **OpenCode:** `geak-guards.ts` copied to `.opencode/plugins/` and registered in
  `opencode.json`.
- The runtime files (`run-geak.sbatch`, `setup-geak.sh`,
  `commandment-config.yaml`, `matmul_kernel.py`) are copied from `geak/` to
  the project root, where the wrapper scripts expect them.
- An Anthropic API key is collected and stored in
  `<project>/.anthropic_api_key` (mode 600, gitignored), and mirrored into
  `~/.config/hpc-assistant/credentials.env` as `GEAK_ANTHROPIC_API_KEY` so
  `launch.sh` can restore it on a new login node.
- SLURM jobs use the LUMI **lumi-multitorch** Singularity image and bootstrap
  GEAK in-job via `setup-geak.sh` (see [`geak/GEAK.md`](geak/GEAK.md)).

### Why a separate Anthropic API key?

GEAK calls Anthropic directly from inside the SLURM container (it does not go
through the `aitta-proxy`). An Anthropic API key is therefore required even if
the agent itself is configured to use Aitta or another backend.

### Enabling GEAK

Run setup on a LUMI login node and answer "yes" at the GEAK prompt:

```bash
bash setup/setup.sh
# ... select LUMI, then Claude Code or OpenCode, then answer "y" to "Install GEAK?"
# ... paste the Anthropic API key when prompted

bash setup/launch.sh
```

Then, inside Claude Code or OpenCode:

> Optimize the kernel in `matmul_kernel.py`

See [`geak/GEAK.md`](geak/GEAK.md) for the architecture diagram, full skills /
hooks table, configuration reference, and a list of files copied into place.

### Safety guards

**Claude Code** — three PreToolUse hooks and one SubagentStop hook block raw
cluster commands, protect workflow files, and prevent duplicate `geak-monitor`
spawns. Denials are logged to `.claude/logs/geak-hook-denials.log`.

**OpenCode** — the `geak-guards.ts` plugin enforces the same policies (bash/edit
guards block; monitor spawn is observational only — see [`geak/GEAK.md`](geak/GEAK.md)).
Denials are logged to `.opencode/logs/geak-hook-denials.log`.

## Security and Safety

The assistant is designed to run on shared HPC login nodes where careless commands can
affect other users, waste billing units, or corrupt data. Safety is enforced at two levels:

### Prompt-Level Rules (CLAUDE.md / AGENTS.md)

Both Claude Code and OpenCode receive explicit HPC safety instructions:

- Never run destructive commands (`rm -rf`, `module purge`) without confirmation
- Never submit or cancel Slurm jobs without showing the full command first
- Never modify shell config files (`.bashrc`, etc.) without approval
- Prefer read-only investigation before making changes
- Warn about quota impact before creating large files

### Tool-Level Permissions (OpenCode only)

OpenCode's `opencode.json` includes granular permissions:

| Category              | Policy                                                     |
| --------------------- | ---------------------------------------------------------- |
| Read-only commands    | `allow` - `ls`, `cat`, `grep`, `squeue`, `git`, `module`   |
| Job submission        | `ask` - `sbatch`, `srun`, `salloc`, `scancel`              |
| File modification     | `ask` - all edits require approval                         |
| Deletion              | `ask` for `rm`, `deny` for `rm -rf`                        |
| System administration | `deny` - `chown`, `dd`, `mkfs`, `kill -9`, `reboot`        |
| External directories  | `ask` - any access outside the project directory           |
| Doom loop detection   | `deny` - prevents infinite tool call loops                 |

To customize permissions, edit the `permission` block in `opencode.json`. See the
[OpenCode permissions docs](https://opencode.ai/docs/permissions/) for full syntax.

## Configuration

All configuration is stored in `~/.config/hpc-assistant/`:

| File               | Contents                                                                           |
| ------------------ | ---------------------------------------------------------------------------------- |
| `config.env`       | Site, framework, KB backend, paths, proxy host/port, custom endpoint URL and model |
| `credentials.env`  | API keys and tokens (mode 600)                                                     |
| `logs/`            | Service logs (aitta-proxy, etc.)                                                   |

## Stopping Services

```bash
# Source the config helpers
source setup/config.sh
detect_container_runtime
stop_services
```

Or manually (only the services that are actually running for your setup):

```bash
singularity instance stop aitta-proxy   # Aitta + Claude Code only
singularity instance stop chroma-mcp    # local-chroma KB only
```

## Full Teardown

To remove everything — services, knowledge base, documentation, config, credentials, and agent framework integration:

```bash
bash setup/teardown.sh
```

This will prompt for confirmation before proceeding. SIF container images and agent binaries (Claude Code, OpenCode) are not removed.

---

## Feedback

Questions and bug reports are welcome — please open a
[GitHub Issue](https://github.com/silogen/hpc-assistant/issues).

Report security vulnerabilities privately — see [SECURITY.md](SECURITY.md).

## Contact

For questions, issues, or contributions, please reach out to the maintainers:

- Sopiko Kurdadze — [@sopiko99](https://github.com/sopiko99)
- Albin Toft — [@albitoft-amd](https://github.com/albitoft-amd)

For questions or bug reports, please open a [GitHub Issue](https://github.com/silogen/hpc-assistant/issues).

Team ownership: [@silogen/hpc-assistant](https://github.com/orgs/silogen/teams/hpc-assistant).

See [CODEOWNERS](.github/CODEOWNERS) for the full ownership list.

---

## Governance

| File | Purpose |
| ---- | ------- |
| [SECURITY.md](SECURITY.md) | How to report security vulnerabilities |
| [.github/CODEOWNERS](.github/CODEOWNERS) | Code ownership and PR review routing |

## Open Source Compliance

This repository follows AMD's open-source software release requirements.
Compliance artifacts:

| File | Purpose |
| ---- | ------- |
| [LICENSE.TXT](LICENSE.TXT) | License summary for AMD-authored and third-party components |
| [LICENSE](LICENSE) | MIT license text for AMD-authored software |
| [THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt) | Third-party copyright and license notices |

AMD-authored source files include SPDX license identifiers in their headers.
Container images distributed via Docker Hub include third-party components documented
in `THIRD-PARTY-NOTICES.txt`.

## License

AMD-authored software in this project is licensed under the
[MIT License](LICENSE). See [LICENSE.TXT](LICENSE.TXT) for the full license
summary including third-party components.
