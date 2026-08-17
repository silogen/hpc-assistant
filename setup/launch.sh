#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# HPC Assistant — launch script.
# Ensures services are running and starts the configured agent framework.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=config.sh
source "$SCRIPT_DIR/config.sh"

# ── Load saved configuration ─────────────────────────────────────────────────

detect_container_runtime
load_config

echo "HPC Assistant — launching ($HPC_SITE / $KB_BACKEND / $INFERENCE_BACKEND / $AGENT_FRAMEWORK)"
echo ""

# ── Ensure services are running ──────────────────────────────────────────────

mkdir -p "$LOG_DIR"

echo "Checking services..."

if [[ "$KB_BACKEND" == "local-chroma" ]] && [[ -n "${CHROMA_MCP_SIF:-}" ]] && [[ -f "$CHROMA_MCP_SIF" ]]; then
  start_chroma_mcp
elif [[ "$KB_BACKEND" == "remote-mcp" ]]; then
  echo "  Knowledge base served by remote MCP (${REMOTE_MCP_NAME:-$SITE_REMOTE_MCP_NAME} -> ${REMOTE_MCP_URL:-$SITE_REMOTE_MCP_URL}); no local instance to start."
fi

if uses_litellm_proxy && [[ -n "${AITTA_PROXY_SIF:-}" ]] && [[ -f "$AITTA_PROXY_SIF" ]]; then
  start_inference_proxy

  # Update config if proxy host changed (different login node)
  save_config
fi

# ── GEAK sanity check ────────────────────────────────────────────────────────
if [[ "${GEAK_ENABLED:-false}" == "true" ]]; then
  if [[ ! -f "$PROJECT_DIR/.anthropic_api_key" ]]; then
    if [[ -n "${GEAK_ANTHROPIC_API_KEY:-}" ]]; then
      echo "  Restoring .anthropic_api_key from credentials store..."
      umask 077
      printf '%s' "$GEAK_ANTHROPIC_API_KEY" > "$PROJECT_DIR/.anthropic_api_key"
      chmod 600 "$PROJECT_DIR/.anthropic_api_key"
    else
      echo "  WARNING: GEAK is enabled but $PROJECT_DIR/.anthropic_api_key is missing" >&2
      echo "  and no key was found in credentials. Re-run setup.sh to provide it." >&2
    fi
  fi

  echo "  GEAK: enabled (SLURM jobs use lumi-multitorch + setup-geak.sh)"
fi

echo ""

# ── Launch agent ─────────────────────────────────────────────────────────────

if [[ "$AGENT_FRAMEWORK" == "claude-code" ]]; then
  CLAUDE_ARGS=()

  # Build system prompt — describes the KB tool that was wired up at setup time.
  if [[ "$KB_BACKEND" == "remote-mcp" ]]; then
    KB_DESCRIPTION="the \`${REMOTE_MCP_TOOL:-$SITE_REMOTE_MCP_TOOL}\` tool from the \`${REMOTE_MCP_NAME:-$SITE_REMOTE_MCP_NAME}\` MCP server"
  else
    KB_DESCRIPTION="the chroma MCP tool"
  fi
  SYSTEM_PROMPT="You are an HPC assistant. For every task, follow this priority order:
1. Check relevant SKILLs available to you. If there are relevant SKILLs, use them to answer the task.
2. Query ${KB_DESCRIPTION} first to check whether the HPC documentation knowledge base contains information relevant to the task. If it does, base your answer on those results.
3. If the knowledge base returns no useful results, check whether any other available MCP tools or skills can address the task, and use them if applicable.
4. Only if neither the knowledge base nor any tool is applicable, reason through the task using your own knowledge — and clearly state that your answer is based on general knowledge rather than the site-specific documentation."

  CLAUDE_ARGS+=(--append-system-prompt "$SYSTEM_PROMPT")

  echo "Launching Claude Code..."
  echo "  Working directory: $PROJECT_DIR"
  echo ""

  cd "$PROJECT_DIR"
  case "$INFERENCE_BACKEND" in
    aitta|custom-openai)
      # The LiteLLM proxy exposes whichever OpenAI-compatible endpoint is
      # configured under the single deployment name `aitta` (see
      # setup/aitta-proxy/config.yaml). Point both the main and the background
      # "small/fast" model at it so every request maps to that deployment.
      resolve_openai_endpoint
      exec env \
        ANTHROPIC_BASE_URL="http://${PROXY_HOST}:${PROXY_PORT}" \
        ANTHROPIC_API_KEY="${ENDPOINT_API_KEY}" \
        ANTHROPIC_AUTH_TOKEN="${ENDPOINT_API_KEY}" \
        ANTHROPIC_MODEL="aitta" \
        ANTHROPIC_SMALL_FAST_MODEL="aitta" \
        claude "${CLAUDE_ARGS[@]}"
      ;;
    anthropic)
      exec env \
        ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL}" \
        ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
        ANTHROPIC_MODEL="${ANTHROPIC_MODEL}" \
        claude "${CLAUDE_ARGS[@]}"
      ;;
  esac

elif [[ "$AGENT_FRAMEWORK" == "opencode" ]]; then
  echo "Launching OpenCode..."
  echo "  Working directory: $PROJECT_DIR"
  echo ""

  cd "$PROJECT_DIR"
  # Background subagents require OpenCode >= 1.14.51. On older versions the flag
  # is silently ignored and geak-monitor will block the primary session.
  OC_VERSION=$(opencode --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1 || echo "unknown")
  echo "  OpenCode version: $OC_VERSION"
  echo "  NOTE: background subagents (non-blocking monitor) require OpenCode >= 1.14.51."
  echo "        On older versions geak-monitor blocks the primary session until the job completes."

  case "$INFERENCE_BACKEND" in
    aitta)
      exec env \
        AITTA_TOKEN="${AITTA_TOKEN}" \
        OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true \
        opencode
      ;;
    custom-openai)
      # Base URL and model live in opencode.json; only the key is injected here.
      exec env \
        CUSTOM_API_KEY="${CUSTOM_API_KEY:-$PLACEHOLDER_API_KEY}" \
        OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true \
        opencode
      ;;
    anthropic)
      exec env \
        ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
        OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true \
        opencode
      ;;
  esac

else
  echo "Generic agent framework — manual launch required."
  echo ""
  echo "Services running:"
  if [[ "$KB_BACKEND" == "remote-mcp" ]]; then
    echo "  knowledge base: remote MCP ${REMOTE_MCP_NAME:-$SITE_REMOTE_MCP_NAME} -> ${REMOTE_MCP_URL:-$SITE_REMOTE_MCP_URL}"
  elif is_instance_running "chroma-mcp"; then
    echo "  chroma-mcp: running (use '$CONTAINER_RT exec instance://chroma-mcp ...')"
  fi
  if uses_litellm_proxy && is_proxy_reachable "${PROXY_HOST:-localhost}" "${PROXY_PORT:-8001}"; then
    echo "  aitta-proxy: http://${PROXY_HOST}:${PROXY_PORT} (Anthropic-compatible API)"
  fi
  echo ""
  echo "Skills directory: $SKILLS_DIR"
  echo "Installed skills: $SITE_SKILLS"
  echo ""
  echo "Configure your agent to use these services."
fi
