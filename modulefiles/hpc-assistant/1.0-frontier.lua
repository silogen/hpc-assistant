-- Copyright Advanced Micro Devices, Inc.
--
-- SPDX-License-Identifier: MIT

-- HPC Assistant — Environment Modules (Lmod), Frontier (OLCF).
-- Load explicitly: module load hpc-assistant/1.0-frontier

-- Resolve install root regardless of Lmod version (myFileName() may be the .lua
-- file path or the module directory path).
local function find_repo_root(modpath)
    local root = modpath:match("^(.-)/modulefiles")
    if root and isFile(pathJoin(root, "bin/hpc-assistant-init")) then
        return root
    end

    local p = modpath
    for _ = 1, 6 do
        if isFile(pathJoin(p, "bin/hpc-assistant-init")) then
            return p
        end
        p = dirname(p)
    end

    return nil
end

local repo_root = find_repo_root(myFileName())
if not repo_root then
    LmodError(
        "HPC Assistant: cannot find install root (expected bin/hpc-assistant-init). "
            .. "Modulefile location: "
            .. myFileName()
    )
end

whatis("Name: hpc-assistant")
whatis("Version: 1.0-frontier")
whatis("Description: Site-aware AI coding agent skills and setup for OLCF Frontier")

help([[
The AMD HPC Assistant provides Frontier-specific agent skills, OpenCode /
Claude Code configuration templates, and a bundled snapshot of the OLCF
Frontier documentation that the agent reads as files (no MCP server needed).

This module sets:
  HPC_ASSISTANT_ROOT                           Install root (skills, setup templates, init script)
  HPC_ASSISTANT_SITE                           FRONTIER (default --site for hpc-assistant-init)
  PATH                                         Adds bin/ (hpc-assistant-init)
  OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS   true (same as setup/launch.sh for OpenCode;
                                                requires OpenCode >= 1.14.51)

After loading, initialize a project directory (on Orion, not $HOME):
  cd /lustre/orion/<project>/scratch/$USER/myapp
  hpc-assistant-init                           # OpenCode, docs-files KB, agent-native inference
  hpc-assistant-init --framework claude-code   # Claude Code instead

Defaults on Frontier:
  knowledge base   docs-files: OLCF docs copied to .hpc-assistant/docs/frontier/
  inference        agent-native: the agent keeps its own login; no API key is written
  skills           profiling, energy-profiling, frontier-resource-management,
                   frontier-slurm-job-management, frontier-frequency-power-capping

Related Frontier modules used by the skills:
  module load rocm                                  # rocprofv3 (profiling)
  module use /sw/frontier/amdsw/modulefiles
  module load omnistat-wrapper                      # energy-profiling

Notes:
  * Run GPU work through Slurm (sbatch/srun) with an OLCF project account; login
    nodes have no GPUs.
  * GEAK (--geak) is only supported on LUMI by hpc-assistant-init.
  * Unload this module or pass --site LUMI to initialize for another site.

More information:
  https://github.com/silogen/hpc-assistant
  https://docs.olcf.ornl.gov/systems/frontier_user_guide.html
]])

setenv("HPC_ASSISTANT_ROOT", repo_root)
setenv("HPC_ASSISTANT_SITE", "FRONTIER")
prepend_path("PATH", pathJoin(repo_root, "bin"))

setenv("OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS", "true")

if mode() == "load" then
    LmodMessage(
        "\n" ..
        "HPC Assistant (Frontier) loaded (" .. repo_root .. ")\n" ..
        "  Run: cd /lustre/orion/<project>/scratch/$USER/<app> && hpc-assistant-init\n" ..
        "  (defaults: --site FRONTIER, docs-files knowledge base, agent-native inference)\n"
    )
end
