-- Copyright Advanced Micro Devices, Inc.
--
-- SPDX-License-Identifier: MIT

-- HPC Assistant — Environment Modules (Lmod)
--
-- Exposes install root, PATH (hpc-assistant-init), and optional Singularity binds
-- for use with the LUMI AI Factory agent environment (lumi-aif-agents).
--
-- Typical usage on LUMI:
--   module use /path/to/hpc-assistant/modulefiles
--   module load hpc-assistant
--   cd ~/projects/myapp && hpc-assistant-init
--   export ANTHROPIC_API_KEY=...            # default; or --inference aitta + AITTA_TOKEN
--   module load Local-LAIF lumi-aif-agents   # optional: containerized OpenCode
--   opencode

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

help([[
The AMD HPC Assistant provides site-specific agent skills, OpenCode /
Claude Code configuration templates, and integration with the LUMI AI Factory
MCP knowledge base.

This module sets:
  HPC_ASSISTANT_ROOT                           Install root (skills, setup templates, init script)
  PATH                                         Adds bin/ (hpc-assistant-init)
  OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS   true (same as setup/launch.sh for OpenCode;
                                                enables non-blocking background subagents; requires OpenCode >= 1.14.51)

After loading, initialize a project directory:
  cd /path/to/your/project
  hpc-assistant-init
  export ANTHROPIC_API_KEY=<key>   # default inference backend
  # or: hpc-assistant-init --inference aitta && export AITTA_TOKEN=<token>
  # or: hpc-assistant-init --inference custom-openai --api-base <url> --model <id>
  #     export CUSTOM_API_KEY=<key>   # own OpenAI-compatible endpoint
  # Optional GEAK kernel-optimizer skill (not installed by default):
  #   hpc-assistant-init --with-skill geak-optimization

Optional: load the LUMI AI Factory agent environment for containerized OpenCode:
  module load Local-LAIF lumi-aif-agents

Slurm and other login-node tools are not available inside the default agent
container today; skills document LUMI workflows for when host or MCP access is
available.

More information:
  https://github.com/silogen/hpc-assistant
  https://docs.lumi-supercomputer.eu/laif/software/agent-infrastructure/
]])

setenv("HPC_ASSISTANT_ROOT", repo_root)
prepend_path("PATH", pathJoin(repo_root, "bin"))

-- Match setup/launch.sh when starting OpenCode: background subagents.
-- On OpenCode < 1.14.51 the flag is ignored; see launch.sh comments.
setenv("OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS", "true")

-- Optional bind of the install tree when used with lumi-aif-agents / Singularity.
local existing_bind = os.getenv("SINGULARITY_BIND") or ""
local skills_bind = pathJoin(repo_root, "skills")
if existing_bind == "" then
    setenv("SINGULARITY_BIND", skills_bind)
else
    setenv("SINGULARITY_BIND", existing_bind .. "," .. skills_bind)
end

if mode() == "load" then
    LmodMessage(
        "\n" ..
        "HPC Assistant loaded (" .. repo_root .. ")\n" ..
        "  Run: cd <project> && hpc-assistant-init\n" ..
        "  (hpc-assistant-init prints the next steps, including which token to set.)\n"
    )
end
