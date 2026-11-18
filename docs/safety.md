# Safety and the permission model

The assistant runs against shared HPC systems, where a careless command can affect other
users, waste billing units, or corrupt data. Safety is enforced at two levels — but the
most effective control is architectural: running the agent in [local mode](LOCAL-MODE.md)
keeps the expensive operations off the cluster entirely.

## Prompt-level rules

Both Claude Code and OpenCode receive explicit HPC safety instructions in their generated
system prompt (`CLAUDE.md` / `AGENTS.md`):

- Never run destructive commands (`rm -rf`, `module purge`) without confirmation
- Never submit or cancel Slurm jobs without showing the full command first
- Never modify shell config files such as `.bashrc` without approval
- Never traverse the parallel filesystem recursively, and always bound a search
- Prefer read-only investigation before making changes
- Warn about quota impact before creating large files

The filesystem rules appear in the prompt *as well as* in the permission block, because an
agent's built-in `Read`, `Glob` and `Grep` tools are not shell commands — no bash
permission rule governs them.

## Tool-level permissions (OpenCode only)

`opencode.json` carries a granular permission block:

| Category | Policy |
| -------- | ------ |
| Cheap, constant-cost reads | `allow` — `ls`, `cat`, `head`, `tail`, `diff`, `pwd`, `env` |
| Search | `allow` — `find`, `grep`, `wc` |
| Whole-tree traversal | `ask` — `du`, `ls -R` |
| Scheduler queries | `allow` — `squeue`, `sacct`, `sinfo`, `scontrol show` |
| Job submission | `ask` — `sbatch`, `srun`, `salloc`, `scancel` |
| File modification | `ask` — all edits require approval |
| Deletion | `ask` for `rm`, `deny` for `rm -rf` |
| System administration | `deny` — `chown`, `dd`, `mkfs`, `kill -9`, `reboot` |
| External directories | `ask` — any access outside the project directory |
| Doom loop detection | `deny` — prevents infinite tool call loops |

`du` and `ls -R` are gated because they walk an entire tree, and on a parallel filesystem
every entry visited is a request to a metadata server shared by the whole machine. `find`
and `grep` are *allowed* because gating commands this common trains people to approve
without reading, which buys nothing. Their recursive forms carry the same cost, so the
system prompt asks the agent to bound them — guidance, not enforcement, which is the honest
limit of what a glob-pattern permission list can do, and part of why local mode is the
recommended placement.

Rules are evaluated **last match wins**, so ordering within the `permission` block matters:
put broad rules first and exceptions after them. To customise, edit that block; the
[OpenCode permissions docs](https://opencode.ai/docs/permissions/) have the full syntax.

Be clear about what this layer cannot do. Glob patterns match command *identity*, not
*cost*, so they can gate `find` but cannot allow a bounded `find`. They are also evadable:
`ls -R` is gated, `ls -Rla` is not.

## Local mode: the policy boundary

In local mode the enforcement point moves off the prompt and into
[`hpc-mcp`](../hpc-mcp/), the MCP bridge running on your own machine. It exposes cluster
operations as bounded tools rather than a shell, refuses compute on the login node and
recursive traversal of the parallel filesystem, and names the alternative whenever it
refuses. Your `.hpc-assistant.json` is the boundary it enforces. See
[LOCAL-MODE.md](LOCAL-MODE.md).

`hpc-assistant-init` also writes a permission overlay alongside it: `"hpc_*": "ask"` so
every tool from the `hpc` MCP server prompts before it runs, `"external_directory": "deny"`
so Read/Glob cannot leave the project, `"ls *"` / `"find *"` / `"cat *"` set to `"deny"` so
bash cannot walk around that, and `edit` denied on `opencode.json`, `.hpc-assistant.json`
and `AGENTS.md` so the assistant cannot rewrite its own policy.

That overlay lives in `opencode.json` — or `.claude/settings.json` for Claude Code — and
not in `.hpc-assistant.json`, because the bridge cannot show an approval dialog. The two
layers are therefore complementary: the bridge bounds what is reachable, the harness
decides what gets asked.

## Reporting a vulnerability

Report security vulnerabilities privately — see [SECURITY.md](../SECURITY.md).
