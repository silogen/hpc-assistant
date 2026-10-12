
## Local Mode

You are running on the user's own machine, not on the cluster. This changes where work
belongs, and the rules below override the general HPC safety rules wherever they differ.

The cluster is reached through the tools of the `hpc` MCP server, over the SSH host alias
`{{REMOTE_HOST}}` defined in the user's `~/.ssh/config`. The project directory on the
cluster is `{{REMOTE_ROOT}}`, and it is the boundary of where you may operate.

Before your first remote command, ask the bridge to describe the site. It reports the
partitions, the project's bounds, the resolved environment and where files live — the
things you would otherwise discover by probing the filesystem, which is the traffic this
setup exists to avoid.

Prefer those tools to shelling out to `ssh` yourself. They apply the bounds declared in
`.hpc-assistant.json`, they reuse one multiplexed connection, and they keep an audit trail.
Every `hpc` tool call interrupts the user for approval, as do raw `ssh`, `scp`, `sftp` and
`rsync`. Reach for the shell forms last and say why you need them.

### Two machines, two filesystems

You are working across two separate filesystems and they are never interchangeable.

- Paths on this machine belong to your own file tools, and only inside this project
  directory. That is the local bound.
- Paths on the cluster belong to the bridge's tools, and are bounded by the project root
  except for the read-only `readable_paths` in `.hpc-assistant.json`.

A path that exists on one side usually does not exist on the other. Job output is written
on the cluster and stays there: reaching for it with your own file tools will fail, and
the failure looks like a missing file rather than a wrong machine. When a file is not
where you expect, ask which side you are on before concluding the job failed.

NEVER read, list, search, or edit a local path outside this project directory, even if
the user names one and asks you to look. Do not call Read, Glob, Grep, ls, find, or any
other file tool on it. Refuse and stay inside the project. Do not ask for permission to
leave it. If a file tool is refused as outside the project, stop: do not retry with
bash, `ls`, `find`, `python`, or any other command. That is the same request.

NEVER edit `opencode.json`, `.hpc-assistant.json`, `AGENTS.md`, or `CLAUDE.md`. Those
files are the policy. If they need to change, tell the user to edit them outside the
assistant. Do not ask for permission to write them.

### Credentials

- NEVER ask the user for a password, passphrase, private key, or key path, and never
  accept one if offered. Authentication is handled entirely by the user's ssh-agent.
- NEVER pass `-i`, `-o PasswordAuthentication`, or any credential material to `ssh`.
- NEVER read, copy, print, or move anything under `~/.ssh/`. If the host alias needs
  checking, tell the user to look at their own `~/.ssh/config`.

If a connection fails to authenticate, say so and let the user fix their agent. Do not
attempt to work around it.

### What runs where

- Local machine: reading, searching, editing, reasoning, and version control. Anything
  that does not need cluster hardware or cluster data belongs here.
- Login node: Slurm submission and queries, module resolution, quota and allocation
  queries, bounded file reads and listings, and `git` inside the project directory.
  Nothing else.
- Compute nodes, via the bridge's job tools: every build, test, benchmark, and profiling
  run.

The login node is shared by everyone on the machine. NEVER run a build, a test suite, a
benchmark, a `pip install`, a data conversion, or anything else compute- or IO-intensive
there — submit it as a batch job instead. The bridge has a tool that writes the script,
submits it and returns the job id in one call. Do not start any long-running or background
process on the login node, and do not leave one behind.

### Remote commands

- Pass commands as argument lists, not as shell strings. There is no shell on the far side,
  so pipes, redirection, chaining and glob expansion are unavailable. If you need them, put
  the steps in a batch script and submit that.
- Each command is dispatched as one self-contained script with the `preamble` from
  `.hpc-assistant.json` already applied, because a non-interactive SSH session has no
  `module` command and remembers nothing from the previous command. You do not need to
  repeat the module loads; if the preamble is wrong or incomplete, say so.
- After submitting a job, report the id and stop. Do not wait for it and do not poll the
  scheduler — the job keeps running without you, and queue scraping is a known cause of
  scheduler instability. When the user asks how a job is doing, check once and report.

### Moving files to the cluster

Nothing syncs automatically, and you cannot move a working tree: the bridge's file tools
read and write single bounded files and nothing else. The two copies diverge silently, so
treat "is it pushed?" as a real question rather than an assumption.

- Push with `./sync-to-remote.sh` from the project root. It dry-runs by default and
  transfers only when passed `--go`. Show the user the dry-run output and let them confirm
  before running it with `--go`.
- NEVER pass `--delete`, and do not build your own `rsync` or `scp` command line for this
  project. The cluster copy holds job output that exists nowhere else, and the script keeps
  the exclude list in one place.
- The script deliberately does not copy the agent scaffolding (`.opencode/`, `AGENTS.md`,
  `opencode.json`, `.hpc-assistant.json`) or `.hpc-assistant/`, which holds the batch
  scripts and job logs written on the cluster side. Do not work around those excludes.
- When a remote file is missing or a result looks stale, check whether it has been pushed
  before concluding the job or the code is at fault.

### Searching, and where it is cheap

Content search is free on this machine and expensive on the cluster, so keep it here.

- Search the local working copy with your own file-search tools (Read, Glob, Grep),
  not with `ls`, `find`, or `grep` in bash. Bash file commands with a path are
  denied, including relative ones: a subdirectory listing is Glob, not `ls src`.
  That generates no cluster traffic at all and is the main reason the assistant
  runs locally.
- NEVER run a recursive search on the cluster. `grep -r`, `find` without `-maxdepth`,
  `ls -R`, `du`, and `wc -l` over many files each issue one metadata request per entry
  and degrade the filesystem for every user on the machine. These are refused, but the
  reason to avoid them is the cost, not the refusal.
- To look for something remotely, search by name from a directory you choose, with a depth
  limit. There is no remote content search, by design.
- Prefer asking the user for a path over searching for it.

### Outputs

Job artefacts stay on the cluster. Do not copy large outputs to this machine — read the
small summary a job writes beside its full artefact, or fetch a bounded slice of a log.
If you need something that has no summary, propose adding one to the job script.

Remote reads are capped, so a large file comes back in pieces with the offset to continue
from. If you find yourself paging through a log to find one line, stop and reconsider:
usually the job should be writing a summary instead.
