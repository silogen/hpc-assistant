#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# GEAK installation script -- runs inside the lumi-multitorch container.
# Called by run-geak.sbatch inside the lumi-multitorch container.
#
# Expected: /workspace bound to project root.
# Requires: ANTHROPIC_API_KEY in environment.

set -euo pipefail

MARKER="/workspace/.geak-third-party-download-accepted"
if [[ ! -f "${MARKER}" ]]; then
  echo "[setup] ERROR: Third-party download notice not accepted (missing ${MARKER})." >&2
  echo "[setup] Accept GEAK_THIRD_PARTY_DOWNLOAD_NOTICE.txt before submitting jobs." >&2
  exit 1
fi

: "${GEAK_BRANCH:=dev-silo}"
: "${GEAK_PR:=}"

DEPS_DIR="/workspace/.deps"
VENV_DIR="/tmp/geak-venv"

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "[setup] Creating virtual environment..."
  rm -rf "${VENV_DIR}"
  python3 -m venv --system-site-packages "${VENV_DIR}"
fi
source "${VENV_DIR}/bin/activate"
CRED_FILE="/workspace/.git-credentials"
gcl() { git -c "credential.helper=store --file=${CRED_FILE}" clone "$@"; }
gpull() { git -c "credential.helper=store --file=${CRED_FILE}" pull "$@"; }
gfetch() { git -c "credential.helper=store --file=${CRED_FILE}" fetch "$@"; }

# ------------------------------------------------------------------
# 1. Clone GEAK dev branch
# ------------------------------------------------------------------
mkdir -p "${DEPS_DIR}"

if [[ ! -d "${DEPS_DIR}/GEAK" ]]; then
  echo "[setup] Cloning GEAK (branch ${GEAK_BRANCH})..."
  gcl --branch "${GEAK_BRANCH}" https://github.com/AMD-AGI/GEAK.git "${DEPS_DIR}/GEAK"
  cd "${DEPS_DIR}/GEAK"
  git config user.email "geak@lumi"
  git config user.name "geak"
else
  echo "[setup] GEAK already cloned, pulling latest..."
  cd "${DEPS_DIR}/GEAK"
  gpull origin "${GEAK_BRANCH}" || true
fi

# ------------------------------------------------------------------
# 2. Optionally fetch and merge a PR
# ------------------------------------------------------------------
if [[ -n "${GEAK_PR}" ]]; then
  echo "[setup] Fetching and merging PR #${GEAK_PR}..."
  gfetch origin "pull/${GEAK_PR}/head:pr-${GEAK_PR}" 2>/dev/null || true
  git merge --no-edit "pr-${GEAK_PR}" 2>/dev/null || \
    echo "[setup] PR #${GEAK_PR} merge skipped (may already be included)."
fi
cd /workspace

# ------------------------------------------------------------------
# 3. Install GEAK
# ------------------------------------------------------------------
echo "[setup] Installing GEAK..."
pip install -e "${DEPS_DIR}/GEAK"
pip install fastmcp

# ------------------------------------------------------------------
# 4. Install Metrix (required for kernel profiling)
# ------------------------------------------------------------------
if ! python3 -c "import metrix" 2>/dev/null; then
  echo "[setup] Installing Metrix from intellikit..."
  if [[ ! -d "${DEPS_DIR}/intellikit" ]]; then
    gcl https://github.com/AMDResearch/intellikit.git "${DEPS_DIR}/intellikit"
  fi
  pip install -e "${DEPS_DIR}/intellikit/metrix"
else
  echo "[setup] Metrix already installed."
fi

# ------------------------------------------------------------------
# 4b. Install MCP tools (matching GEAK Dockerfile)
# ------------------------------------------------------------------
echo "[setup] Installing MCP tools (matching GEAK Dockerfile)..."
GEAK_DIR="${DEPS_DIR}/GEAK"
pip install -e "${GEAK_DIR}/mcp_tools/mcp-client/"
pip install -e "${GEAK_DIR}/mcp_tools/metrix-mcp/"
pip install -e "${GEAK_DIR}/mcp_tools/openevolve-mcp/"
pip install -e "${GEAK_DIR}/mcp_tools/kernel-ercs/"
pip install -e "${GEAK_DIR}/mcp_tools/kernel-evolve/"
pip install -e "${GEAK_DIR}/mcp_tools/automated-test-discovery/"
pip install -e "${GEAK_DIR}/mcp_tools/profiler-mcp/"

# ------------------------------------------------------------------
# 5. Create GEAK global config
# ------------------------------------------------------------------
mkdir -p ~/.config/mini-swe-agent
cat > ~/.config/mini-swe-agent/.env << EOF
MSWEA_CONFIGURED=true
ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
EOF
echo "[setup] GEAK config written to ~/.config/mini-swe-agent/.env"

# ------------------------------------------------------------------
# 6. Health check
# ------------------------------------------------------------------
if command -v geak > /dev/null 2>&1; then
  echo "[health] OK: geak CLI available"
else
  echo "[health] ERROR: geak CLI not found"
  exit 1
fi
