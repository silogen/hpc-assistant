# Running the agent on the login node

This is the on-cluster placement: the agent process itself runs on the HPC login node.
[Local mode](LOCAL-MODE.md) is the recommended alternative and the one we suggest for
wider use — on-cluster is the right choice when you need to work directly against data
that cannot leave the cluster.

All three paths below run from a clone of this repository:

```bash
git clone https://github.com/silogen/hpc-assistant.git $HOME/hpc-assistant
cd $HOME/hpc-assistant
```

| Path | Use it when |
| ---- | ----------- |
| [Environment module](#option-a--environment-module) | The site installs this repository on a shared path. No wizard, no containers. |
| [Setup wizard](#option-b--setup-wizard) | First time, or you want non-default options. |
| [Manual setup](#option-c--manual-setup) | You would rather configure the files yourself. |

## Option A — Environment module

For LUMI, or any site that installs this repository under `/appl` or another shared
path, use the included **Lmod** module and init script instead of the full wizard. This
installs skills and copies the agent templates into your **project directory**; it does
not prompt for credentials or start containers.

```bash
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

`hpc-assistant-init` uses the same logic as the wizard for skills and templates
(`configure_opencode_project` in `../setup/config.sh`). By default it writes `AGENTS.md`,
`opencode.json` and `.opencode/skills/` under the project directory. Existing `AGENTS.md`
and `opencode.json` files are left unchanged unless you pass `--force`.

| Flag | Purpose |
| ---- | ------- |
| `--site NAME` | HPC site (default: `LUMI`) |
| `-d, --dir DIR` | Project directory (default: current directory) |
| `-f, --force` | Overwrite existing agent config files |
| `--framework opencode\|claude-code` | Agent framework (default: `opencode`) |
| `--inference anthropic\|aitta\|custom-openai` | Inference backend (default: `anthropic`) |
| `--api-base URL` | Base URL of your OpenAI-compatible endpoint (`custom-openai` only) |
| `--model ID` | Model id served by that endpoint (`custom-openai` only) |
| `--sif-path PATH` | Path to `aitta-proxy.sif` (claude-code + aitta/custom-openai only) |
| `--with-skill NAME` | Opt in to an optional skill (repeatable), e.g. `geak-optimization` |
| `--no-optional-skills` | Do not install optional skills (overrides a saved list) |
| `--no-save-config` | Skip writing `~/.config/hpc-assistant/config.env` |

> **Claude Code + Aitta:** OpenCode talks to Aitta directly, but Claude Code only speaks
> the Anthropic API, so it must go through the LiteLLM proxy (`aitta-proxy.sif`). For that
> combination run `hpc-assistant-init --framework claude-code --inference aitta --sif-path
> <…>/aitta-proxy.sif`, then use `../setup/setup.sh` for credentials and
> `../setup/launch.sh` to start the proxy — the module init alone does not start
> containers.

**GEAK via the module:** enable the skill with `hpc-assistant-init --with-skill
geak-optimization`. The skill (`skills/geak-optimization/`) covers building the GEAK
environment and submitting a kernel-optimization job on LUMI.

**For site operators:** install the tree under e.g. `/appl/local/laifs/hpc-assistant` and
register `modulefiles/` in the site module path. The module resolves `HPC_ASSISTANT_ROOT`
from the modulefile location automatically.

The module also sets `OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true`, matching
`../setup/launch.sh`. That enables non-blocking background subagents and needs
OpenCode ≥ 1.14.51. Unload the module if you need it unset.

## Option B — Setup wizard

### 1. Pull container images

> Skip this step entirely if you are on LUMI using the default configuration (remote MCP +
> Aitta + OpenCode). No containers are needed for that path.

Images are published to the public Docker Hub repository
[`amdenterpriseai/hpc-assistant`](https://hub.docker.com/r/amdenterpriseai/hpc-assistant).
All images share that one repository and are distinguished by a tag prefix. No Docker Hub
login is required.

```bash
mkdir -p ~/.local/share/hpc-assistant/sif/
cd ~/.local/share/hpc-assistant/sif/

# Only needed for Aitta inference with Claude Code. OpenCode speaks the OpenAI API
# natively and connects to Aitta directly. See ../setup/dockerfiles/aitta-proxy.dockerfile.
singularity build aitta-proxy.sif docker://docker.io/amdenterpriseai/hpc-assistant:aitta-proxy-latest

# Only needed for the local-ChromaDB knowledge base. Skip both if you use a managed
# remote MCP, which is the default for LUMI.
singularity build build-db.sif docker://docker.io/amdenterpriseai/hpc-assistant:build-db-latest
singularity build chroma-mcp.sif docker://docker.io/amdenterpriseai/hpc-assistant:chroma-mcp-latest
```

### 2. Run setup on the login node

```bash
bash setup/setup.sh
```

The wizard will:

1. Ask you to select your HPC site (LUMI)
2. Use the site's managed remote MCP as the knowledge base automatically — LUMI ships one,
   so there is nothing to build or host and no question is asked
3. Offer default LUMI settings (Aitta + `openai/gpt-oss-120b`); accept with Enter to skip
   the remaining questions
4. Otherwise ask for the inference backend (Aitta, Anthropic, or your own
   OpenAI-compatible endpoint)
5. Ask for the agent framework (Claude Code, OpenCode, or Generic)
6. (LUMI only) Optionally enable GEAK kernel optimization, prompting for an Anthropic key
7. Prompt for SIF paths where needed
8. (Local-ChromaDB only) Clone the site documentation and build the knowledge base
9. Start the services that path requires as Singularity instances — none, for OpenCode +
   Aitta
10. Install skills and configure MCP for the selected framework

### 3. Launch

```bash
bash setup/launch.sh
```

The launch script checks that services are running, restarting them if needed — which also
handles a change of login node — and then starts the agent.

## Option C — Manual setup

The repository includes pre-filled templates for LUMI under `../setup/templates/`.

### 1. Copy the agent config template

**OpenCode:**

```bash
# Aitta inference (default for LUMI)
cp setup/templates/lumi-opencode-aitta.json opencode.json

# Or the Anthropic API
cp setup/templates/lumi-opencode-anthropic.json opencode.json
```

`lumi-opencode-aitta.json` comes pre-configured with the Aitta provider pointing at
`https://aitta-api.csc.fi/openai/v1`, the LUMI AI Factory MCP (`lumi-aif`) registered, and
HPC-safe permission rules. It declares several Aitta models — including
`openai/gpt-oss-120b` — and selects `aitta/Qwen/Qwen3.6-27B`. Note that this differs from
the wizard, whose accept-the-defaults path sets `openai/gpt-oss-120b`. Edit `opencode.json`
directly after copying if you need a different URL or model.

**Your own OpenAI-compatible endpoint:**

```bash
cp setup/templates/opencode-custom.json opencode.json
```

Replace the `{{CUSTOM_API_BASE}}` and `{{CUSTOM_MODEL}}` placeholders with your endpoint
and model, and `"{{OC_MCP_BLOCK}}"` with an MCP block — e.g. the `lumi-aif` block from
`lumi-opencode-aitta.json` — or `{}` for no knowledge base. The API key is read from
`CUSTOM_API_KEY` in the environment.

**Claude Code:**

```bash
cp setup/templates/lumi-system-prompt.md CLAUDE.md
claude mcp add --transport http lumi-aif https://lumi-aif-agents.2.rahtiapp.fi/mcp
```

### 2. Copy the system prompt

For OpenCode the system prompt path is declared inside `opencode.json` as
`"instructions": ["AGENTS.md"]`:

```bash
cp setup/templates/lumi-system-prompt.md AGENTS.md
```

### 3. Install skills

```bash
# OpenCode
mkdir -p .opencode/skills
cp -r skills/profiling skills/lumi-resource-management \
      skills/lumi-slurm-job-management skills/energy-profiling .opencode/skills/

# Claude Code
mkdir -p .claude/skills
cp -r skills/profiling skills/lumi-resource-management \
      skills/lumi-slurm-job-management skills/energy-profiling .claude/skills/
```

### 4. Set your token and launch

**OpenCode + Aitta:**

```bash
export AITTA_TOKEN=your-token-here
opencode
```

**Anthropic API:**

```bash
export ANTHROPIC_API_KEY=your-key-here
opencode   # or: claude
```

**Claude Code + Aitta** needs the LiteLLM proxy from `aitta-proxy.sif`. The model is passed
with a `hosted_vllm/` prefix, which selects LiteLLM's OpenAI-compatible provider and is
stripped before the request reaches Aitta:

```bash
export AITTA_API_BASE=https://aitta-api.csc.fi/openai/v1
export AITTA_TOKEN=your-token-here
export LITELLM_MODEL=hosted_vllm/openai/gpt-oss-120b
# `singularity` or `apptainer` — same subcommands.
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

# The proxy exposes the Aitta backend under the model name `aitta`
# (see ../setup/aitta-proxy/config.yaml).
export ANTHROPIC_BASE_URL=http://localhost:8001
export ANTHROPIC_API_KEY="$AITTA_TOKEN"
export ANTHROPIC_AUTH_TOKEN="$AITTA_TOKEN"
export ANTHROPIC_MODEL=aitta
export ANTHROPIC_SMALL_FAST_MODEL=aitta
claude
```

## Stopping services

```bash
source setup/config.sh
detect_container_runtime
stop_services
```

Or stop only what is actually running for your setup:

```bash
singularity instance stop aitta-proxy   # Aitta + Claude Code only
singularity instance stop chroma-mcp    # local-ChromaDB only
```

## Full teardown

```bash
bash setup/teardown.sh
```

Removes services, knowledge base, documentation clone, config, credentials and agent
framework integration, after prompting for confirmation. SIF images and agent binaries are
left in place.
