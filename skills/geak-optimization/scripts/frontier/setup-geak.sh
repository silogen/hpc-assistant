#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# One-time GEAK v4 installation for Frontier, run on a LOGIN node (compute nodes have no
# direct internet; installing at job runtime is avoided on purpose).
#
# Usage: setup-geak.sh <project_dir> <python_with_torch_and_triton>
#   project_dir  where GEAK jobs are submitted from (gets .geak-frontier.env)
#   python       interpreter of an existing ROCm PyTorch+Triton environment; the GEAK venv
#                is layered on it with --system-site-packages, so kernels see the same
#                torch/triton as the application.
# Environment: GEAK_FRONTIER_HOME (default: <project_dir>/../geak-frontier),
#              GEAK_REF (default: main).
#
# GEAK v4 runs inside Claude Code, which must already be installed (>= 2.1.177).
# `pip install` of the geak package itself is skipped: its build hook installs Claude Code
# into ~/.local/bin, which this setup does not need.

set -euo pipefail

PROJECT_DIR="$(realpath "${1:?Usage: setup-geak.sh <project_dir> <python>}")"
BASE_PY="${2:?Usage: setup-geak.sh <project_dir> <python>}"
HOME_DIR="${GEAK_FRONTIER_HOME:-$(realpath -m "${PROJECT_DIR}/../geak-frontier")}"
GEAK_REF="${GEAK_REF:-main}"
GEAK_HOME="${HOME_DIR}/deps/GEAK"
GEAK_VENV="${HOME_DIR}/venv"
# Metrix commit pinned by GEAK's pyproject.toml.
METRIX="metrix @ git+https://github.com/AMDResearch/intellikit.git@2f61453a779980b00504ea3b772ff4a1a1c3f4ad#subdirectory=metrix"

[[ -x "${BASE_PY}" ]] || command -v "${BASE_PY}" >/dev/null || { echo "Python not found: ${BASE_PY}" >&2; exit 1; }
if [[ "$(hostname)" != login* ]]; then
  echo "WARNING: not on a login node; compute nodes have no direct internet access." >&2
fi

mkdir -p "${HOME_DIR}/deps"
if [[ ! -d "${GEAK_HOME}/.git" ]]; then
  git clone --depth 1 --branch "${GEAK_REF}" https://github.com/AMD-AGI/GEAK.git "${GEAK_HOME}"
else
  git -C "${GEAK_HOME}" pull --ff-only || echo "WARNING: could not update ${GEAK_HOME}"
fi

if [[ ! -x "${GEAK_VENV}/bin/python" ]]; then
  "${BASE_PY}" -m venv --system-site-packages "${GEAK_VENV}"
fi
"${GEAK_VENV}/bin/pip" install --quiet pyyaml requests datasets "${METRIX}"
"${GEAK_VENV}/bin/python" -c "import torch, triton, metrix, yaml; print('torch', torch.__version__, '| triton', triton.__version__)"

claude_bin="$(command -v claude || echo "${HOME}/.local/bin/claude")"
if [[ -x "${claude_bin}" ]]; then
  echo "Claude Code: $("${claude_bin}" --version)"
else
  echo "WARNING: Claude Code CLI not found; install it (>= 2.1.177) before submitting jobs." >&2
fi

cat > "${PROJECT_DIR}/.geak-frontier.env" <<EOF
# Written by geak-optimization/scripts/frontier/setup-geak.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)
GEAK_HOME=${GEAK_HOME}
GEAK_VENV=${GEAK_VENV}
EOF
echo "GEAK v4 ready: ${GEAK_HOME} ($(git -C "${GEAK_HOME}" rev-parse --short HEAD)), venv ${GEAK_VENV}"
echo "Wrote ${PROJECT_DIR}/.geak-frontier.env"
