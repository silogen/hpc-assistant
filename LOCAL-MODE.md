<!--
Copyright Advanced Micro Devices, Inc.

SPDX-License-Identifier: MIT
-->

# Local mode

Run the assistant on your own machine and use the cluster login node only for the things
that genuinely have to happen there: submitting jobs, querying the scheduler, and reading
bounded slices of files.

This is the recommended way to run the HPC Assistant. Everything expensive — reading,
searching, editing, and reasoning — happens on your machine, where it costs the shared
system nothing.

## Why not just run it on the login node

Because the operations an assistant performs most often are the ones a shared login node
tolerates least. Exploring an unfamiliar codebase means listing directories and searching
file contents, and on a parallel filesystem such as Lustre every entry visited is a
request to a metadata server shared by the whole machine. One `grep -r` over a project
tree is enough to slow the filesystem down for everyone logged in.

Moving the agent to your machine makes that class of operation free rather than merely
discouraged: the files it searches are on local disk, so no amount of searching touches
the cluster at all.

## What you need

- The cluster reachable over SSH from your machine, with your key already working.
- `ssh` (OpenSSH 6.7 or newer, for connection multiplexing).
- An agent framework installed locally: [OpenCode](https://opencode.ai) or
  [Claude Code](https://docs.anthropic.com/en/docs/claude-code).
- A local clone of this repository, for `hpc-assistant-local-setup`.
- Python 3.10 or newer, for the bridge that gives the agent its cluster tools. Nothing
  else: the setup script builds its own virtualenv, so `uv` and `pipx` are optional.

You do not need Singularity, and you do not need anything installed on the cluster.

## Step 1 — configure SSH multiplexing

Do this first. Without it, every single operation the assistant performs opens and
authenticates a brand new SSH connection, which is a worse load profile on the login node
than running the assistant there in the first place. With it, a session of hundreds of
operations authenticates once.

Add to `~/.ssh/config`:

```ssh-config
Host lumi
    HostName lumi.csc.fi
    User <your-lumi-username>
    IdentityFile ~/.ssh/id_ed25519
    ControlMaster auto
    ControlPath ~/.ssh/cm-%r@%h:%p
    ControlPersist 10m
    ServerAliveInterval 60
```

Then confirm it works:

```bash
ssh lumi true      # opens the master connection
ssh -O check lumi  # should report a live master process
```

`hpc-assistant-init --local` warns you if multiplexing is not enabled for the host you
name, so you do not have to remember to verify this yourself.

Two details matter beyond performance. Naming a **host alias** rather than
`user@hostname` means your `ProxyJump`, certificate, and MFA settings keep working, and it
means the assistant never needs to know anything about your credentials. And keeping
`ControlPath` inside your own home directory means the socket is yours, not something
shared.

## Step 2 — set up the project and the bridge

On the cluster, `module load hpc-assistant` does the installing for you. On your own
machine there is no module system to lean on, so one script stands in for it. Work in a
local clone of the project you want help with:

```bash
git clone <your-project> ~/projects/myapp
cd ~/projects/myapp

~/projects/hpc-assistant/bin/hpc-assistant-local-setup \
  --remote-host lumi \
  --remote-root /scratch/project_465000000/myuser/myapp \
  --account project_465000000
```

That installs the bridge into a virtualenv of its own under
`~/.local/share/hpc-assistant/`, using the pinned graph in
[`hpc-mcp/requirements.txt`](hpc-mcp/requirements.txt), initialises the
project, registers the bridge in your agent config **by absolute path**, and finishes by
opening a connection to the cluster to prove the whole chain works. Re-running it is safe:
the virtualenv is reused and existing project files are left alone unless you pass
`--force`. Run it again after pulling a newer version of this repository, and
`--uninstall` to remove the virtualenv again.

The absolute path matters more than it looks. Agents are often launched from a desktop
environment that never sourced a shell profile, so a bare `hpc-mcp` on `PATH` is exactly
the kind of thing that works in your terminal and not in the app.

`--remote-root` is required and has no default: it is the boundary the assistant is
confined to, so it has to be a deliberate choice. Give a concrete path — shell variables
such as `$USER` are not expanded.

Everything the script does not recognise is passed through to `hpc-assistant-init`, so
`--framework claude-code`, `--partition`, `--account`, and the rest work here
too. Its own options are `--venv`, `--python`, `--reinstall`, `--link`, `--no-check`, and
`--uninstall`; `hpc-assistant-local-setup --help` lists them, and
`HPC_ASSISTANT_PIP_ARGS` is there for networks that need a proxy, an internal index, or a
particular CA bundle.

This writes into the project:

| File | Purpose |
| --- | --- |
| `.hpc-assistant.json` | The policy boundary: remote host, project root, optional read-only `readable_paths`, account, preamble, and the bounds the assistant honours |
| `AGENTS.md` or `CLAUDE.md` | System prompt, with a local-mode section appended |
| `opencode.json` | Agent config, with a local-mode permission overlay (`hpc_*` and bypass SSH/rsync require approval; local paths outside the project are denied) and the bridge registered |
| `.mcp.json` | Claude Code only: the bridge registration |
| `.claude/settings.json` | Claude Code only: `permissions.ask` for every `hpc` MCP tool; reads outside the working directory are blocked |
| `.opencode/skills/` or `.claude/skills/` | The site skills |

Read `.hpc-assistant.json` before your first session. Its `limits` block is the policy
boundary, and its `preamble` block is where you declare the modules and environment your
remote commands need. Fill the preamble in: every command the assistant sends to the
cluster is wrapped in it, because a non-interactive SSH session has no `module` command
and no shell profile.

`remote.readable_paths` is the extra read-only roots (on LUMI, the site container
directories). Writes stay inside `remote.project_root`. Paths are checked as written, not
after following a symlink: use `/appl/local/containers/...`, not the `/pfs/lustrep3/...`
target `readlink` prints. `site_info` lists the entries; do not guess one.

`--inference anthropic|aitta|custom-openai` is chosen at init (default `anthropic`) and
written into the agent config. Re-run setup after pulling this repository to refresh the
bridge; then quit the agent fully so it reloads `hpc-mcp`. `--reinstall` rebuilds the
virtualenv from scratch.

```json
"preamble": {
  "modules": ["LUMI/24.03", "partition/G", "rocm/6.0.3"],
  "env": { "MPICH_GPU_SUPPORT_ENABLED": "1" },
  "commands": ["source .venv/bin/activate"]
}
```

### Doing it in pieces

The setup script is a convenience over two independent steps, and either can be run on its
own. If you already manage Python tools with `uv` or `pipx`, installing the bridge yourself
and then initialising the project is equivalent:

```bash
uv tool install ~/projects/hpc-assistant/hpc-mcp   # or: pipx install ...

cd ~/projects/myapp
hpc-assistant-init --local \
  --remote-host lumi \
  --remote-root /scratch/project_465000000/myuser/myapp \
  --account project_465000000
```

`hpc-assistant-init` finds the bridge on `PATH`, or falls back to `uvx --from`, and tells
you when it cannot find it at all. To pin a particular one, set `HPC_MCP_COMMAND` to its
path — that is the hook the setup script uses.

### Checking it

Whichever way you installed it, this is the check the setup script runs for you, and the
one to re-run whenever something looks wrong:

```bash
cd ~/projects/myapp
hpc-mcp --check
```

If you used the setup script, the bridge lives in its virtualenv rather than on your
`PATH`, so either call it by path
(`~/.local/share/hpc-assistant/venv/bin/hpc-mcp --check`) or pass `--link` once to get a
symlink in `~/.local/bin`.

This validates the config, opens the SSH connection, runs your preamble on the cluster, and
tells you whether your workspace is on a network mount. Checking the preamble here is worth
the few seconds: a wrong module name otherwise surfaces an hour later as a puzzling failure
inside a batch job.

A busy login node closes the occasional cold connection, so a single refusal is not a
verdict — the bridge retries once on its own, and so does the setup script.

The agent gets fifteen tools. The scheduler ones are named after the commands they wrap —
`sinfo`, `squeue`, `sacct`, `sbatch`, `scancel`, `scontrol_show_job` — so the site skills
keep working without translation. The ones worth knowing about are `site_info`, which
answers the questions an agent would otherwise answer by probing the filesystem;
`run_on_compute`, which writes a batch script, submits it, and returns the job id.
After submit, the agent reports the id and stops. It checks with `squeue` or `sacct`
only when you ask — it does not wait, and it does not poll. `await_job` exists for an
explicit wait; it is not the path after submit.

## Step 3 — how files are shared

Files move with **rsync**. Nothing in the bridge moves a working tree: its file tools read
and write single bounded files, so a transfer command has to exist alongside them. Setup
writes one into the project.

```bash
./sync-to-remote.sh          # dry run, shows what would move
./sync-to-remote.sh --go     # transfer
```

It never passes `--delete`. The remote copy may hold job output that exists nowhere else,
and mirroring over it would destroy work rather than synchronise it. Its exclude list is
the one that takes effect — `file_access.rsync.excludes` in `.hpc-assistant.json` is not
read by any code, so keep the two in step.

The excludes cover `.hpc-assistant/`, where the bridge writes batch scripts and job logs
on the cluster, and the agent scaffolding (`.opencode/`, `AGENTS.md`, `opencode.json`,
`.hpc-assistant.json`), which is local by definition and whose paths refer to your own
machine.

Two copies of the tree exist, and they diverge silently. Treat "is it pushed?" as a real
question: a missing file on the cluster is more often an unsynced one than a failed job.

## Step 4 — run it

```bash
cd ~/projects/myapp
export AITTA_TOKEN=<your-token>   # or ANTHROPIC_API_KEY
opencode
```

Model traffic goes from your machine to the provider. Nothing is sent to a model provider
from the cluster.

If you use Claude Code with Aitta or another OpenAI-compatible endpoint, you still need
the LiteLLM translation proxy, because Claude Code speaks only the Anthropic API. In local
mode that proxy runs on your machine instead of on a shared node.

## Step 5 — check it against your cluster

`hpc-mcp --check` confirms the configuration loads, the connection opens, and the preamble
runs. Do that first, because everything else depends on it:

```bash
cd ~/projects/myapp
hpc-mcp --check
```

That is a connection test, not a validation of what the tools do once connected. For the
latter there is a harness that drives the assembled server the way an agent does, so a
passing run says the agent's path works rather than that some internal function does:

```bash
python hpc-mcp/scripts/validate_cluster.py                     # read-only
python hpc-mcp/scripts/validate_cluster.py --submit-job \
    --partition debug --gpus 0                                 # plus one real job
```

It is read-only by default: writes go to a scratch directory under `remote.project_root`
and are removed afterwards, and nothing reaches the scheduler beyond `sbatch --test-only`
unless you pass `--submit-job`. Add `--create-root` the first time if the project root does
not exist yet.

The harness checks the things that are properties of your cluster rather than of this
repository, and are therefore the things unit tests cannot settle: whether Lmod is where the
bootstrap looks for it, whether your coreutils understand `find -printf` and
`dd iflag=skip_bytes`, whether the scheduler accepts a pre-flight, whether a multiplexed
connection survives between calls, and whether the refusals still hold with your project
root substituted in. It also prices your preamble, which is worth knowing: on LUMI,
`module load LUMI/24.03` adds between one and two and a half seconds to every command that
needs it, against a round trip of about one second.

Run it after changing `.hpc-assistant.json`, and when moving to a new cluster. A site that
reaches storage through a symlink, or that ships BSD rather than GNU coreutils, will show up
here rather than in the middle of a session.

## What changes in the agent's behaviour

Cluster work goes through the bridge, and the bridge refuses these regardless of what you
or the model asks for. Compute on the login node — compilers, interpreters, package
managers, archivers, profilers — is refused and pointed at `run_on_compute`. A project
script (`./run.sh` or `/scratch/.../run.sh`) is the same: leave the file in the project
and submit it as a job; it does not run on the login node. Recursive traversal and
content search on the cluster are refused and pointed at your local working copy. Anything
that could evade those checks — `bash -c`, `awk`/`sed`/`vim` that can run a program,
`git -c` / `git submodule foreach`, a path smuggled in `--flag=/etc/passwd` — is refused
because it would put every other bound out of reach.

Refusals always name an alternative. That is not politeness: a bare refusal gets retried in
a slightly different form, while a redirection actually changes what the model does next.

Alongside the bridge, the permission overlay adjusts four things.

Every tool from the `hpc` MCP server requires approval (`hpc_*` in OpenCode,
`mcp__hpc__*` in Claude Code). The bridge itself cannot prompt: it runs whatever it
allows. Approval is the agent harness, applied at init, not a key in
`.hpc-assistant.json`.

Local paths outside the project directory are denied (`external_directory` in OpenCode,
`blockReadsOutsideWorkingDirectories` in Claude Code). That check does not stop bash:
the login-node template allows `ls *`, which is how `ls /elsewhere` ran after Read
was refused. The overlay therefore denies `ls *`, `cat *`, `find *`, `grep *`, and
the other path-taking file commands, and also any command whose argument is an
absolute path, a home path, or `..`. Bare `ls` of the cwd stays allowed. Listing
a subdirectory is Glob/Read, not bash.

Raw `ssh`, `scp`, `sftp` and `rsync` require approval. This
is the point: those are the ways to reach the cluster around the bridge, which means around
every bound in `.hpc-assistant.json`. They remain available for the cases the tools do not
cover, but as your decision rather than the model's.

Searching the local working copy is allowed via Read, Glob, and Grep, since on
local disk it is cheap. Those tools honour `external_directory`; bash `ls *`
does not.

Edits of `opencode.json`, `.hpc-assistant.json`, `AGENTS.md`, and `CLAUDE.md` are
denied. Those files are the policy; rewriting them from inside the session is how
the bound disappears. Change them yourself, outside the assistant.

## Limitations today

- **GEAK is not supported in local mode.** Its job scripts and enforcement hooks assume
  the agent is running on the cluster. `--local --geak` is rejected rather than silently
  producing something broken.
- **Nothing syncs your files for you.** The bridge reads and writes individual files within
  your project root, and it writes the batch scripts it submits, but moving a working tree
  between your machine and the cluster is still your `git push` or `rsync` — see step 3.
  Setup writes a `sync-to-remote.sh` into the project: it
  dry-runs by default, transfers only with `--go`, and never passes `--delete`. Its exclude
  list is the one that takes effect, because `file_access.rsync.excludes` in
  `.hpc-assistant.json` is not read by any code — keep the two in step.
- **The agent's own file tools are unbounded inside the project.** `Read`, `Glob` and
  `Grep` are not shell commands and not MCP tools. Inside the working copy that is the
  point: the files are on local disk. Outside it they are denied. Bash `ls *` is
  denied too, so it cannot replace a refused Read. A command that hides the path
  inside a string (`python3 -c`) still falls through to the default bash `ask`.
- **`sacct` lags.** A job that has just finished can report `UNKNOWN` for a few seconds
  before accounting catches up. A single `squeue` plus `sacct` is enough to say so;
  do not wait in a loop to guess.

## Troubleshooting

**`ssh -O check` says no master process.** The alias has no `ControlMaster auto`, or the
master exited. Run `ssh lumi true` and check again.

**Authentication fails repeatedly.** Fix it yourself rather than asking the assistant to
work around it — it is instructed to refuse credential handling. Check `ssh-add -l`. The
bridge runs `ssh` in batch mode and never prompts, so anything needing an answer from you
has to be answered in a terminal: `ssh lumi true` first, then start the agent.

**The agent says it has no cluster tools.** The bridge is not starting. Run `hpc-mcp --check`
in the project directory to see why; the usual causes are that `hpc-mcp` is not on `PATH`
or that the agent was launched from a directory above the project. Check what the agent is
actually told to run — the `command` array under `mcp.hpc` in `opencode.json`, or
`mcpServers.hpc` in `.mcp.json`. `hpc-assistant-local-setup` writes an absolute path there
precisely so that a session does not depend on how the agent was launched.

**The install cannot verify PyPI's certificate.** A virtualenv's `pip` verifies TLS against
its own bundled CA list rather than the store the interpreter uses, so behind a
TLS-inspecting proxy `python3 -c 'urllib.request.urlopen(...)'` can succeed while the
install fails. `hpc-assistant-local-setup` retries with the interpreter's own CA bundle,
which is usually enough; otherwise pass what your network needs through
`HPC_ASSISTANT_PIP_ARGS`.

**Every remote command fails with a module error.** The `preamble` in
`.hpc-assistant.json` is wrong. `hpc-mcp --check` runs it in isolation and shows the error.

**A tool call hangs and never returns.** Usually a dropped SSH master (`ssh -O check`
the host alias), or `list_dir` / `find` over a large tree such as
`/appl/local/containers` at depth greater than 1. List with `run_command` `ls` on the
`/appl/local/...` spelling instead; OpenCode's MCP call times out around 60 seconds.

**The agent wants to `grep` the cluster.** It should not. If it happens repeatedly, check
that the local-mode section is present in your `AGENTS.md` or `CLAUDE.md` — re-run
`hpc-assistant-init --local --force` to restore it.
