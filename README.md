# HPC Assistant

> Agent-agnostic HPC Assistant for AMD GPU systems — site-specific skills, a RAG knowledge base, and an inference proxy for HPC login nodes.

The HPC Assistant brings safe, site-aware AI coding agents to HPC systems where direct LLM
API access and `pip install` are typically unavailable. It ships site-specific skills for
Slurm, quotas, AMD GPU profiling and energy measurement; a RAG knowledge base served over
MCP; and an Anthropic-to-OpenAI proxy so you can route through alternative backends such as
Aitta on LUMI. Skills are plain markdown that works with Claude Code, OpenCode and other
frameworks, and the target is AMD Instinct GPU workloads on systems such as LUMI.

## Features

- **RAG knowledge base** — a managed remote MCP (the
  [LUMI AI Factory MCP](https://docs.lumi-supercomputer.eu/laif/software/agent-infrastructure/#mcp-server)
  is the default on LUMI) or a local ChromaDB built from cloned HPC docs
- **Agent skills** — Slurm job management, resource and quota commands, GPU profiling with
  `rocprofv3`, energy profiling with Omnistat
- **Runs where you want it** — on your own machine against the cluster over SSH, or on the
  login node itself
- **Agent-agnostic** — generic skill format; Claude Code and OpenCode supported, others
  addable
- **Login-node friendly** — no `pip install` required; Python dependencies run inside
  Singularity or Apptainer containers

## Supported HPC sites

| Site | Skills | Knowledge base | Documentation |
| ---- | ------ | -------------- | ------------- |
| LUMI | Profiling, Resource Management, Slurm Job Management, Energy Consumption Profiling | Remote MCP ([LUMI AI Factory](https://docs.lumi-supercomputer.eu/laif/software/agent-infrastructure/#mcp-server), default) **or** local ChromaDB | [LUMI User Guide](https://github.com/Lumi-supercomputer/lumi-userguide) |

## Quick start

**Prerequisites.** An agent framework, and Python 3.10+ for local mode. 

Install OpenCode
(the default):

```bash
curl -fsSL https://opencode.ai/install | bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
```

Or Claude Code:

```bash
curl -fsSL https://claude.ai/install.sh | bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
```

You also need an API key for whichever [inference backend](docs/backends.md) you choose.
Singularity or Apptainer is needed only for the on-cluster extras — the knowledge-base
containers and, for Claude Code with Aitta, the proxy.

Then clone this repository:

```bash
git clone https://github.com/silogen/hpc-assistant.git $HOME/hpc-assistant
```

**Decide where the agent runs.** In local mode it runs on your own machine and uses the
login node only for scheduler and file operations, which keeps recursive listing and
content search off the parallel filesystem. That is the recommended placement. Run it on
the login node instead when you need to work directly against data that cannot leave the
cluster.

### A. Local mode — agent on your own machine (recommended)

Set up SSH multiplexing, then run one command inside a local clone of your project:

```bash
# 1. In ~/.ssh/config — without this, every operation opens a new SSH connection
#    Host lumi
#        HostName lumi.csc.fi
#        User <your-username>
#        ControlMaster auto
#        ControlPath ~/.ssh/cm-%r@%h:%p
#        ControlPersist 10m
ssh lumi true && ssh -O check lumi

# 2. Installs the bridge that gives the agent its cluster tools, configures the
#    project, and verifies the connection. --remote-host is the Host alias from
#    step 1, so these two have to agree.
cd ~/projects/myapp
$HOME/hpc-assistant/scripts/hpc-assistant-local-setup \
  --remote-host lumi \
  --remote-root /scratch/project_465000000/myuser/myapp \
  --account project_465000000

# 3. Run the agent locally
export ANTHROPIC_API_KEY=your-key-here
opencode
```

The bridge installs into a virtualenv of its own; re-running the command is how you upgrade
it. Nothing is installed on the cluster.

### B. On the login node

For sites that install this repository on a shared path, the Lmod module plus
`hpc-assistant-init` is the shortest route. It copies skills and agent config into your
project directory without prompting for credentials or starting containers:

```bash
module use $HOME/hpc-assistant/modulefiles
module load hpc-assistant

cd ~/projects/myapp
hpc-assistant-init
export ANTHROPIC_API_KEY=your-key-here
opencode
```

For a guided setup with all the options — including the local-ChromaDB knowledge base and
Claude Code with Aitta — use the wizard, `bash setup/setup.sh`, and see
[docs/on-cluster-setup.md](docs/on-cluster-setup.md).

### Using Aitta instead

Both paths take `--inference aitta` and read `AITTA_TOKEN`. For local mode:

```bash
hpc-assistant-local-setup --inference aitta \
  --remote-host lumi \
  --remote-root /scratch/project_465000000/myuser/myapp \
  --account project_465000000
export AITTA_TOKEN=your-token-here
opencode
```

Or on the login node:

```bash
hpc-assistant-init --inference aitta
export AITTA_TOKEN=your-token-here
opencode
```

> The Aitta model must be running before you can use it. If it is not, your first request
> triggers Aitta to allocate compute and load it, which can take several minutes.

OpenCode talks to Aitta directly. Claude Code only speaks the Anthropic API, so that
combination additionally needs the LiteLLM proxy — see [docs/backends.md](docs/backends.md).

### Skills only, no cluster setup

To install just the agent skills — no bridge, no containers, no knowledge base — use the
[`skills` CLI](https://github.com/vercel-labs/skills) via `npx` (needs
[Node.js](https://nodejs.org)):

```bash
npx skills add silogen/hpc-assistant
```

That prompts you to pick skills and a destination. To target one skill and framework
directly:

```bash
npx skills add silogen/hpc-assistant --skill profiling --agent opencode
```

This repository is also a Claude Code / Codex / Cursor plugin marketplace. In Claude Code:

```text
/plugin marketplace add silogen/hpc-assistant
/plugin install hpc-assistant-skills@hpc-assistant
```

Either way installs `geak-optimization` too — same opt-in behavior as the full setup: it
only copies markdown, never the GEAK package itself.

## Skills

Skills live in `skills/` as generic markdown: a `SKILL.md` with YAML frontmatter and
instructions, plus optional `references/`. Setup copies them into `.opencode/skills/` or
`.claude/skills/`.

| Skill | Scope | Description |
| ----- | ----- | ----------- |
| `profiling` | Generic | AMD GPU profiling with rocprofv3 (tracing, counters, Perfetto) |
| `energy-profiling` | Generic | Energy consumption profiling with Omnistat |
| `lumi-resource-management` | LUMI | Project quotas, billing units, storage allocations |
| `lumi-slurm-job-management` | LUMI | Slurm job submission, partitions, containers, Python/PyTorch jobs |
| `geak-optimization` | Generic (opt-in) | GEAK GPU kernel optimizer; not copied unless enabled at setup |

`geak-optimization` is opt-in: the setup wizard asks before copying it, or add
`--with-skill geak-optimization` when you init a project. Local mode:

```bash
hpc-assistant-local-setup --with-skill geak-optimization \
  --remote-host lumi \
  --remote-root /scratch/project_465000000/myuser/myapp \
  --account project_465000000
```

On the login node:

```bash
hpc-assistant-init --with-skill geak-optimization
```

Enabling it copies the skill's instructions into the agent; it does not install GEAK
itself. Once enabled, ask the agent to use it — e.g. *"Use GEAK to optimize kernel.hip"* —
and it installs GEAK, builds the environment, and submits the optimization job.

To add a site: create a directory under `skills/`, write its `SKILL.md`, add the skill name
to `get_site_config` in `setup/config.sh` (`SITE_SKILLS` for always-on, `SITE_OPTIONAL_SKILLS`
for opt-in), and re-run setup.

## Documentation

| Document | Contents |
| -------- | -------- |
| [docs/LOCAL-MODE.md](docs/LOCAL-MODE.md) | Running the agent on your own machine: full setup, the policy file, current limitations |
| [docs/on-cluster-setup.md](docs/on-cluster-setup.md) | Login-node setup — environment module, wizard, manual — plus stopping services and teardown |
| [docs/backends.md](docs/backends.md) | Inference backends (Aitta, Anthropic, custom OpenAI-compatible), knowledge-base backends, framework support |
| [docs/safety.md](docs/safety.md) | Prompt-level rules, the OpenCode permission model, and what it cannot do |
| [hpc-mcp/README.md](hpc-mcp/README.md) | The MCP bridge: tool reference and design |

Repository layout:

```text
skills/        Agent skills, copied into your project by setup, or installed as a plugin
.claude-plugin/, .codex-plugin/, .cursor-plugin/, .agents/plugins/
               Plugin + marketplace manifests (skills-only install)
setup/         setup.sh, launch.sh, teardown.sh, config.sh, templates/, dockerfiles/
scripts/       hpc-assistant-init, hpc-assistant-local-setup
hpc-mcp/       MCP bridge: bounded cluster tools over your own SSH connection
modulefiles/   Lmod module for site installs
docs/          The documents listed above
```

Setup writes into *your project directory*, not the repository: `opencode.json` +
`AGENTS.md` + `.opencode/skills/` for OpenCode, `CLAUDE.md` + `.claude/skills/` for Claude
Code, and `.hpc-assistant.json` — the policy boundary — in local mode.

## How it works

On the login node, the agent is an ordinary process with skills, a knowledge-base MCP and a
permission block.

In local mode it instead reaches the cluster through [`hpc-mcp`](hpc-mcp/), a small MCP
server on your machine that exposes cluster operations as bounded tools: scheduler commands
under their own names, `run_on_compute` for anything that computes, and depth-limited file
access. It refuses compute on the login node and recursive traversal of the parallel
filesystem, naming the alternative when it does. After a submit it reports the job id and
stops, checking the queue only when you ask. Your `.hpc-assistant.json` is the policy
boundary it enforces, and init additionally sets the agent harness to ask before every
`hpc` MCP tool (`permission.hpc_*` in `opencode.json`; Claude Code's equivalent in
`.claude/settings.json`). Files move by **rsync**: setup writes a `sync-to-remote.sh` into
the project, which dry-runs by default and never passes `--delete`.

Safety is enforced through the system prompt and, on OpenCode, a granular permission block
— `rm -rf` and administrative commands are denied, job submission and edits require
approval, and whole-tree walks like `du` are gated because every entry visited is a request
to a shared metadata server. The honest limits of that approach, and the full rule table,
are in [docs/safety.md](docs/safety.md).

## Configuration

Stored in `~/.config/hpc-assistant/`:

| File | Contents |
| ---- | -------- |
| `config.env` | Site, framework, KB backend, paths, proxy host/port, custom endpoint URL and model |
| `credentials.env` | API keys and tokens (mode 600) |
| `logs/` | Service logs |

In local mode the per-project `.hpc-assistant.json` holds the remote host, the project root
and the bounds the assistant honours. For stopping services and full teardown, see
[docs/on-cluster-setup.md](docs/on-cluster-setup.md).


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


## Open Source Compliance

This repository follows AMD's open-source software release requirements.
Compliance artifacts:

| File                                               | Purpose                                                     |
| -------------------------------------------------- | ----------------------------------------------------------- |
| [LICENSE.TXT](LICENSE.TXT)                         | License summary for AMD-authored and third-party components |
| [LICENSE](LICENSE)                                 | MIT license text for AMD-authored software                  |
| [THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt) | Third-party copyright and license notices                   |
| [SOURCE-MANIFEST.md](SOURCE-MANIFEST.md)           | AMD-authored sources and third-party component inventory    |

AMD-authored source files include SPDX license identifiers in their headers.
Container images distributed via Docker Hub, and the local-mode virtualenv
created by `hpc-assistant-local-setup`, include third-party components documented
in `THIRD-PARTY-NOTICES.txt`.

## License

AMD-authored software in this project is licensed under the
[MIT License](LICENSE). See [LICENSE.TXT](LICENSE.TXT) for the full license
summary including third-party components.