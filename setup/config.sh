#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# Shared configuration for HPC Assistant setup and launch scripts.
# Sourced by setup.sh and launch.sh — do not execute directly.

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SETUP_DIR="${SETUP_DIR:-$PROJECT_DIR/setup}"
SKILLS_DIR="${SKILLS_DIR:-$PROJECT_DIR/skills}"

# GEAK: bundled assets live under geak/ at repo root; skills live under skills/.
# GEAK_DIR must always resolve from this file's location (not $PROJECT_DIR) so
# hpc-assistant-init can install into an arbitrary project directory.
GEAK_SKILLS="optimize-kernel check-geak-job cancel-geak-job"
GEAK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/geak"
# shellcheck source=../geak/download-notice.sh
source "$GEAK_DIR/download-notice.sh"

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/hpc-assistant"
CONFIG_FILE="$CONFIG_DIR/config.env"
CREDENTIALS_FILE="$CONFIG_DIR/credentials.env"
LOG_DIR="$CONFIG_DIR/logs"

DEFAULT_DATA_DIR="$HOME/.local/share/hpc-assistant"
DEFAULT_SIF_DIR="$DEFAULT_DATA_DIR/sif"
DEFAULT_CHROMA_DATA_DIR="$DEFAULT_DATA_DIR/chroma_data"
DEFAULT_DOCS_DIR="$DEFAULT_DATA_DIR/docs"

CONTAINER_CHROMA_MOUNT="/workspace/chroma_data"
CONTAINER_DOCS_MOUNT="/workspace/docs"

# Custom OpenAI-compatible endpoint (own or local LLM API).
DEFAULT_CUSTOM_API_BASE="http://localhost:8000/v1"
# OpenAI client libraries require a non-empty key even when the server ignores it.
PLACEHOLDER_API_KEY="no-key-required"

# ── Container runtime detection ───────────────────────────────────────────────
detect_container_runtime() {
  if command -v apptainer &>/dev/null; then
    CONTAINER_RT="apptainer"
  elif command -v singularity &>/dev/null; then
    CONTAINER_RT="singularity"
  else
    echo "ERROR: Neither singularity nor apptainer found in PATH." >&2
    return 1
  fi
}

# ── HPC site configuration ───────────────────────────────────────────────────
# Sets: SITE_DOCS_REPO, SITE_DOCS_SUBPATH, SITE_DOCS_GLOB, SITE_DOCS_FILE_TYPE,
#       SITE_COLLECTION, SITE_SKILLS, and (optionally) SITE_HAS_REMOTE_MCP,
#       SITE_REMOTE_MCP_NAME, SITE_REMOTE_MCP_URL, SITE_REMOTE_MCP_TOOL,
#       SITE_REMOTE_MCP_DESCRIPTION
get_site_config() {
  local site="$1"

  # Reset optional remote-MCP fields so a previous site's values don't leak.
  SITE_HAS_REMOTE_MCP="false"
  SITE_REMOTE_MCP_NAME=""
  SITE_REMOTE_MCP_URL=""
  SITE_REMOTE_MCP_TOOL=""
  SITE_REMOTE_MCP_DESCRIPTION=""

  case "$site" in
    LUMI)
      SITE_DOCS_REPO="https://github.com/Lumi-supercomputer/lumi-userguide.git"
      SITE_DOCS_SUBPATH="docs"
      SITE_DOCS_GLOB="**/*.md"
      SITE_DOCS_FILE_TYPE="markdown"
      SITE_COLLECTION="LUMI_documentation"
      SITE_SKILLS="profiling lumi-resource-management lumi-slurm-job-management"
      # LUMI AI Factory provides a public, regularly-updated MCP server
      # over the LUMI Docs and LUMI AI Guide knowledge base.
      # https://docs.lumi-supercomputer.eu/laif/software/agent-infrastructure/#mcp-server
      SITE_HAS_REMOTE_MCP="true"
      SITE_REMOTE_MCP_NAME="lumi-aif"
      SITE_REMOTE_MCP_URL="https://lumi-aif-agents.2.rahtiapp.fi/mcp"
      SITE_REMOTE_MCP_TOOL="retrieve_docs"
      SITE_REMOTE_MCP_DESCRIPTION="LUMI AI Factory MCP (LUMI Docs + LUMI AI Guide, managed by CSC)"
      ;;
    *)
      echo "Unknown site: $site" >&2
      return 1
      ;;
  esac
}

# ── Interactive helpers ────────────────────────────────────────────────────────

read_secret() {
  local prompt="$1" result="" char
  printf '%s' "$prompt"
  while IFS= read -rs -n1 char; do
    if [[ -z "$char" ]]; then
      break
    elif [[ "$char" == $'\x7f' || "$char" == $'\b' ]]; then
      if [[ -n "$result" ]]; then
        result="${result%?}"
        printf '\b \b'
      fi
    else
      result+="$char"
      printf '*'
    fi
  done
  echo ""
  eval "$2=\$result"
}

# ── Interactive menus ─────────────────────────────────────────────────────────

select_hpc_site() {
  echo "Select your HPC site:"
  echo "  1) LUMI"
  read -rp "Choice [1]: " choice
  case "$choice" in
    1|"") HPC_SITE="LUMI" ;;
    *) echo "Invalid choice." >&2; exit 1 ;;
  esac
  get_site_config "$HPC_SITE"
}

# Pick where the HPC documentation knowledge base comes from.
# Sets KB_BACKEND to either "remote-mcp" or "local-chroma".
select_knowledge_base() {
  echo ""
  echo "Select the knowledge-base backend:"
  if [[ "${SITE_HAS_REMOTE_MCP:-false}" == "true" ]]; then
    echo "  1) Remote MCP — ${SITE_REMOTE_MCP_DESCRIPTION}"
    echo "       ${SITE_REMOTE_MCP_URL}"
    echo "       (recommended: nothing to build, host, or maintain)"
    echo "  2) Local ChromaDB — clone docs and build a vector index on this system"
    read -rp "Choice [1-2, default 1]: " choice
    case "$choice" in
      ""|1) KB_BACKEND="remote-mcp" ;;
      2)    KB_BACKEND="local-chroma" ;;
      *) echo "Invalid choice." >&2; exit 1 ;;
    esac
  else
    echo "  1) Local ChromaDB — clone docs and build a vector index on this system"
    read -rp "Choice [1]: " choice
    case "$choice" in
      ""|1) KB_BACKEND="local-chroma" ;;
      *) echo "Invalid choice." >&2; exit 1 ;;
    esac
  fi
}

select_inference_backend() {
  echo ""
  echo "Select inference backend:"
  echo "  1) Aitta inference service"
  echo "  2) Anthropic API (direct)"
  echo "  3) Custom OpenAI-compatible endpoint (own/local LLM API)"
  read -rp "Choice [1-3]: " choice
  case "$choice" in
    1)
      INFERENCE_BACKEND="aitta"
      read -rp "Aitta API base URL [https://aitta-api.csc.fi/openai/v1]: " INPUT
      AITTA_API_BASE="${INPUT:-https://aitta-api.csc.fi/openai/v1}"

      read_secret "Aitta API token: " AITTA_TOKEN

      # Known default models are listed in setup/aitta-proxy/config.yaml.
      echo "  (Known models are listed in setup/aitta-proxy/config.yaml)"
      read -rp "Target model [openai/gpt-oss-120b]: " INPUT
      TARGET_MODEL="${INPUT:-openai/gpt-oss-120b}"

      read -rp "Proxy port [8001]: " INPUT
      PROXY_PORT="${INPUT:-8001}"
      ;;
    2)
      INFERENCE_BACKEND="anthropic"
      read_secret "Anthropic API key: " ANTHROPIC_API_KEY
      read -rp "Base URL [https://api.anthropic.com]: " INPUT
      ANTHROPIC_BASE_URL="${INPUT:-https://api.anthropic.com}"
      read -rp "Model [claude-sonnet-4-6]: " INPUT
      ANTHROPIC_MODEL="${INPUT:-claude-sonnet-4-6}"
      ;;
    3)
      INFERENCE_BACKEND="custom-openai"
      echo ""
      echo "Any endpoint exposing the OpenAI /chat/completions API works here"
      echo "(vLLM, llama.cpp, Ollama, TGI, LiteLLM, a site-hosted gateway, ...)."
      echo "Include the API version path, e.g. http://localhost:8000/v1"
      echo ""
      read -rp "API base URL [$DEFAULT_CUSTOM_API_BASE]: " INPUT
      CUSTOM_API_BASE="${INPUT:-$DEFAULT_CUSTOM_API_BASE}"

      echo "  (Leave empty for a local server that does not check the key.)"
      read_secret "API key: " CUSTOM_API_KEY
      CUSTOM_API_KEY="${CUSTOM_API_KEY:-$PLACEHOLDER_API_KEY}"

      # No default: the model id must match what the endpoint actually serves.
      CUSTOM_MODEL=""
      while [[ -z "$CUSTOM_MODEL" ]]; do
        read -rp "Model id (as served by the endpoint): " CUSTOM_MODEL || {
          echo "No model id provided." >&2
          exit 1
        }
      done

      read -rp "Proxy port [8001]: " INPUT
      PROXY_PORT="${INPUT:-8001}"
      ;;
    *) echo "Invalid choice." >&2; exit 1 ;;
  esac
}

# True when the LiteLLM proxy is required: an OpenAI-compatible backend behind an
# agent that only speaks the Anthropic API (i.e. anything but OpenCode, which
# talks to such endpoints directly).
uses_litellm_proxy() {
  case "${INFERENCE_BACKEND:-}" in
    aitta|custom-openai) [[ "${AGENT_FRAMEWORK:-}" != "opencode" ]] ;;
    *) return 1 ;;
  esac
}

# Resolve the active OpenAI-compatible endpoint into backend-agnostic variables:
# ENDPOINT_API_BASE, ENDPOINT_API_KEY, ENDPOINT_MODEL.
resolve_openai_endpoint() {
  case "${INFERENCE_BACKEND:-}" in
    aitta)
      ENDPOINT_API_BASE="${AITTA_API_BASE:-}"
      ENDPOINT_API_KEY="${AITTA_TOKEN:-}"
      ENDPOINT_MODEL="${TARGET_MODEL:-}"
      ;;
    custom-openai)
      ENDPOINT_API_BASE="${CUSTOM_API_BASE:-}"
      ENDPOINT_API_KEY="${CUSTOM_API_KEY:-$PLACEHOLDER_API_KEY}"
      ENDPOINT_MODEL="${CUSTOM_MODEL:-}"
      ;;
    *)
      echo "ERROR: ${INFERENCE_BACKEND:-<unset>} is not an OpenAI-compatible backend." >&2
      return 1
      ;;
  esac

  if [[ -z "$ENDPOINT_API_BASE" || -z "$ENDPOINT_MODEL" ]]; then
    echo "ERROR: incomplete $INFERENCE_BACKEND configuration (base URL and model are required)." >&2
    echo "       Re-run setup.sh, or fix $CONFIG_FILE / $CREDENTIALS_FILE." >&2
    return 1
  fi
}

select_agent_framework() {
  echo ""
  echo "Select agent framework:"
  echo "  1) Claude Code"
  echo "  2) OpenCode"
  echo "  3) Generic (manual agent configuration)"
  read -rp "Choice [1-3]: " choice
  case "$choice" in
    1) AGENT_FRAMEWORK="claude-code" ;;
    2) AGENT_FRAMEWORK="opencode" ;;
    3) AGENT_FRAMEWORK="generic" ;;
    *) echo "Invalid choice." >&2; exit 1 ;;
  esac
}

# ── Credential persistence ────────────────────────────────────────────────────

save_credentials() {
  mkdir -p "$CONFIG_DIR"
  : > "$CREDENTIALS_FILE"
  chmod 600 "$CREDENTIALS_FILE"

  {
    echo "INFERENCE_BACKEND=$INFERENCE_BACKEND"
    case "$INFERENCE_BACKEND" in
      aitta)
        echo "AITTA_API_BASE=$AITTA_API_BASE"
        echo "AITTA_TOKEN=$AITTA_TOKEN"
        echo "TARGET_MODEL=$TARGET_MODEL"
        if [[ "${AGENT_FRAMEWORK:-}" != "opencode" ]]; then
          echo "PROXY_PORT=$PROXY_PORT"
        fi
        ;;
      anthropic)
        echo "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY"
        echo "ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL"
        echo "ANTHROPIC_MODEL=$ANTHROPIC_MODEL"
        ;;
      custom-openai)
        # Base URL and model are written to config.env by save_config (they are
        # not secrets, and hpc-assistant-init persists them without a key).
        echo "CUSTOM_API_KEY=$CUSTOM_API_KEY"
        if [[ "${AGENT_FRAMEWORK:-}" != "opencode" ]]; then
          echo "PROXY_PORT=$PROXY_PORT"
        fi
        ;;
    esac
    if [[ "${GEAK_ENABLED:-false}" == "true" ]] && [[ -n "${GEAK_ANTHROPIC_API_KEY:-}" ]]; then
      echo "GEAK_ANTHROPIC_API_KEY=$GEAK_ANTHROPIC_API_KEY"
    fi
  } >> "$CREDENTIALS_FILE"
}

save_config() {
  mkdir -p "$CONFIG_DIR"
  {
    echo "HPC_SITE=$HPC_SITE"
    echo "AGENT_FRAMEWORK=$AGENT_FRAMEWORK"
    echo "INFERENCE_BACKEND=$INFERENCE_BACKEND"
    echo "KB_BACKEND=${KB_BACKEND:-local-chroma}"
    if [[ "${KB_BACKEND:-local-chroma}" == "local-chroma" ]]; then
      echo "CHROMA_DATA_DIR=${CHROMA_DATA_DIR:-}"
      echo "CHROMA_MCP_SIF=${CHROMA_MCP_SIF:-}"
      echo "BUILD_DB_SIF=${BUILD_DB_SIF:-}"
      echo "DOCS_DIR=${DOCS_DIR:-}"
    else
      echo "REMOTE_MCP_NAME=${SITE_REMOTE_MCP_NAME:-}"
      echo "REMOTE_MCP_URL=${SITE_REMOTE_MCP_URL:-}"
      echo "REMOTE_MCP_TOOL=${SITE_REMOTE_MCP_TOOL:-}"
    fi
    if [[ "$INFERENCE_BACKEND" == "custom-openai" ]]; then
      echo "CUSTOM_API_BASE=${CUSTOM_API_BASE:-}"
      echo "CUSTOM_MODEL=${CUSTOM_MODEL:-}"
    fi
    if [[ "$AGENT_FRAMEWORK" != "opencode" ]]; then
      echo "AITTA_PROXY_SIF=${AITTA_PROXY_SIF:-}"
      echo "PROXY_PORT=${PROXY_PORT:-8001}"
      echo "PROXY_HOST=${PROXY_HOST:-}"
    fi
    echo "GEAK_ENABLED=${GEAK_ENABLED:-false}"
  } > "$CONFIG_FILE"
}

load_config() {
  if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "ERROR: No configuration found at $CONFIG_FILE" >&2
    echo "Run setup.sh first." >&2
    return 1
  fi
  # shellcheck source=/dev/null
  source "$CONFIG_FILE"

  if [[ -f "$CREDENTIALS_FILE" ]]; then
    # shellcheck source=/dev/null
    source "$CREDENTIALS_FILE"
  fi

  get_site_config "$HPC_SITE"

  # Backward-compat: configs written before KB_BACKEND existed are local-chroma.
  KB_BACKEND="${KB_BACKEND:-local-chroma}"
}

# ── Agent project configuration (shared by setup.sh and hpc-assistant-init) ───

prepare_system_prompt_vars() {
  COLLECTION="${COLLECTION:-$SITE_COLLECTION}"
  if [[ "${KB_BACKEND:-local-chroma}" == "remote-mcp" ]]; then
    KB_INSTRUCTION="**Query the knowledge base** — use the \`${SITE_REMOTE_MCP_TOOL}\` tool from the \`${SITE_REMOTE_MCP_NAME}\` MCP server (${SITE_REMOTE_MCP_DESCRIPTION}) for site-specific documentation not covered by skills."
  else
    KB_INSTRUCTION="**Query the knowledge base** — use the chroma MCP tool (collection: \`${COLLECTION}\`) for site-specific documentation not covered by skills."
  fi
}

render_system_prompt() {
  local dest="$1"
  if [[ "${USE_DEFAULTS:-false}" == "true" ]]; then
    cp "$SETUP_DIR/templates/lumi-system-prompt.md" "$dest"
  else
    prepare_system_prompt_vars
    sed "s|{{HPC_SITE}}|${HPC_SITE}|g; s|{{KB_INSTRUCTION}}|${KB_INSTRUCTION}|g" \
      "$SETUP_DIR/templates/system-prompt.md" > "$dest"
  fi
}

render_opencode_json() {
  local dest="$1"
  # A custom endpoint has no pre-baked site template by definition — its URL and
  # model come from the user, so always render from the parameterized template.
  if [[ "${USE_DEFAULTS:-false}" == "true" ]] && [[ "${INFERENCE_BACKEND:-aitta}" != "custom-openai" ]]; then
    case "${INFERENCE_BACKEND:-aitta}" in
      aitta)     cp "$SETUP_DIR/templates/lumi-opencode-aitta.json" "$dest" ;;
      anthropic) cp "$SETUP_DIR/templates/lumi-opencode-anthropic.json" "$dest" ;;
      *)
        echo "ERROR: Unknown inference backend for OpenCode defaults: ${INFERENCE_BACKEND}" >&2
        return 1
        ;;
    esac
    return 0
  fi

  local oc_mcp_block="{}"
  if [[ "${KB_BACKEND:-local-chroma}" == "remote-mcp" ]]; then
    oc_mcp_block="{ \"${SITE_REMOTE_MCP_NAME}\": { \"type\": \"remote\", \"url\": \"${SITE_REMOTE_MCP_URL}\", \"enabled\": true } }"
  elif [[ -n "${CONTAINER_RT:-}" ]] && is_instance_running "chroma-mcp"; then
    oc_mcp_block="{ \"chroma\": { \"type\": \"local\", \"command\": [\"${CONTAINER_RT}\", \"exec\", \"instance://chroma-mcp\", \"uvx\", \"chroma-mcp\", \"--client-type\", \"persistent\", \"--data-dir\", \"${CONTAINER_CHROMA_MOUNT}\"], \"enabled\": true, \"timeout\": 40000 } }"
  fi

  case "${INFERENCE_BACKEND:-aitta}" in
    aitta)
      sed \
        -e "s|{{AITTA_API_BASE}}|${AITTA_API_BASE}|g" \
        -e "s|{{TARGET_MODEL}}|${TARGET_MODEL}|g" \
        -e "s|\"{{OC_MCP_BLOCK}}\"|${oc_mcp_block}|g" \
        "$SETUP_DIR/templates/opencode-aitta.json" > "$dest"
      ;;
    anthropic)
      sed \
        -e "s|{{ANTHROPIC_MODEL}}|${ANTHROPIC_MODEL}|g" \
        -e "s|\"{{OC_MCP_BLOCK}}\"|${oc_mcp_block}|g" \
        "$SETUP_DIR/templates/opencode-anthropic.json" > "$dest"
      ;;
    custom-openai)
      resolve_openai_endpoint || return 1
      sed \
        -e "s|{{CUSTOM_API_BASE}}|${ENDPOINT_API_BASE}|g" \
        -e "s|{{CUSTOM_MODEL}}|${ENDPOINT_MODEL}|g" \
        -e "s|\"{{OC_MCP_BLOCK}}\"|${oc_mcp_block}|g" \
        "$SETUP_DIR/templates/opencode-custom.json" > "$dest"
      ;;
    *)
      echo "ERROR: Unknown inference backend: ${INFERENCE_BACKEND}" >&2
      return 1
      ;;
  esac
}

install_site_skills() {
  local dest_root="$1"
  mkdir -p "$dest_root"

  for skill in $SITE_SKILLS; do
    if [[ -d "$SKILLS_DIR/$skill" ]]; then
      echo "  Installing skill: $skill"
      rm -rf "$dest_root/$skill"
      cp -r "$SKILLS_DIR/$skill" "$dest_root/$skill"
    else
      echo "  WARNING: Skill directory not found: $SKILLS_DIR/$skill" >&2
    fi
  done
}

_should_write_file() {
  local path="$1"
  [[ "${FORCE_CONFIG_OVERWRITE:-false}" == "true" ]] || [[ ! -f "$path" ]]
}

configure_opencode_project() {
  local project_dir="$1"
  local oc_skills_dir="$project_dir/.opencode/skills"

  echo "Configuring OpenCode in: $project_dir"
  install_site_skills "$oc_skills_dir"

  if _should_write_file "$project_dir/AGENTS.md"; then
    render_system_prompt "$project_dir/AGENTS.md"
    echo "  Wrote AGENTS.md"
  else
    echo "  Skipping AGENTS.md (already exists; use --force to overwrite)"
  fi

  if _should_write_file "$project_dir/opencode.json"; then
    render_opencode_json "$project_dir/opencode.json"
    echo "  Wrote opencode.json"
  else
    echo "  Skipping opencode.json (already exists; use --force to overwrite)"
  fi
}

register_claude_remote_mcp() {
  if [[ "${KB_BACKEND:-local-chroma}" != "remote-mcp" ]]; then
    return 0
  fi
  if ! command -v claude &>/dev/null; then
    echo "  WARNING: claude CLI not found — skipping MCP registration." >&2
    return 0
  fi
  echo "  Registering remote MCP '$SITE_REMOTE_MCP_NAME' with Claude Code..."
  claude mcp remove "$SITE_REMOTE_MCP_NAME" 2>/dev/null || true
  claude mcp add --transport http "$SITE_REMOTE_MCP_NAME" "$SITE_REMOTE_MCP_URL"
}

register_claude_chroma_mcp() {
  if ! command -v claude &>/dev/null; then
    echo "  WARNING: claude CLI not found — skipping chroma MCP registration." >&2
    return 0
  fi
  echo "  Registering chroma MCP with Claude Code..."
  claude mcp remove chroma 2>/dev/null || true
  claude mcp add chroma \
    -- "$CONTAINER_RT" exec instance://chroma-mcp \
    uvx chroma-mcp \
    --client-type persistent \
    --data-dir "$CONTAINER_CHROMA_MOUNT"
}

configure_claude_code_project() {
  local project_dir="$1"
  local claude_skills_dir="$project_dir/.claude/skills"

  echo "Configuring Claude Code in: $project_dir"
  install_site_skills "$claude_skills_dir"

  if [[ "${KB_BACKEND:-local-chroma}" == "remote-mcp" ]]; then
    register_claude_remote_mcp
  elif is_instance_running "chroma-mcp"; then
    register_claude_chroma_mcp
  fi

  if _should_write_file "$project_dir/CLAUDE.md"; then
    render_system_prompt "$project_dir/CLAUDE.md"
    echo "  Wrote CLAUDE.md"
  else
    echo "  Skipping CLAUDE.md (already exists; use --force to overwrite)"
  fi
}

save_module_init_config() {
  mkdir -p "$CONFIG_DIR"
  HPC_SITE="${HPC_SITE:-LUMI}"
  AGENT_FRAMEWORK="${AGENT_FRAMEWORK:-opencode}"
  INFERENCE_BACKEND="${INFERENCE_BACKEND:-aitta}"
  KB_BACKEND="${KB_BACKEND:-remote-mcp}"
  GEAK_ENABLED="${GEAK_ENABLED:-false}"
  get_site_config "$HPC_SITE"
  if [[ "${GEAK_ENABLED:-false}" == "true" ]] && [[ "$HPC_SITE" == "LUMI" ]]; then
    SITE_SKILLS="$SITE_SKILLS $GEAK_SKILLS"
  fi
  save_config
}

# ── Service management helpers ────────────────────────────────────────────────

is_instance_running() {
  $CONTAINER_RT instance list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$1"
}

is_proxy_reachable() {
  local host="$1" port="$2"
  # LiteLLM exposes an unauthenticated liveness probe at /health/liveliness.
  curl -sf --connect-timeout 3 "http://${host}:${port}/health/liveliness" > /dev/null 2>&1
}

start_chroma_mcp() {
  if is_instance_running "chroma-mcp"; then
    echo "  chroma-mcp instance already running."
    return 0
  fi
  echo "  Starting chroma-mcp instance..."
  $CONTAINER_RT instance start \
    --bind "$CHROMA_DATA_DIR:$CONTAINER_CHROMA_MOUNT" \
    "$CHROMA_MCP_SIF" chroma-mcp
}

# Starts the LiteLLM proxy (aitta-proxy.sif) in front of the configured
# OpenAI-compatible endpoint — Aitta or a user-supplied one. The container reads
# AITTA_* variable names, so the resolved endpoint is passed under those names;
# the image itself is generic and needs no rebuild for a custom endpoint.
start_inference_proxy() {
  local host port
  host="$(hostname)"
  port="${PROXY_PORT:-8001}"

  resolve_openai_endpoint || return 1

  if is_proxy_reachable "$host" "$port"; then
    echo "  aitta-proxy already reachable on ${host}:${port}."
    PROXY_HOST="$host"
    return 0
  fi

  if [[ -n "${PROXY_HOST:-}" ]] && is_proxy_reachable "$PROXY_HOST" "$port"; then
    echo "  aitta-proxy reachable on saved host ${PROXY_HOST}:${port}."
    return 0
  fi

  echo "  Starting aitta-proxy (LiteLLM -> ${ENDPOINT_API_BASE}) on ${host}:${port}..."

  if is_instance_running "aitta-proxy"; then
    $CONTAINER_RT instance stop aitta-proxy 2>/dev/null || true
  fi

  # LiteLLM routes by provider prefix. Use `hosted_vllm/` (not `openai/`) for
  # OpenAI-compatible backends, for two reasons:
  #   1. The `openai` provider routes Anthropic /v1/messages through the OpenAI
  #      Responses API (/responses), which such backends rarely implement;
  #      hosted_vllm goes straight to /chat/completions.
  #   2. The `openai` provider strips a leading `openai/` from the model name,
  #      mangling ids like `openai/gpt-oss-120b` into `gpt-oss-120b`.
  #      hosted_vllm forwards the model id verbatim.
  local litellm_model="hosted_vllm/${ENDPOINT_MODEL}"

  # Belt-and-suspenders: also force the chat-completions bridge in case someone
  # switches back to an `openai/`-prefixed model. Harmless for hosted_vllm.
  # See BerriAI/litellm#28756 (the YAML setting is not reliably loaded).
  local force_chat="LITELLM_USE_CHAT_COMPLETIONS_URL_FOR_ANTHROPIC_MESSAGES=true"

  $CONTAINER_RT instance start \
    --env "AITTA_API_BASE=$ENDPOINT_API_BASE" \
    --env "AITTA_TOKEN=$ENDPOINT_API_KEY" \
    --env "LITELLM_MODEL=$litellm_model" \
    --env "$force_chat" \
    "$AITTA_PROXY_SIF" aitta-proxy

  nohup $CONTAINER_RT exec \
    --env "AITTA_API_BASE=$ENDPOINT_API_BASE" \
    --env "AITTA_TOKEN=$ENDPOINT_API_KEY" \
    --env "LITELLM_MODEL=$litellm_model" \
    --env "$force_chat" \
    instance://aitta-proxy \
    litellm --config /app/config.yaml --host 0.0.0.0 --port "$port" \
    > "$LOG_DIR/aitta-proxy.log" 2>&1 &
  disown

  echo "  Waiting for aitta-proxy to become ready..."
  local attempts=0
  while [[ $attempts -lt 60 ]]; do
    if is_proxy_reachable "$host" "$port"; then
      PROXY_HOST="$host"
      echo "  aitta-proxy running on ${host}:${port}"
      return 0
    fi
    sleep 1
    attempts=$((attempts + 1))
  done

  echo "  WARNING: aitta-proxy did not respond within 60s. Check $LOG_DIR/aitta-proxy.log" >&2
  PROXY_HOST="$host"
}

stop_services() {
  echo "Stopping services..."
  if is_instance_running "aitta-proxy"; then
    $CONTAINER_RT instance stop aitta-proxy && echo "  Stopped aitta-proxy."
  fi
  if is_instance_running "chroma-mcp"; then
    $CONTAINER_RT instance stop chroma-mcp && echo "  Stopped chroma-mcp."
  fi
}

# ── GEAK (Claude Code) installation ───────────────────────────────────────────
# Copies the geak-monitor subagent, hooks, and runtime files into the project.
# Skill directories themselves are copied by the regular skill loop in setup.sh
# once GEAK_SKILLS is appended to SITE_SKILLS.
install_geak_claude() {
  local project_dir="${1:-$PROJECT_DIR}"
  local agents_dir="$project_dir/.claude/agents"
  local hooks_dir="$project_dir/.claude/hooks"

  if [[ ! -d "$GEAK_DIR" ]]; then
    echo "  ERROR: GEAK assets directory not found at $GEAK_DIR" >&2
    return 1
  fi

  mkdir -p "$agents_dir" "$hooks_dir"

  echo "  Installing geak-monitor subagent..."
  cp "$GEAK_DIR/claude/agents/geak-monitor.md" "$agents_dir/geak-monitor.md"

  echo "  Installing GEAK hooks..."
  cp "$GEAK_DIR/claude/hooks/"*.sh "$hooks_dir/"
  chmod +x "$hooks_dir/"*.sh

  echo "  Copying GEAK runtime files to project root..."
  for f in run-geak.sbatch setup-geak.sh commandment-config.yaml matmul_kernel.py; do
    cp "$GEAK_DIR/$f" "$project_dir/$f"
  done
  chmod +x "$project_dir/run-geak.sbatch" "$project_dir/setup-geak.sh"
  install_geak_download_notice_files "$project_dir"

  if [[ -n "${GEAK_ANTHROPIC_API_KEY:-}" ]]; then
    echo "  Writing $project_dir/.anthropic_api_key (mode 600)..."
    umask 077
    printf '%s' "$GEAK_ANTHROPIC_API_KEY" > "$project_dir/.anthropic_api_key"
    chmod 600 "$project_dir/.anthropic_api_key"
  fi
}

# Writes the full hook-aware .claude/settings.json (PreToolUse Bash/Edit/Agent
# + SubagentStop matcher) used when GEAK is enabled. Overwrites the minimal
# settings.json that setup.sh writes for plain Claude Code.
write_geak_claude_settings() {
  local project_dir="${1:-$PROJECT_DIR}"
  cat > "$project_dir/.claude/settings.json" <<'SETTINGS_EOF'
{
  "env": {
    "BASH_DEFAULT_TIMEOUT_MS": "1800000",
    "BASH_MAX_TIMEOUT_MS": "7200000"
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": ".claude/hooks/enforce-geak-bash.sh",
            "timeout": 30000
          }
        ]
      },
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": ".claude/hooks/protect-scripts.sh",
            "timeout": 30000
          }
        ]
      },
      {
        "matcher": "Agent",
        "hooks": [
          {
            "type": "command",
            "command": ".claude/hooks/guard-monitor-spawn.sh",
            "timeout": 30000
          }
        ]
      }
    ],
    "SubagentStop": [
      {
        "matcher": "geak-monitor",
        "hooks": [
          {
            "type": "command",
            "command": ".claude/hooks/cleanup-monitor.sh",
            "timeout": 30000
          }
        ]
      }
    ]
  }
}
SETTINGS_EOF
}

# ── GEAK (OpenCode) installation ───────────────────────────────────────────────
# Copies the geak-monitor subagent and plugin into the project's .opencode/ tree.
# Skill directories are copied by the regular skill loop in setup.sh.
install_geak_opencode() {
  local project_dir="${1:-$PROJECT_DIR}"
  local agents_dir="$project_dir/.opencode/agents"
  local plugins_dir="$project_dir/.opencode/plugins"

  if [[ ! -d "$GEAK_DIR" ]]; then
    echo "  ERROR: GEAK assets directory not found at $GEAK_DIR" >&2
    return 1
  fi

  mkdir -p "$agents_dir" "$plugins_dir"

  echo "  Installing geak-monitor subagent..."
  cp "$GEAK_DIR/opencode/agents/geak-monitor.md" "$agents_dir/geak-monitor.md"

  echo "  Installing GEAK plugin..."
  # Only the TypeScript source is deployed. No bun install needed:
  # - OpenCode loads local file plugins using its own embedded Bun runtime.
  # - `import type { Plugin }` is a type-only import erased at load time.
  # - npm packages listed in opencode.json use @npmcli/arborist internally,
  #   not a system bun binary.
  cp "$GEAK_DIR/opencode/plugins/geak-guards.ts" "$plugins_dir/geak-guards.ts"

  echo "  Copying GEAK runtime files to project root..."
  for f in run-geak.sbatch setup-geak.sh commandment-config.yaml matmul_kernel.py; do
    cp "$GEAK_DIR/$f" "$project_dir/$f"
  done
  chmod +x "$project_dir/run-geak.sbatch" "$project_dir/setup-geak.sh"
  install_geak_download_notice_files "$project_dir"

  if [[ -n "${GEAK_ANTHROPIC_API_KEY:-}" ]]; then
    echo "  Writing $project_dir/.anthropic_api_key (mode 600)..."
    umask 077
    printf '%s' "$GEAK_ANTHROPIC_API_KEY" > "$project_dir/.anthropic_api_key"
    chmod 600 "$project_dir/.anthropic_api_key"
  fi
}

# Adds the GEAK plugin to opencode.json by injecting a "plugin" key.
# Reads the existing opencode.json, injects the plugin entry, and writes it back.
write_geak_opencode_config() {
  local project_dir="${1:-$PROJECT_DIR}"
  local oc_config="$project_dir/opencode.json"

  if [[ ! -f "$oc_config" ]]; then
    echo "  ERROR: opencode.json not found at $oc_config" >&2
    return 1
  fi

  # Inject "plugin": [".opencode/plugins/geak-guards.ts"] into opencode.json.
  # Uses Python (available on LUMI) to parse and rewrite JSON cleanly.
  python3 - "$oc_config" <<'PYEOF'
import json, sys
path = sys.argv[1]
with open(path) as f:
    cfg = json.load(f)
plugins = cfg.get("plugin", [])
entry = ".opencode/plugins/geak-guards.ts"
if entry not in plugins:
    plugins.append(entry)
cfg["plugin"] = plugins
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
PYEOF
  echo "  Added geak-guards.ts to opencode.json plugin list"
}

# Install GEAK hooks, agents, runtime files, and framework-specific wiring into
# project_dir (same steps as setup.sh when GEAK_ENABLED=true).
install_geak_into_project() {
  local project_dir="${1:?install_geak_into_project: project_dir required}"

  if [[ "${GEAK_ENABLED:-false}" != "true" ]]; then
    return 0
  fi

  echo "── Installing GEAK kernel-optimization integration ──"
  case "${AGENT_FRAMEWORK:-}" in
    claude-code)
      install_geak_claude "$project_dir"
      write_geak_claude_settings "$project_dir"
      echo "  Updated .claude/settings.json (with GEAK hooks)"
      ;;
    opencode)
      install_geak_opencode "$project_dir"
      write_geak_opencode_config "$project_dir"
      echo "  GEAK plugin registered in opencode.json"
      ;;
    *)
      echo "  WARNING: GEAK requires claude-code or opencode; skipping." >&2
      ;;
  esac
}
