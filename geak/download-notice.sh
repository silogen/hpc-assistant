#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# Shared helpers for the GEAK third-party download notice.
# Sourced by setup/config.sh, setup/setup.sh, hpc-assistant-init, and
# skills/optimize-kernel/scripts/submit-geak-job.sh.

GEAK_DOWNLOAD_NOTICE_BASENAME="GEAK_THIRD_PARTY_DOWNLOAD_NOTICE.txt"
GEAK_DOWNLOAD_ACCEPT_MARKER=".geak-third-party-download-accepted"

geak_download_notice_source() {
  local root="${GEAK_DIR:-${HPC_ASSISTANT_ROOT:-}/geak}"
  echo "${root}/THIRD_PARTY_DOWNLOAD_NOTICE.txt"
}

geak_download_notice_in_project() {
  local project_dir="${1:?}"
  echo "${project_dir}/${GEAK_DOWNLOAD_NOTICE_BASENAME}"
}

geak_download_acceptance_marker() {
  local project_dir="${1:?}"
  echo "${project_dir}/${GEAK_DOWNLOAD_ACCEPT_MARKER}"
}

display_geak_third_party_download_notice() {
  local notice_file="${1:-$(geak_download_notice_source)}"
  if [[ ! -f "$notice_file" ]]; then
    echo "ERROR: Download notice not found: $notice_file" >&2
    return 1
  fi
  echo ""
  cat "$notice_file"
  echo ""
}

prompt_geak_third_party_download_notice() {
  local project_dir="${1:-${PROJECT_DIR:-${PROJECT_TARGET:-$PWD}}}"
  local notice_src notice_dest

  notice_src="$(geak_download_notice_source)"
  notice_dest="$(geak_download_notice_in_project "$project_dir")"

  if [[ -f "$notice_src" && "$notice_src" != "$notice_dest" ]]; then
    cp "$notice_src" "$notice_dest"
  fi

  display_geak_third_party_download_notice "${notice_dest}"
  echo "By typing 'yes', you acknowledge this notice and authorize download"
  echo "of the third-party components described above."
  read -rp "Accept and continue? [y/N]: " choice
  case "${choice:-n}" in
    y|Y|yes|YES)
      write_geak_download_acceptance_marker "$project_dir"
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

write_geak_download_acceptance_marker() {
  local project_dir="${1:?}"
  local notice_file marker sha

  notice_file="$(geak_download_notice_in_project "$project_dir")"
  marker="$(geak_download_acceptance_marker "$project_dir")"

  if [[ ! -f "$notice_file" ]]; then
    local notice_src
    notice_src="$(geak_download_notice_source)"
    if [[ -f "$notice_src" ]]; then
      cp "$notice_src" "$notice_file"
    fi
  fi

  if command -v sha256sum >/dev/null 2>&1; then
    sha=$(sha256sum "$notice_file" | awk '{print $1}')
  else
    sha=$(shasum -a 256 "$notice_file" | awk '{print $1}')
  fi

  umask 077
  {
    echo "accepted_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "notice_file=${GEAK_DOWNLOAD_NOTICE_BASENAME}"
    echo "notice_sha256=${sha}"
  } > "$marker"
  chmod 600 "$marker"
}

require_geak_download_acceptance() {
  local project_dir="${1:?}"
  local marker notice_dest

  marker="$(geak_download_acceptance_marker "$project_dir")"
  if [[ -f "$marker" ]]; then
    return 0
  fi

  notice_dest="$(geak_download_notice_in_project "$project_dir")"
  echo "ERROR: GEAK third-party download notice not accepted." >&2
  if [[ -f "$notice_dest" ]]; then
    display_geak_third_party_download_notice "$notice_dest" >&2
  else
    display_geak_third_party_download_notice >&2 || true
  fi
  echo "Accept the notice during setup (setup.sh / hpc-assistant-init --geak)" >&2
  echo "or run: bash geak/accept-download-notice.sh" >&2
  return 1
}

install_geak_download_notice_files() {
  local project_dir="${1:?}"
  local notice_src

  notice_src="$(geak_download_notice_source)"
  if [[ -f "$notice_src" ]]; then
    cp "$notice_src" "$(geak_download_notice_in_project "$project_dir")"
  fi
  if [[ -f "${GEAK_DIR}/download-notice.sh" ]]; then
    cp "${GEAK_DIR}/download-notice.sh" "${project_dir}/geak-download-notice.sh"
    chmod +x "${project_dir}/geak-download-notice.sh"
  fi
}
