# Backends

Two independent choices: where the model runs (**inference**) and where documentation
lookups go (**knowledge base**). Both are picked during setup and persisted to
`~/.config/hpc-assistant/config.env`.

## Inference backends

### Aitta — recommended for LUMI

[Aitta](https://docs.lumi-supercomputer.eu/laif/inference/aitta/) is CSC's inference
service. OpenCode speaks the OpenAI API natively and connects to it directly, so there is
no proxy and no container on that path.

Claude Code only speaks the Anthropic API, so it goes through the Aitta proxy: the
official [LiteLLM](https://docs.litellm.ai/) gateway with our config baked in, pinned to a
vetted release (see `../setup/dockerfiles/aitta-proxy.dockerfile`). It runs detached as a
Singularity instance on the login node.

> **The model must be running before you can use it.** If it is not, your first request
> triggers Aitta to allocate compute and load it, which can take several minutes.

**Configuration.** LiteLLM reads `AITTA_API_BASE`, `AITTA_TOKEN` and `LITELLM_MODEL` from
the environment at start, so the published image contains no secrets, URLs or model names.
It exposes the backend under the model name `aitta`, which is what `ANTHROPIC_MODEL=aitta`
points Claude Code at.

**Choosing a model.** Set `TARGET_MODEL` through the wizard or your credentials file; the
launch scripts inject it at runtime, so no rebuild is needed. Known defaults live in
[`setup/aitta-proxy/config.yaml`](../setup/aitta-proxy/config.yaml) — edit that file and
rebuild the SIF to change the baked-in default.

**Login nodes.** The proxy binds to `0.0.0.0` so it is reachable from any login node, and
the launch script detects which node it is on, reconnecting or restarting as needed.

### Anthropic API

A direct connection. Requires an API key and network access from wherever the agent runs.

### Custom OpenAI-compatible endpoint

`--inference custom-openai` works with anything serving the OpenAI `/chat/completions`
API: a vLLM or llama.cpp server you started yourself (including inside a SLURM job),
Ollama, TGI, a site gateway, or a commercial provider. Nothing about the endpoint is baked
into this repository or the images — you supply three values:

| Value | Stored in | Notes |
| ----- | --------- | ----- |
| Base URL (`CUSTOM_API_BASE`) | `config.env` | Include the version path, e.g. `http://localhost:8000/v1` |
| Model id (`CUSTOM_MODEL`) | `config.env` | Must match what the endpoint serves; there is no default |
| API key (`CUSTOM_API_KEY`) | `credentials.env` (mode 600) | Optional; a placeholder is stored for servers that do not check it |

```bash
# Wizard: answer "n" to the defaults prompt, then pick option 3
bash setup/setup.sh

# Or without prompts:
hpc-assistant-init --inference custom-openai \
  --api-base http://localhost:8000/v1 --model my-model
```

**OpenCode** connects directly through the `@ai-sdk/openai-compatible` provider — no proxy,
no container. The URL and model are written into `opencode.json` and only the key is passed
at launch, so changing either means editing `opencode.json` or re-running setup.

**Claude Code** goes through the same LiteLLM proxy used for Aitta, so this path needs
`aitta-proxy.sif`. Requests are routed with the `hosted_vllm/` provider prefix, which is
correct for vLLM and most self-hosted OpenAI-compatible servers.

> **Reachability:** the endpoint must be reachable from wherever the agent runs. A model
> served inside a SLURM job needs the compute node's hostname and an open port, not
> `localhost`.

## Knowledge base backends

The assistant queries a RAG knowledge base of HPC documentation over MCP. The choice is
persisted as `KB_BACKEND` and changes which tools the agent is given.

### Remote MCP — recommended for LUMI

Where a site exposes a managed MCP server, setup simply registers it. Nothing is cloned to
the login node, no index is built, and no `chroma-mcp` instance runs.

LUMI uses the LUMI AI Factory MCP, maintained by CSC and continuously updated from the LUMI
Docs and the LUMI AI Guide:

- URL: `https://lumi-aif-agents.2.rahtiapp.fi/mcp`
- Tool: `retrieve_docs`
- Source: [LUMI AIF — infrastructure for AI agents](https://docs.lumi-supercomputer.eu/laif/software/agent-infrastructure/#mcp-server)

What setup wires up:

- **Claude Code** — `claude mcp add --transport http lumi-aif https://lumi-aif-agents.2.rahtiapp.fi/mcp`
- **OpenCode** — an `mcp.lumi-aif` block of `{ "type": "remote", "url": "..." }` in `opencode.json`
- **Generic** — point your own client at the URL, e.g.
  `fastmcp call https://lumi-aif-agents.2.rahtiapp.fi/mcp retrieve_docs 'query=...' 'k=2'`

The generated system prompt instructs the model to call `retrieve_docs` before falling back
on general knowledge.

### Local ChromaDB

Clones the site's documentation repository, chunks and embeds it with `build-db.sif`, and
serves it over MCP from a `chroma-mcp.sif` Singularity instance. Worth it when:

- the site has no managed MCP yet, or
- you want to extend or replace the knowledge base with private or internal documentation.

Needs the `build-db` and `chroma-mcp` images — see
[pull container images](on-cluster-setup.md#1-pull-container-images). The documentation
clone goes to `$DOCS_DIR`, which defaults to `~/.local/share/hpc-assistant/docs`.

## Agent framework support

### Claude Code

- Skills from `skills/` are copied to `.claude/skills/`
- The knowledge-base MCP is registered (remote, or chroma on the local path)
- A `CLAUDE.md` is written with the assistant instructions
- The launch script sets the environment and starts `claude`

Use from any project directory with `claude --add-dir /path/to/hpc-assistant`.

### OpenCode

- Skills from `skills/` are copied to `.opencode/skills/`
- An `AGENTS.md` is written with the assistant instructions
- An `opencode.json` is generated with the provider configuration, MCP registration, model
  selection and the permission rules described in [safety.md](safety.md)
- The launch script exports the token and starts `opencode`

With Aitta this is one fewer moving part than Claude Code: OpenCode connects straight to
the endpoint, so there is no proxy container to manage.

### Generic / other frameworks

Skills are plain markdown under `skills/`, and the services run independently. Configure
your framework to use the knowledge-base MCP for RAG queries, read the skill files for
instruction context, and — if using Aitta with an Anthropic-format client — point it at the
proxy. The proxy accepts Anthropic-format requests at `/v1/messages` with model name
`aitta`, and OpenAI-format requests at `/v1/chat/completions`.
