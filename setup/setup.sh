#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# HPC Assistant — unified setup script.
# Run on a login node. All Python dependencies run inside Singularity containers.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=config.sh
source "$SCRIPT_DIR/config.sh"

echo "╔══════════════════════════════════════════╗"
echo "║   HPC Assistant — Setup                  ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "This script configures the HPC Assistant on a login node."
echo "Press Enter to accept defaults shown in [brackets]."
echo ""

# ── 1. Prerequisites ──────────────────────────────────────────────────────────

detect_container_runtime
echo "Container runtime: $CONTAINER_RT"
echo ""

# ── 2. HPC site selection ────────────────────────────────────────────────────

select_hpc_site
echo ""

# ── 3. Quick-start defaults ──────────────────────────────────────────────────

echo "Default settings for ${HPC_SITE}:"
echo "  Knowledge base : LUMI AI Factory remote MCP (lumi-aif)"
echo "  Inference      : Aitta"
echo "    API base URL : https://aitta-api.csc.fi/openai/v1"
echo "    Model        : openai/gpt-oss-120b"
echo ""
read -rp "Use these defaults? [Y/n]: " choice
case "$choice" in
  ""|Y|y) USE_DEFAULTS="true" ;;
  *)      USE_DEFAULTS="false" ;;
esac
echo ""

# ── 4. Knowledge-base backend ────────────────────────────────────────────────
# Sites with a managed remote MCP (e.g. LUMI) always use it — don't ask.

if [[ "${SITE_HAS_REMOTE_MCP:-false}" == "true" ]]; then
  KB_BACKEND="remote-mcp"
elif [[ "$USE_DEFAULTS" == "true" ]]; then
  KB_BACKEND="remote-mcp"
else
  select_knowledge_base
  echo ""
fi

# ── 5. Inference backend ─────────────────────────────────────────────────────

if [[ "$USE_DEFAULTS" == "true" ]]; then
  INFERENCE_BACKEND="aitta"
  AITTA_API_BASE="https://aitta-api.csc.fi/openai/v1"
  TARGET_MODEL="openai/gpt-oss-120b"
  PROXY_PORT="8001"
  read_secret "Aitta API token: " AITTA_TOKEN
  echo ""
else
  select_inference_backend
  echo ""
fi

# ── 6. Agent framework ───────────────────────────────────────────────────────

select_agent_framework
echo ""

# ── 7. Optional skills ───────────────────────────────────────────────────────
# Asked even when using site defaults so GEAK and similar extras stay opt-in.

load_saved_optional_skills
select_optional_skills

# ── 8. Paths ─────────────────────────────────────────────────────────────────

echo "── Container images (SIF files) ──"
echo "Build instructions are in the README. Provide paths to pre-built SIF files."
echo ""

if [[ "$USE_DEFAULTS" == "true" ]]; then
  # With defaults: remote-mcp + aitta. Proxy only needed for claude-code.
  if [[ "$AGENT_FRAMEWORK" != "opencode" ]]; then
    read -rp "Path to aitta-proxy.sif [$DEFAULT_SIF_DIR/aitta-proxy.sif]: " INPUT
    AITTA_PROXY_SIF="${INPUT:-$DEFAULT_SIF_DIR/aitta-proxy.sif}"
  else
    AITTA_PROXY_SIF=""
  fi
  BUILD_DB_SIF=""
  CHROMA_MCP_SIF=""
  CHROMA_DATA_DIR=""
  DOCS_DIR=""
else
  if uses_litellm_proxy; then
    read -rp "Path to aitta-proxy.sif [$DEFAULT_SIF_DIR/aitta-proxy.sif]: " INPUT
    AITTA_PROXY_SIF="${INPUT:-$DEFAULT_SIF_DIR/aitta-proxy.sif}"
  else
    AITTA_PROXY_SIF=""
  fi

  if [[ "$KB_BACKEND" == "local-chroma" ]]; then
    read -rp "Path to build-db.sif [$DEFAULT_SIF_DIR/build-db.sif]: " INPUT
    BUILD_DB_SIF="${INPUT:-$DEFAULT_SIF_DIR/build-db.sif}"

    read -rp "Path to chroma-mcp.sif [$DEFAULT_SIF_DIR/chroma-mcp.sif]: " INPUT
    CHROMA_MCP_SIF="${INPUT:-$DEFAULT_SIF_DIR/chroma-mcp.sif}"

    echo ""
    echo "── Data directories ──"

    read -rp "Path for chroma database [$DEFAULT_CHROMA_DATA_DIR]: " INPUT
    CHROMA_DATA_DIR="${INPUT:-$DEFAULT_CHROMA_DATA_DIR}"

    read -rp "Path for documentation clone [$DEFAULT_DOCS_DIR]: " INPUT
    DOCS_DIR="${INPUT:-$DEFAULT_DOCS_DIR}"
  else
    BUILD_DB_SIF=""
    CHROMA_MCP_SIF=""
    CHROMA_DATA_DIR=""
    DOCS_DIR=""
  fi
fi
echo ""

# ── 9. Build knowledge base ──────────────────────────────────────────────────

echo "── Knowledge base setup ──"

if [[ "$KB_BACKEND" == "remote-mcp" ]]; then
  echo "Using managed remote MCP for $HPC_SITE:"
  echo "  Name : $SITE_REMOTE_MCP_NAME"
  echo "  URL  : $SITE_REMOTE_MCP_URL"
  echo "  Tool : $SITE_REMOTE_MCP_TOOL"
  echo "Skipping local docs clone, build-db, and chroma-mcp instance."
else
  DOCS_SOURCE_DIR="$DOCS_DIR"
  if [[ -n "$SITE_DOCS_SUBPATH" ]]; then
    DOCS_SOURCE_DIR="$DOCS_DIR/$SITE_DOCS_SUBPATH"
  fi

  if [[ ! -d "$DOCS_DIR" ]]; then
    echo "Cloning $HPC_SITE documentation..."
    git clone "$SITE_DOCS_REPO" "$DOCS_DIR"
  fi

  mkdir -p "$CHROMA_DATA_DIR"

  if [[ -d "$DOCS_SOURCE_DIR" ]] && [[ -f "$BUILD_DB_SIF" ]]; then
    if [[ -n "${SITE_DOCS_GLOB:-}" && -n "${SITE_DOCS_FILE_TYPE:-}" && -n "${SITE_COLLECTION:-}" ]]; then
      DOC_GLOB="$SITE_DOCS_GLOB"
      DOC_FILE_TYPE="$SITE_DOCS_FILE_TYPE"
      COLLECTION="$SITE_COLLECTION"
      echo "Building chroma knowledge base with $HPC_SITE defaults..."
      echo "  Glob: $DOC_GLOB  |  File type: $DOC_FILE_TYPE  |  Collection: $COLLECTION"
    else
      echo "Building chroma knowledge base..."
      read -rp "Glob pattern [$SITE_DOCS_GLOB]: " INPUT
      DOC_GLOB="${INPUT:-$SITE_DOCS_GLOB}"
      read -rp "File type [$SITE_DOCS_FILE_TYPE]: " INPUT
      DOC_FILE_TYPE="${INPUT:-$SITE_DOCS_FILE_TYPE}"
      read -rp "Collection name [$SITE_COLLECTION]: " INPUT
      COLLECTION="${INPUT:-$SITE_COLLECTION}"
    fi

    $CONTAINER_RT run \
      --bind "$DOCS_SOURCE_DIR:$CONTAINER_DOCS_MOUNT" \
      --bind "$CHROMA_DATA_DIR:$CONTAINER_CHROMA_MOUNT" \
      "$BUILD_DB_SIF" \
      "$CONTAINER_DOCS_MOUNT" "$DOC_GLOB" "$CONTAINER_CHROMA_MOUNT" \
      --file-type "$DOC_FILE_TYPE" --collection "$COLLECTION" \
      --chunk-size 1000 --chunk-overlap 200 --batch-size 200

    echo "Knowledge base built at $CHROMA_DATA_DIR"
  elif [[ ! -f "$BUILD_DB_SIF" ]]; then
    echo "WARNING: build-db.sif not found at $BUILD_DB_SIF — skipping knowledge base build."
    echo "Build it later with: bash $SCRIPT_DIR/setup.sh  (or see README)"
  else
    echo "Skipping knowledge base build (no documentation directory)."
  fi
fi
echo ""

# ── 10. Start services ───────────────────────────────────────────────────────

echo "── Starting services ──"
mkdir -p "$LOG_DIR"

if [[ "$KB_BACKEND" == "local-chroma" ]]; then
  if [[ -f "$CHROMA_MCP_SIF" ]]; then
    start_chroma_mcp
  else
    echo "  WARNING: chroma-mcp.sif not found — skipping. Knowledge base MCP will not be available."
  fi
fi

if uses_litellm_proxy; then
  if [[ -f "$AITTA_PROXY_SIF" ]]; then
    start_inference_proxy
  else
    echo "  WARNING: aitta-proxy.sif not found — skipping. Build it first (see README)."
  fi
elif [[ "$INFERENCE_BACKEND" == "aitta" ]] || [[ "$INFERENCE_BACKEND" == "custom-openai" ]]; then
  echo "  OpenCode talks OpenAI API natively — aitta-proxy not needed."
fi
echo ""

# ── 11. Configure agent framework ────────────────────────────────────────────

echo "── Configuring agent framework ──"

export SETUP_DIR="$SCRIPT_DIR"
FORCE_CONFIG_OVERWRITE=true

if [[ "$AGENT_FRAMEWORK" == "claude-code" ]]; then
  echo "Setting up Claude Code skills and MCP..."
  configure_claude_code_project "$PROJECT_DIR"

elif [[ "$AGENT_FRAMEWORK" == "opencode" ]]; then
  echo "Setting up OpenCode skills, rules, and MCP..."
  configure_opencode_project "$PROJECT_DIR"

else
  echo "Generic agent framework selected."
  echo "Skills are available in: $SKILLS_DIR"
  echo "Configure your agent to use the chroma MCP and skills directory as needed."
fi
echo ""

# ── 12. Save configuration ───────────────────────────────────────────────────

save_credentials
save_config
echo "Configuration saved to $CONFIG_FILE"
echo "Credentials saved to $CREDENTIALS_FILE (mode 600)"

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Setup complete                         ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  HPC site        : $HPC_SITE"
echo "  Knowledge base  : $KB_BACKEND"
if [[ "$KB_BACKEND" == "remote-mcp" ]]; then
  echo "                    $SITE_REMOTE_MCP_NAME -> $SITE_REMOTE_MCP_URL"
else
  echo "                    chroma data: $CHROMA_DATA_DIR"
fi
echo "  Inference       : $INFERENCE_BACKEND"
if [[ "$INFERENCE_BACKEND" == "custom-openai" ]]; then
  echo "                    $CUSTOM_API_BASE ($CUSTOM_MODEL)"
fi
echo "  Agent framework : $AGENT_FRAMEWORK"
echo "  Skills          : $SITE_SKILLS"
if [[ -n "${ENABLED_OPTIONAL_SKILLS:-}" ]]; then
  echo "  Optional skills : $ENABLED_OPTIONAL_SKILLS"
else
  echo "  Optional skills : (none — re-run setup or hpc-assistant-init --with-skill NAME)"
fi
if uses_litellm_proxy; then
  echo "  Inference proxy : ${PROXY_HOST:-unknown}:${PROXY_PORT:-8001}"
fi
echo ""
echo "To launch the assistant:"
echo "  bash $SCRIPT_DIR/launch.sh"
echo ""
if [[ "$AGENT_FRAMEWORK" == "claude-code" ]]; then
  echo "Or use Claude Code directly from the project directory:"
  echo "  cd $PROJECT_DIR && claude"
  echo ""
  echo "From any other directory, add this project as context:"
  echo "  claude --add-dir $PROJECT_DIR"
elif [[ "$AGENT_FRAMEWORK" == "opencode" ]]; then
  echo "Or use OpenCode directly from the project directory:"
  echo "  cd $PROJECT_DIR && opencode"
fi
