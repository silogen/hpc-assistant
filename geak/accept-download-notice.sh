#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# Standalone acceptance of the GEAK third-party download notice.
# Run from your project directory before submitting GEAK jobs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GEAK_DIR="$SCRIPT_DIR"
HPC_ASSISTANT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="${1:-$PWD}"

# shellcheck source=download-notice.sh
source "$SCRIPT_DIR/download-notice.sh"

if prompt_geak_third_party_download_notice "$PROJECT_DIR"; then
  echo "Accepted. Marker written to $(geak_download_acceptance_marker "$PROJECT_DIR")"
else
  echo "Notice not accepted — GEAK download scripts will not run." >&2
  exit 1
fi
