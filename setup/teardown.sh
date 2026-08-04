#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# HPC Assistant — teardown script.
# Stops all services, removes data, config, and Claude Code integration.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=config.sh
source "$SCRIPT_DIR/config.sh"

echo "╔══════════════════════════════════════════╗"
echo "║   HPC Assistant — Teardown               ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "This will remove ALL data, services, and configuration."
echo ""
read -rp "Are you sure? Type 'yes' to confirm: " CONFIRM
if [[ "$CONFIRM" != "yes" ]]; then
  echo "Aborted."
  exit 0
fi
echo ""

# ── Load config if it exists (best-effort) ────────────────────────────────────

detect_container_runtime || true

if [[ -f "$CONFIG_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$CONFIG_FILE" 2>/dev/null || true
fi

# ── 1. Stop Singularity/Apptainer instances ──────────────────────────────────

echo "── Stopping services ──"

if [[ -n "${CONTAINER_RT:-}" ]]; then
  for instance in aitta-proxy chroma-mcp; do
    if is_instance_running "$instance" 2>/dev/null; then
      $CONTAINER_RT instance stop "$instance" && echo "  Stopped $instance"
    else
      echo "  $instance not running"
    fi
  done
else
  echo "  No container runtime found, skipping instance cleanup"
fi
echo ""

# ── 2. Remove agent framework integration ─────────────────────────────────────

echo "── Removing agent framework integration ──"

# Claude Code
if command -v claude &>/dev/null; then
  echo "  Removing knowledge-base MCP registrations from Claude Code..."
  claude mcp remove chroma 2>/dev/null && echo "  Removed chroma MCP" || echo "  chroma MCP not registered (skipped)"
  # Remove any per-site remote MCP that setup.sh may have added.
  for mcp_name in lumi-aif; do
    claude mcp remove "$mcp_name" 2>/dev/null \
      && echo "  Removed $mcp_name MCP" \
      || echo "  $mcp_name MCP not registered (skipped)"
  done
else
  echo "  Claude Code not found (skipped)"
fi

# Remove only what setup generated (skills + CLAUDE.md). The committed
# .claude/settings.json (and the .claude/ dir itself) is left in place.
if [[ -d "$PROJECT_DIR/.claude/skills" ]]; then
  rm -rf "$PROJECT_DIR/.claude/skills"
  echo "  Removed $PROJECT_DIR/.claude/skills/"
  rmdir "$PROJECT_DIR/.claude" 2>/dev/null && echo "  Removed empty $PROJECT_DIR/.claude/" || true
fi

if [[ -f "$PROJECT_DIR/CLAUDE.md" ]]; then
  rm -f "$PROJECT_DIR/CLAUDE.md"
  echo "  Removed $PROJECT_DIR/CLAUDE.md"
fi

# OpenCode — remove generated skills; drop .opencode/ only if it ends up empty.
if [[ -d "$PROJECT_DIR/.opencode/skills" ]]; then
  rm -rf "$PROJECT_DIR/.opencode/skills"
  echo "  Removed $PROJECT_DIR/.opencode/skills/"
  rmdir "$PROJECT_DIR/.opencode" 2>/dev/null && echo "  Removed empty $PROJECT_DIR/.opencode/" || true
fi

if [[ -f "$PROJECT_DIR/AGENTS.md" ]]; then
  rm -f "$PROJECT_DIR/AGENTS.md"
  echo "  Removed $PROJECT_DIR/AGENTS.md"
fi

if [[ -f "$PROJECT_DIR/opencode.json" ]]; then
  rm -f "$PROJECT_DIR/opencode.json"
  echo "  Removed $PROJECT_DIR/opencode.json"
fi
echo ""

# ── 3. Remove knowledge base and documentation ──────────────────────────────

echo "── Removing data ──"

if [[ "${KB_BACKEND:-local-chroma}" == "remote-mcp" ]]; then
  echo "  Knowledge base was a remote MCP (${REMOTE_MCP_URL:-unknown}); nothing local to remove."
fi

# Always try the well-known local paths in case a previous run used them.
CHROMA_DIR="${CHROMA_DATA_DIR:-$DEFAULT_CHROMA_DATA_DIR}"
if [[ -n "$CHROMA_DIR" ]] && [[ -d "$CHROMA_DIR" ]]; then
  rm -rf "$CHROMA_DIR"
  echo "  Removed chroma data: $CHROMA_DIR"
else
  echo "  No chroma data found (skipped)"
fi

DOCS="${DOCS_DIR:-$DEFAULT_DOCS_DIR}"
if [[ -n "$DOCS" ]] && [[ -d "$DOCS" ]]; then
  rm -rf "$DOCS"
  echo "  Removed documentation: $DOCS"
else
  echo "  No documentation found (skipped)"
fi
echo ""

# ── 4. Remove configuration and credentials ──────────────────────────────────

echo "── Removing configuration ──"

if [[ -d "$CONFIG_DIR" ]]; then
  rm -rf "$CONFIG_DIR"
  echo "  Removed $CONFIG_DIR"
else
  echo "  No config directory found (skipped)"
fi
echo ""

# ── 5. Remove data directory if empty ─────────────────────────────────────────

if [[ -d "$DEFAULT_DATA_DIR" ]]; then
  rmdir "$DEFAULT_DATA_DIR" 2>/dev/null && echo "  Removed empty $DEFAULT_DATA_DIR" || true
fi

# ── 6. Unset environment variables ────────────────────────────────────────────

echo "── Unsetting environment variables ──"
unset ANTHROPIC_API_KEY ANTHROPIC_BASE_URL ANTHROPIC_MODEL ANTHROPIC_AUTH_TOKEN
unset AITTA_API_BASE AITTA_TOKEN TARGET_MODEL
echo "  Cleared ANTHROPIC_* and AITTA_* variables for this shell"
echo ""

# ── Done ──────────────────────────────────────────────────────────────────────

echo "╔══════════════════════════════════════════╗"
echo "║   Teardown complete                      ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "Removed:"
echo "  - Singularity instances (aitta-proxy, chroma-mcp)"
echo "  - Generated agent config (.claude/skills/, .opencode/skills/, CLAUDE.md, AGENTS.md, opencode.json)"
echo "  - Knowledge base (chroma data)"
echo "  - Documentation clone"
echo "  - Config and credentials (~/.config/hpc-assistant/)"
echo ""
echo "NOT removed (manual cleanup if desired):"
echo "  - Committed repo files (e.g. .claude/settings.json)"
echo "  - SIF container images (in ${DEFAULT_SIF_DIR})"
echo "  - Claude Code / OpenCode binaries"
echo "  - This repository"
