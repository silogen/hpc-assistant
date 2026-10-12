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

# The MCP bridge must resolve from this file's location, not $PROJECT_DIR, because
# hpc-assistant-init installs into an arbitrary project directory.
HPC_MCP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/hpc-mcp"

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
#       SITE_COLLECTION, SITE_SKILLS, SITE_OPTIONAL_SKILLS, and (optionally)
#       SITE_HAS_REMOTE_MCP, SITE_REMOTE_MCP_NAME, SITE_REMOTE_MCP_URL,
#       SITE_REMOTE_MCP_TOOL, SITE_REMOTE_MCP_DESCRIPTION
get_site_config() {
  local site="$1"

  # Reset optional remote-MCP fields so a previous site's values don't leak.
  SITE_HAS_REMOTE_MCP="false"
  SITE_REMOTE_MCP_NAME=""
  SITE_REMOTE_MCP_URL=""
  SITE_REMOTE_MCP_TOOL=""
  SITE_REMOTE_MCP_DESCRIPTION=""
  SITE_OPTIONAL_SKILLS=""

  case "$site" in
    LUMI)
      SITE_DOCS_REPO="https://github.com/Lumi-supercomputer/lumi-userguide.git"
      SITE_DOCS_SUBPATH="docs"
      SITE_DOCS_GLOB="**/*.md"
      SITE_DOCS_FILE_TYPE="markdown"
      SITE_COLLECTION="LUMI_documentation"
      SITE_SKILLS="profiling energy-profiling lumi-resource-management lumi-slurm-job-management"
      SITE_OPTIONAL_SKILLS="geak-optimization"
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
      read -rp "Target model [Qwen/Qwen3.6-27B]: " INPUT
      TARGET_MODEL="${INPUT:-Qwen/Qwen3.6-27B}"

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
    echo "ENABLED_OPTIONAL_SKILLS=${ENABLED_OPTIONAL_SKILLS:-}"
    echo "LOCAL_MODE=${LOCAL_MODE:-false}"
    if [[ "${LOCAL_MODE:-false}" == "true" ]]; then
      echo "REMOTE_HOST=${REMOTE_HOST:-}"
      echo "REMOTE_ROOT=${REMOTE_ROOT:-}"
      echo "SLURM_ACCOUNT=${SLURM_ACCOUNT:-}"
      echo "FILE_ACCESS_MODE=${FILE_ACCESS_MODE:-rsync}"
      echo "SSHFS_MOUNTPOINT=${SSHFS_MOUNTPOINT:-}"
    fi
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
  # Configs written before optional skills existed install none of them.
  ENABLED_OPTIONAL_SKILLS="${ENABLED_OPTIONAL_SKILLS:-}"
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

optional_skill_description() {
  case "$1" in
    geak-optimization)
      echo "GEAK GPU kernel optimizer (AMD Instinct). Extra GEAK install + API key; skip for normal HPC jobs."
      ;;
    *)
      echo "optional skill"
      ;;
  esac
}

is_known_optional_skill() {
  local name="$1" skill
  for skill in ${SITE_OPTIONAL_SKILLS:-}; do
    [[ "$skill" == "$name" ]] && return 0
  done
  return 1
}

is_optional_skill_enabled() {
  local name="$1" skill
  for skill in ${ENABLED_OPTIONAL_SKILLS:-}; do
    [[ "$skill" == "$name" ]] && return 0
  done
  return 1
}

enable_optional_skill() {
  local name="$1"
  if ! is_known_optional_skill "$name"; then
    echo "ERROR: '$name' is not an optional skill for ${HPC_SITE:-this site}." >&2
    echo "Available optional skills: ${SITE_OPTIONAL_SKILLS:-<none>}" >&2
    return 1
  fi
  if is_optional_skill_enabled "$name"; then
    return 0
  fi
  ENABLED_OPTIONAL_SKILLS="${ENABLED_OPTIONAL_SKILLS:+$ENABLED_OPTIONAL_SKILLS }$name"
}

# Read ENABLED_OPTIONAL_SKILLS from an existing config.env without sourcing the
# rest of the file (which would clobber flags already parsed by the caller).
load_saved_optional_skills() {
  [[ -f "$CONFIG_FILE" ]] || return 0
  local val skill kept=""
  val="$(grep -E '^ENABLED_OPTIONAL_SKILLS=' "$CONFIG_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' || true)"
  ENABLED_OPTIONAL_SKILLS="${val:-}"
  for skill in ${ENABLED_OPTIONAL_SKILLS:-}; do
    if is_known_optional_skill "$skill"; then
      kept="${kept:+$kept }$skill"
    else
      echo "WARNING: ignoring unknown optional skill '$skill'" >&2
    fi
  done
  ENABLED_OPTIONAL_SKILLS="$kept"
}

# Interactive opt-in for SITE_OPTIONAL_SKILLS. Defaults to the current
# ENABLED_OPTIONAL_SKILLS value (empty unless load_saved_optional_skills ran).
select_optional_skills() {
  ENABLED_OPTIONAL_SKILLS="${ENABLED_OPTIONAL_SKILLS:-}"
  if [[ -z "${SITE_OPTIONAL_SKILLS:-}" ]]; then
    ENABLED_OPTIONAL_SKILLS=""
    return 0
  fi

  echo "── Optional skills ──"
  echo "These are not copied into the agent unless you enable them."
  echo ""

  local skill choice default_yes default_prompt selected=""
  for skill in $SITE_OPTIONAL_SKILLS; do
    default_yes=false
    default_prompt="y/N"
    if is_optional_skill_enabled "$skill"; then
      default_yes=true
      default_prompt="Y/n"
    fi
    echo "  $skill — $(optional_skill_description "$skill")"
    read -rp "  Enable $skill? [$default_prompt]: " choice
    case "$choice" in
      Y|y) selected="${selected:+$selected }$skill" ;;
      N|n) ;;
      "")
        if [[ "$default_yes" == "true" ]]; then
          selected="${selected:+$selected }$skill"
        fi
        ;;
      *) echo "Invalid choice." >&2; exit 1 ;;
    esac
    echo ""
  done
  ENABLED_OPTIONAL_SKILLS="$selected"
}

_copy_skill() {
  local dest_root="$1" skill="$2"
  if [[ -d "$SKILLS_DIR/$skill" ]]; then
    echo "  Installing skill: $skill"
    rm -rf "$dest_root/$skill"
    cp -r "$SKILLS_DIR/$skill" "$dest_root/$skill"
  else
    echo "  WARNING: Skill directory not found: $SKILLS_DIR/$skill" >&2
  fi
}

install_site_skills() {
  local dest_root="$1"
  mkdir -p "$dest_root"

  local skill
  for skill in $SITE_SKILLS; do
    _copy_skill "$dest_root" "$skill"
  done

  for skill in ${SITE_OPTIONAL_SKILLS:-}; do
    if is_optional_skill_enabled "$skill"; then
      _copy_skill "$dest_root" "$skill"
    elif [[ -d "$dest_root/$skill" ]]; then
      echo "  Removing disabled optional skill: $skill"
      rm -rf "$dest_root/$skill"
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

# ── Local mode (agent on the user's machine, cluster over SSH) ─────────────────
# Local mode installs the same skills and system prompt into a project directory on
# the user's own machine, plus a .hpc-assistant.json declaring the remote host,
# project root and the bounds the assistant honours. See LOCAL-MODE.md.

# Only rsync is supported. git and sshfs were designed and partly implemented but never
# tested end to end, so they are not offered rather than shipped unverified. Re-adding one
# means listing it here, restoring its block in templates/hpc-assistant-local.json, adding
# it to FILE_ACCESS_MODES in hpc-mcp/src/hpc_mcp/config.py, and documenting it in
# LOCAL-MODE.md and the local-mode prompt.
LOCAL_MODE_FILE_ACCESS_MODES="rsync"

local_mode_defaults() {
  LOCAL_ROOT="${LOCAL_ROOT:-$PWD}"
  REMOTE_HOST="${REMOTE_HOST:-lumi}"
  REMOTE_ROOT="${REMOTE_ROOT:-}"
  SLURM_ACCOUNT="${SLURM_ACCOUNT:-}"
  FILE_ACCESS_MODE="${FILE_ACCESS_MODE:-rsync}"
  SSHFS_MOUNTPOINT="${SSHFS_MOUNTPOINT:-$HOME/mnt/$REMOTE_HOST}"
  DEFAULT_PARTITION="${DEFAULT_PARTITION:-small-g}"
}

validate_file_access_mode() {
  local mode="$1"
  for known in $LOCAL_MODE_FILE_ACCESS_MODES; do
    if [[ "$mode" == "$known" ]]; then
      return 0
    fi
  done
  echo "ERROR: Unknown file access mode: $mode" >&2
  echo "       Expected one of: ${LOCAL_MODE_FILE_ACCESS_MODES// /, }" >&2
  return 1
}

# Advisory checks only. Init must still succeed on a machine whose SSH config is not
# finished yet, so nothing here is fatal — but every warning is something that will
# bite the user later, so each one names the fix.
check_local_prerequisites() {
  local host="$1"

  if ! command -v ssh &>/dev/null; then
    echo "  WARNING: ssh not found in PATH — the assistant cannot reach the cluster." >&2
    return 0
  fi

  local resolved=""
  resolved="$(ssh -G "$host" 2>/dev/null | awk '/^hostname /{print $2; exit}')"
  if [[ -z "$resolved" ]]; then
    echo "  WARNING: could not read SSH config for host '$host'." >&2
  elif [[ "$resolved" == "$host" ]]; then
    echo "  WARNING: '$host' does not look like a configured SSH host alias." >&2
    echo "           Add a Host block for it in ~/.ssh/config — see LOCAL-MODE.md." >&2
  fi

  # ControlMaster multiplexing is what keeps the connection profile defensible: without
  # it every single operation opens and authenticates a new SSH connection.
  local controlmaster=""
  controlmaster="$(ssh -G "$host" 2>/dev/null | awk '/^controlmaster /{print $2; exit}')"
  if [[ -z "$controlmaster" || "$controlmaster" == "false" || "$controlmaster" == "no" ]]; then
    echo "  WARNING: ControlMaster is not enabled for '$host'." >&2
    echo "           Without it, each operation opens a new SSH connection — a worse" >&2
    echo "           login-node profile than running on the cluster. Add to ~/.ssh/config:" >&2
    echo "" >&2
    echo "             Host $host" >&2
    echo "                 ControlMaster auto" >&2
    echo "                 ControlPath ~/.ssh/cm-%r@%h:%p" >&2
    echo "                 ControlPersist 10m" >&2
    echo "" >&2
  fi
}

check_sshfs_prerequisites() {
  local mountpoint="$1" local_root="$2"

  if ! command -v sshfs &>/dev/null; then
    echo "  WARNING: sshfs not found in PATH." >&2
    if [[ "$(uname -s)" == "Darwin" ]]; then
      echo "           On macOS this needs macFUSE, which requires a kernel extension and" >&2
      echo "           (on Apple Silicon) reduced-security approval. See LOCAL-MODE.md." >&2
    else
      echo "           Install it with your package manager, e.g. 'apt install sshfs'." >&2
    fi
  fi

  # A mount inside the workspace looks like ordinary local disk to the agent's own
  # file-search tools, which turns every local search into a metadata storm on the
  # cluster. This is the one mistake that undoes the benefit of running locally.
  case "$mountpoint" in
    "$local_root"|"$local_root"/*)
      echo "  WARNING: the sshfs mountpoint is inside the project directory:" >&2
      echo "             mountpoint : $mountpoint" >&2
      echo "             project    : $local_root" >&2
      echo "           The assistant's file-search tools cannot tell a mount from local" >&2
      echo "           disk, so a recursive search would hammer the cluster filesystem." >&2
      echo "           Move the mountpoint outside the project directory." >&2
      ;;
  esac
}

render_hpc_assistant_config() {
  local dest="$1"
  sed \
    -e "s|{{HPC_SITE}}|${HPC_SITE}|g" \
    -e "s|{{REMOTE_HOST}}|${REMOTE_HOST}|g" \
    -e "s|{{REMOTE_ROOT}}|${REMOTE_ROOT}|g" \
    -e "s|{{SLURM_ACCOUNT}}|${SLURM_ACCOUNT}|g" \
    -e "s|{{DEFAULT_PARTITION}}|${DEFAULT_PARTITION}|g" \
    -e "s|{{FILE_ACCESS_MODE}}|${FILE_ACCESS_MODE}|g" \
    -e "s|{{LOCAL_ROOT}}|${LOCAL_ROOT}|g" \
    -e "s|{{SSHFS_MOUNTPOINT}}|${SSHFS_MOUNTPOINT}|g" \
    "$SETUP_DIR/templates/hpc-assistant-local.json" > "$dest"
}

# Nothing in hpc-mcp moves files, so the project needs a transfer command of its own.
# Rendered rather than documented because the exclude list is the only thing standing
# between a routine push and overwriting job output on the cluster.
render_sync_script() {
  local dest="$1"
  sed \
    -e "s|{{REMOTE_HOST}}|${REMOTE_HOST}|g" \
    -e "s|{{REMOTE_ROOT}}|${REMOTE_ROOT}|g" \
    "$SETUP_DIR/templates/sync-to-remote.sh" > "$dest"
  chmod +x "$dest"
}

# The site prompt describes working on the cluster; local mode changes where work
# belongs. Appending keeps one source of truth for the shared HPC safety rules.
append_local_mode_prompt() {
  local dest="$1"
  sed \
    -e "s|{{REMOTE_HOST}}|${REMOTE_HOST}|g" \
    -e "s|{{REMOTE_ROOT}}|${REMOTE_ROOT}|g" \
    "$SETUP_DIR/templates/local-mode-prompt.md" >> "$dest"
}

# OpenCode evaluates bash permissions with the LAST matching rule winning, so the
# overlay works by appending. Four things change in local mode: every hpc-mcp tool
# requires approval, because those calls are the actual cluster traffic; paths
# outside the local project are denied rather than prompted, so the assistant
# cannot wander the laptop; the login-node template's `ls *` / `find *` allows are
# revoked, because they do not honour external_directory; raw SSH and file
# transfer become approval-gated, because otherwise the agent can reach the
# cluster outside every bound this project declares. Searching the working copy
# is Read/Glob/Grep, which external_directory can actually bound.
apply_local_permission_overlay() {
  local oc_config="$1" file_access_mode="$2"

  if [[ ! -f "$oc_config" ]]; then
    echo "  ERROR: opencode.json not found at $oc_config" >&2
    return 1
  fi

  python3 - "$oc_config" "$file_access_mode" <<'PYEOF'
import json, sys

path, file_access_mode = sys.argv[1], sys.argv[2]
with open(path) as f:
    cfg = json.load(f)

permission = cfg.setdefault("permission", {})
# OpenCode names MCP tools `{server}_{tool}`. The bridge is registered as `hpc`.
permission["hpc_*"] = "ask"
# The project directory is the local bound. "ask" still lets the model try, which
# is the behaviour we do not want: refuse without a prompt.
permission["external_directory"] = "deny"

# Policy files live in the project, so "edit": "ask" lets the model rewrite the
# bound if the user approves. Deny those paths; everything else stays on the
# existing catch-all (ask, unless the template already used an object).
edit = permission.get("edit")
if not isinstance(edit, dict):
    permission["edit"] = {"*": edit if edit in ("allow", "ask", "deny") else "ask"}
    edit = permission["edit"]
elif "*" not in edit:
    edit["*"] = "ask"
POLICY_FILES = (
    "opencode.json",
    ".hpc-assistant.json",
    "AGENTS.md",
    "CLAUDE.md",
)
for name in POLICY_FILES:
    for pattern in (name, f"*/{name}"):
        edit.pop(pattern, None)
        edit[pattern] = "deny"

rules = permission.setdefault("bash", {})

def put(pattern, action):
    """Append a rule, moving it last so it takes precedence."""
    rules.pop(pattern, None)
    rules[pattern] = action

# Reaching the cluster outside the declared bounds stays a human decision.
for cmd in ("ssh", "scp", "sftp", "rsync", "sshfs", "fusermount", "umount"):
    put(f"{cmd} *", "ask")

# Read-only version control on the local checkout is cheap and constant; gating it
# just trains the user to approve without reading. Mutating git stays on "git *".
# Both forms are needed: a glob's "*" cannot match the absent space, so "git status *"
# never matches a bare "git status", which is how it is usually typed.
for sub in ("status", "diff", "log", "show", "branch", "remote -v", "rev-parse"):
    put(f"git {sub}", "allow")
    put(f"git {sub} *", "allow")

# The login-node template allows `ls *`, `cat *`, `find *`, and friends. Those
# globs match `ls /elsewhere`, and a bash allow is what actually ran after
# external_directory denied Read. Deny the path-taking forms; listing the cwd
# has no path argument and cannot leave the project. Subdirectories belong to
# Glob/Read, which do honour external_directory.
for pattern in (
    "ls *",
    "ls -R *",
    "cat *",
    "head *",
    "tail *",
    "wc *",
    "find *",
    "grep *",
    "diff *",
    "du *",
):
    put(pattern, "deny")
for pattern in ("ls", "ls -l", "ls -la", "ls -lh", "ls -1", "ls -al", "ls -F", "ls ."):
    put(pattern, "allow")

# Any other command whose argument is an absolute path, a home path, or `..`.
for pattern in ("* /*", "* ~", "* ~*", "* ~/*", "* ..", "* ../*", "* */..*"):
    put(pattern, "deny")

# Bash must not be a writable back door onto the same files.
for name in POLICY_FILES:
    put(f"* {name}", "deny")
    put(f"* */{name}", "deny")

# Denials are re-appended last so no overlay rule above can ever override them.
for pattern in ("rm -rf *", "chown *", "dd *", "mkfs *", "kill -9 *",
                "killall *", "reboot *", "shutdown *"):
    put(pattern, "deny")

with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
PYEOF
}

# Claude Code's equivalent of the OpenCode overlay above. MCP tools are named
# mcp__{server}__{tool}; `mcp__hpc__*` matches every tool from the bridge.
apply_local_claude_permission_overlay() {
  local settings="$1"
  mkdir -p "$(dirname "$settings")"

  python3 - "$settings" <<'PYEOF'
import json, os, sys

path = sys.argv[1]
cfg = {}
if os.path.exists(path):
    with open(path) as handle:
        loaded = json.load(handle)
    if isinstance(loaded, dict):
        cfg = loaded

perms = cfg.setdefault("permissions", {})
if not isinstance(perms, dict):
    perms = {}
    cfg["permissions"] = perms
ask = perms.get("ask")
if not isinstance(ask, list):
    ask = []
    perms["ask"] = ask
rule = "mcp__hpc__*"
if rule not in ask and "mcp__hpc" not in ask:
    ask.append(rule)
# File tools refuse paths outside the working directory instead of prompting.
perms["blockReadsOutsideWorkingDirectories"] = True
deny = perms.get("deny")
if not isinstance(deny, list):
    deny = []
    perms["deny"] = deny
for rule in (
    "Bash(ls *)",
    "Bash(cat *)",
    "Bash(find *)",
    "Bash(grep *)",
    "Bash(* /*)",
    "Bash(* ..)",
    "Bash(* ../*)",
    "Bash(* ~*)",
    "Edit(opencode.json)",
    "Write(opencode.json)",
    "Edit(.hpc-assistant.json)",
    "Write(.hpc-assistant.json)",
    "Edit(AGENTS.md)",
    "Write(AGENTS.md)",
    "Edit(CLAUDE.md)",
    "Write(CLAUDE.md)",
    "Edit(.claude/settings.json)",
    "Write(.claude/settings.json)",
):
    if rule not in deny:
        deny.append(rule)

with open(path, "w") as handle:
    json.dump(cfg, handle, indent=2)
    handle.write("\n")
PYEOF
}

# The bridge is what makes the bounds in .hpc-assistant.json real rather than advisory,
# so register it as an MCP server for whichever framework is in use. The config path is
# passed explicitly: the server would find it by walking up from its working directory,
# but which directory that is depends on how the agent was launched.
# Builds an array rather than a string: install paths contain spaces often enough that
# splitting a joined command line back apart is a bug waiting to happen.
resolve_hpc_mcp_command() {
  local project_dir="$1" config_path="$project_dir/.hpc-assistant.json"

  if [[ -n "${HPC_MCP_COMMAND:-}" ]]; then
    # An executable named in full is taken as one word, so a path containing a space
    # works — which matters, because the interpreter of a managed virtualenv lands
    # wherever the user keeps their files. Anything else is split on whitespace, which is
    # what makes the multi-word `uvx --from DIR hpc-mcp` form expressible.
    if [[ -x "$HPC_MCP_COMMAND" ]]; then
      HPC_MCP_ARGV=("$HPC_MCP_COMMAND")
    else
      read -r -a HPC_MCP_ARGV <<< "$HPC_MCP_COMMAND"
    fi
  elif command -v hpc-mcp >/dev/null 2>&1; then
    HPC_MCP_ARGV=(hpc-mcp)
  elif command -v uvx >/dev/null 2>&1 && [[ -d "$HPC_MCP_DIR" ]]; then
    HPC_MCP_ARGV=(uvx --from "$HPC_MCP_DIR" hpc-mcp)
  else
    HPC_MCP_ARGV=(hpc-mcp)
    echo "  NOTE: hpc-mcp is not on PATH yet. The bridge is registered anyway;" >&2
    echo "        install it with:  uv tool install '$HPC_MCP_DIR'" >&2
    echo "        or:               pipx install '$HPC_MCP_DIR'" >&2
  fi
  HPC_MCP_ARGV+=(--config "$config_path")
}

register_hpc_mcp_server() {
  local project_dir="$1" framework="${2:-opencode}" target
  local HPC_MCP_ARGV=()

  resolve_hpc_mcp_command "$project_dir"

  case "$framework" in
    claude-code) target="$project_dir/.mcp.json" ;;
    *)           target="$project_dir/opencode.json" ;;
  esac

  if [[ "$framework" != "claude-code" && ! -f "$target" ]]; then
    echo "  ERROR: opencode.json not found at $target" >&2
    return 1
  fi

  python3 - "$target" "$framework" "${HPC_MCP_ARGV[@]}" <<'PYEOF'
import json, os, sys

path, framework = sys.argv[1], sys.argv[2]
argv = sys.argv[3:]

config = {}
if os.path.exists(path):
    with open(path) as handle:
        config = json.load(handle)

if framework == "claude-code":
    servers = config.setdefault("mcpServers", {})
    servers["hpc"] = {"command": argv[0], "args": argv[1:]}
else:
    # An existing knowledge-base server stays; this is an addition, not a replacement.
    servers = config.setdefault("mcp", {})
    if not isinstance(servers, dict):
        servers = {}
        config["mcp"] = servers
    servers["hpc"] = {"type": "local", "command": argv, "enabled": True}

with open(path, "w") as handle:
    json.dump(config, handle, indent=2)
    handle.write("\n")
PYEOF
}

configure_local_project() {
  local project_dir="$1"

  echo "── Configuring local mode ──"
  check_local_prerequisites "$REMOTE_HOST"
  if [[ "$FILE_ACCESS_MODE" == "sshfs" ]]; then
    check_sshfs_prerequisites "$SSHFS_MOUNTPOINT" "$LOCAL_ROOT"
  fi

  if _should_write_file "$project_dir/.hpc-assistant.json"; then
    render_hpc_assistant_config "$project_dir/.hpc-assistant.json"
    echo "  Wrote .hpc-assistant.json"
  else
    echo "  Skipping .hpc-assistant.json (already exists; use --force to overwrite)"
  fi

  if [[ "$FILE_ACCESS_MODE" != "sshfs" ]]; then
    if _should_write_file "$project_dir/sync-to-remote.sh"; then
      render_sync_script "$project_dir/sync-to-remote.sh"
      echo "  Wrote sync-to-remote.sh"
    else
      echo "  Skipping sync-to-remote.sh (already exists; use --force to overwrite)"
    fi
  fi

  local prompt_file
  case "${AGENT_FRAMEWORK:-opencode}" in
    claude-code) prompt_file="$project_dir/CLAUDE.md" ;;
    *)           prompt_file="$project_dir/AGENTS.md" ;;
  esac

  if grep -q '^## Local Mode$' "$prompt_file" 2>/dev/null; then
    echo "  Skipping local-mode section in $(basename "$prompt_file") (already present)"
  else
    append_local_mode_prompt "$prompt_file"
    echo "  Appended local-mode section to $(basename "$prompt_file")"
  fi

  if [[ "${AGENT_FRAMEWORK:-opencode}" == "opencode" ]]; then
    apply_local_permission_overlay "$project_dir/opencode.json" "$FILE_ACCESS_MODE"
    echo "  Applied local-mode permission overlay to opencode.json"
  elif [[ "${AGENT_FRAMEWORK:-opencode}" == "claude-code" ]]; then
    apply_local_claude_permission_overlay "$project_dir/.claude/settings.json"
    echo "  Applied local-mode permission overlay to .claude/settings.json"
  fi

  if register_hpc_mcp_server "$project_dir" "${AGENT_FRAMEWORK:-opencode}"; then
    case "${AGENT_FRAMEWORK:-opencode}" in
      claude-code) echo "  Registered the hpc-mcp bridge in .mcp.json" ;;
      *)           echo "  Registered the hpc-mcp bridge in opencode.json" ;;
    esac
  fi

  mkdir -p "$project_dir/.hpc-assistant"
}

save_module_init_config() {
  mkdir -p "$CONFIG_DIR"
  LOCAL_MODE="${LOCAL_MODE:-false}"
  HPC_SITE="${HPC_SITE:-LUMI}"
  AGENT_FRAMEWORK="${AGENT_FRAMEWORK:-opencode}"
  INFERENCE_BACKEND="${INFERENCE_BACKEND:-aitta}"
  KB_BACKEND="${KB_BACKEND:-remote-mcp}"
  get_site_config "$HPC_SITE"
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
