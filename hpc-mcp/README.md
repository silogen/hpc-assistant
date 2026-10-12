# hpc-mcp

An MCP server that exposes HPC cluster operations as a small set of bounded tools, over
the user's own SSH connection.

It exists so that a coding agent can work on cluster code from the user's own machine
while the cluster only ever sees a narrow, rate-limited, audited set of operations. See
[`LOCAL-MODE.md`](../LOCAL-MODE.md) one directory up.

## What it does and does not do

It runs on the user's machine and shells out to the system `ssh` client. Nothing is
installed on the cluster.

Authentication stays in the user's `ssh-agent`. There is no parameter anywhere in this
package for a key, a key path, a password or a passphrase, and a test asserts that the
`ssh` argv this package builds contains no option that would introduce one. The remote is
named by an alias from `~/.ssh/config`, so `ProxyJump`, certificates and MFA keep working
exactly as they already do for the user.

Commands are handled as argv lists, never as shell strings. There is no shell to inject
into, and globs reach the remote program literally instead of fanning out over a
directory. Pipes, redirection and chaining are not available; work that needs them belongs
in a batch script.

Three things are refused outright, each with a suggested alternative: compute work on the
login node, recursive traversal of the parallel filesystem, and anything that would put
those checks out of reach, such as `bash -c`.

## Install

Not published to PyPI yet, so install from the clone. Usually you do not install it
directly: `bin/hpc-assistant-local-setup` puts it in a virtualenv of its own and registers
it in your agent config by absolute path. To install it yourself instead:

```bash
uv tool install path/to/hpc-assistant/hpc-mcp    # or: pipx install ...
```

`hpc-assistant-init --local` then registers whatever it finds on `PATH`, and tells you if
there is nothing there. For development, from the repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install -r hpc-mcp/requirements.txt -e 'hpc-mcp[dev]'
.venv/bin/python -m pytest hpc-mcp/tests
```

Eight tests are skipped on macOS: they run the remote filesystem scripts against a real
shell, and those scripts target the GNU coreutils the cluster has. They run on Linux.

## Configure

The server reads `.hpc-assistant.json` from the current directory or the nearest parent.
`hpc-assistant-init --local` writes one. That file is the policy boundary: the project
root outside which no path may be touched, the account jobs are billed to (optional on
sites with a default association), `slurm.default_partition` and `slurm.extra_directives`
for site-specific `#SBATCH` flags, the environment preamble, and the limits.

Check the configuration and the connection before pointing an agent at it:

```bash
hpc-mcp --check
```

This validates the config, opens the connection, runs the declared preamble, and reports
whether the local workspace is on a network mount — a case worth knowing about, because
such a workspace looks like local disk to a file-search tool while costing the cluster a
metadata operation per entry.

## Tools

| Tool | What it does |
| --- | --- |
| `site_info` | Partitions, project bounds, resolved environment. Read this first. |
| `sinfo`, `squeue`, `sacct`, `scontrol_show_job`, `scancel` | Scheduler queries, throttled to one call per interval. |
| `sbatch` | Submits an existing script, after reporting what `--test-only` says. |
| `run_on_compute` | Writes a batch script, submits it, returns the job id. Does not wait unless `wait` is true. |
| `await_job` | Optional: wait until a job ends. Not the path after submit. |
| `read_file`, `tail_file`, `write_file`, `list_dir`, `find_files` | Bounded file access. Search is by name only, and `tail_file` is the one to use on logs. |
| `run_command` | One short command on the login node, within the same bounds. |

The scheduler tools are named after the commands they wrap, so a skill that says "run
`squeue`" maps onto a tool without translation, and the same skill still works for an agent
running on a cluster node where the real command is available.

## Layout

| Module | Responsibility |
| --- | --- |
| `config.py` | Loads and validates `.hpc-assistant.json`. |
| `policy.py` | Path bounds, placement and traversal refusals, throttling, output caps, audit log. |
| `mounts.py` | Decides whether a local path is really on local disk. |
| `preamble.py` | Renders the declared environment into dispatch and batch scripts. |
| `transport/` | `SSHTransport` over multiplexed OpenSSH, `LocalTransport` for on-node agents. |
| `session.py` | The single path every dispatch takes, so no tool can skip a check. |
| `remote_fs.py` | Bounded filesystem script templates. |
| `tools/` | The MCP tool surface. |

Tests in `tests/` are organised around the guarantees rather than the modules:
`test_policy.py` asserts the policy refusals, and `test_transport_ssh.py` asserts the
credential guarantee.

```bash
python -m pytest tests
```

Eight of those tests skip off Linux: they run the filesystem scripts through a real shell,
and those scripts target GNU coreutils.

What the suite cannot cover is whether the scripts mean the same thing on a real login node.
`scripts/validate_cluster.py` answers that by driving the assembled server through
`call_tool` against a cluster you have access to — read-only unless you ask for a job:

```bash
python scripts/validate_cluster.py                                  # read-only
python scripts/validate_cluster.py --submit-job --partition debug --gpus 0
```

Each of these was a real failure it found on LUMI: a missing `tail_file` tool, `pwd`
reporting `/pfs/lustrep3/...` for a project root configured as `/scratch/...`,
`run_command(["module", "list"])` exiting 127 because `exec` cannot run a shell function,
and `module load` on every scheduler query.
