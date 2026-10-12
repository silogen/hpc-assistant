# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# The policy engine.
#
# Login-node policy is enforced here so a new tool cannot skip a check.
#
# Two design choices do most of the work.
#
# Commands are handled as argv lists, never as shell strings. There is no shell to inject
# into, no quoting to get wrong, and no glob expansion — so `wc -l *` passes a literal
# asterisk to wc instead of fanning out over a directory. Validation then means checking
# tokens, and dispatch means quoting them back together, which is the parse-validate-
# reconstruct pattern with the parse step made trivial by construction.
#
# Refusals carry a suggestion. A bare "not allowed" gets retried in a slightly different
# form; "not allowed, submit it with run_on_compute" redirects the model.

from __future__ import annotations

import asyncio
import json
import os
import posixpath
import re
import shlex
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .errors import PolicyViolation
from .mounts import MountTable

# Trailing version so `python3.11` and `gcc-13` match the unversioned names below.
_VERSION_SUFFIX = re.compile(r"[-.](\d+)(\.\d+)*$")

# Work that belongs in a Slurm job. Compilers, interpreters, package managers, archivers
# and profilers are all here for the same reason: on a login node shared by hundreds of
# users, they are the difference between a responsive prompt and a stalled one.
COMPUTE_BINARIES = frozenset(
    {
        "cargo", "clang", "clang++", "cmake", "cmake3", "conda", "configure", "cc", "CC",
        "c++", "curl", "ffmpeg", "ftn", "g++", "gcc", "gfortran", "gdb", "go", "gzip",
        "bzip2", "hipcc", "java", "javac", "jupyter", "julia", "make", "mamba", "matlab",
        "maven", "mpicc", "mpicxx", "mpiexec", "mpirun", "mvn", "ninja", "node", "npm",
        "nvcc", "nsys", "omniperf", "omnitrace", "perf", "pip", "pip3", "pnpm", "poetry",
        "pytest", "python", "python2", "python3", "R", "Rscript", "rocprof", "rocprofv3",
        "rustc", "tar", "unzip", "uv", "valgrind", "wget", "xz", "yarn", "zip", "zstd",
    }
)

# Ways to launch something else, which would put the checks above out of reach.
# Shells and wrappers (env, xargs, timeout) are the obvious cases. Editors, pagers,
# awk/sed, interpreters, and copy tools belong here too: `awk 'BEGIN{system("make")}'`,
# `sed -e '1e make'`, `vim -c ':!make'`, and `rsync -e` all run another program.
ESCAPE_BINARIES = frozenset(
    {
        "at", "awk", "bash", "batch", "crontab", "csh", "dash", "emacs", "env", "eval",
        "exec", "fish", "flock", "gawk", "ionice", "ksh", "less", "lua", "man", "mawk",
        "more", "nano", "nice", "nohup", "parallel", "perl", "php", "rsync", "ruby",
        "scp", "screen", "script", "sed", "setsid", "sftp", "sh", "source", "ssh",
        "stdbuf", "su", "sudo", "tclsh", "time", "timeout", "tmux", "vi", "vim",
        "watch", "xargs", "zsh",
    }
)

# Containers do real work and are also a launcher. Both reasons point the same way.
CONTAINER_BINARIES = frozenset({"apptainer", "docker", "podman", "singularity"})

SHELL_OPERATORS = frozenset(
    {"|", "||", "&&", "&", ";", ">", ">>", "<", "<<", "2>", "2>&1", "$(", "`"}
)

SCHEDULER_BINARIES = frozenset(
    {"sacct", "sbatch", "scancel", "scontrol", "sinfo", "squeue", "sstat", "sprio"}
)

# Commands that may run on the login node. Site-specific quota and allocation tools
# belong in login_commands in .hpc-assistant.json, not here.
DOCUMENTED_LOGIN_BINARIES = frozenset(
    {
        "cat", "date", "df", "dirname", "echo", "basename", "git", "groups", "head",
        "hostname", "id", "ls", "lscpu", "module", "printenv", "pwd", "quota", "readlink",
        "sacct", "sbatch", "scancel", "scontrol", "sinfo", "squeue", "stat", "tail",
        "test", "uname", "wc", "whoami",
    }
)


@dataclass
class Decision:
    """What the engine concluded, for the audit log."""

    allowed: bool
    reason: str = ""


class RateLimiter:
    """Minimum spacing between calls in a family.

    Throttles rather than refuses. A refusal teaches the agent to retry, which is more
    scheduler traffic, not less; waiting is both kinder and more effective.
    """

    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval = min_interval_seconds
        self._last: dict[str, float] = {}

    def delay_for(self, family: str, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        last = self._last.get(family)
        if last is None:
            return 0.0
        elapsed = now - last
        return max(0.0, self.min_interval - elapsed)

    def record(self, family: str, now: float | None = None) -> None:
        self._last[family] = time.monotonic() if now is None else now

    async def acquire(self, family: str) -> float:
        waited = self.delay_for(family)
        if waited > 0:
            await asyncio.sleep(waited)
        self.record(family)
        return waited


def truncate_output(text: str, max_bytes: int) -> tuple[str, bool]:
    """Keep the head and tail of oversized output.

    Both ends, because a failed command's useful information is split between them: what
    it was doing at the start and how it died at the end.
    """
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text, False
    half = max(1, max_bytes // 2)
    head = encoded[:half].decode("utf-8", errors="ignore")
    tail = encoded[-half:].decode("utf-8", errors="ignore")
    omitted = len(encoded) - len(head.encode()) - len(tail.encode())
    marker = f"\n\n[... {omitted} bytes omitted by hpc-mcp output cap ...]\n\n"
    return head + marker + tail, True


class Policy:
    def __init__(self, config: Config, mounts: MountTable | None = None) -> None:
        self.config = config
        self.mounts = mounts or MountTable()
        self.scheduler_limiter = RateLimiter(
            config.limits.scheduler_min_interval_seconds
        )
        self._root_aliases: tuple[str, ...] = ()

    # ── Paths ────────────────────────────────────────────────────────────────────

    def add_root_alias(self, path: str) -> None:
        """Accept a second spelling of the project root and fold it onto the first.

        Cluster storage is routinely reached through a symlink: on LUMI, /scratch/<project>
        resolves into /pfs/lustrep<n>/scratch/<project>, so `pwd`, `scontrol show job` and
        every path in a job log come back with a prefix the config never mentions.
        Refusing those would be literally correct and completely useless, since they name
        the same bytes. Learning the alias keeps one spelling canonical — the one the
        config declares, which is the one the audit log should show.
        """
        cleaned = posixpath.normpath(path).rstrip("/")
        root = self.config.remote.project_root
        if not cleaned or cleaned == "/" or cleaned == root:
            return
        if cleaned not in self._root_aliases:
            self._root_aliases = (*self._root_aliases, cleaned)

    @property
    def root_aliases(self) -> tuple[str, ...]:
        return self._root_aliases

    def _canonical_root(self, resolved: str) -> str:
        """Rewrite an alias-rooted path onto the configured root."""
        root = self.config.remote.project_root
        for alias in self._root_aliases:
            if resolved == alias:
                return root
            if resolved.startswith(alias + "/"):
                return root.rstrip("/") + resolved[len(alias) :]
        return resolved

    def normalize_remote_path(self, path: str, *, write: bool = False) -> str:
        """Resolve a remote path lexically and confirm the assistant may use it.

        Lexically, because the path is on another machine: asking the filesystem would
        cost a round trip and would still be a check-then-use race. `..` is collapsed
        before comparison so `root/../../etc` cannot slip through.

        Everything inside the project root is allowed. Outside it, remote.readable_paths
        may grant reads and never writes, so whatever this bridge can damage is still
        exactly the project root. Pass write=True for anything that creates or changes a
        file.
        """
        if not path or not path.strip():
            raise PolicyViolation("An empty path was requested.")
        if "\x00" in path:
            raise PolicyViolation("Paths must not contain null bytes.")

        root = self.config.remote.project_root
        candidate = path if posixpath.isabs(path) else posixpath.join(root, path)
        # Normalised before the alias is applied, so an alias cannot be reached by a
        # traversal that only looks like one after collapsing.
        resolved = self._canonical_root(posixpath.normpath(candidate))

        if resolved == root or resolved.startswith(root.rstrip("/") + "/"):
            return resolved

        in_readable = any(
            resolved == allowed or resolved.startswith(allowed + "/")
            for allowed in self.config.remote.readable_paths
        )
        if in_readable and not write:
            return resolved
        if in_readable:
            raise PolicyViolation(
                f"{resolved} is readable but not writable.",
                "remote.readable_paths grants reads only. Write inside "
                "remote.project_root instead.",
            )
        raise PolicyViolation(
            f"{resolved} is outside the project root {root}.",
            "Only paths inside remote.project_root may be accessed. If the assistant "
            "needs to read something a site shares, such as a container directory, add "
            "it to remote.readable_paths in .hpc-assistant.json.",
        )

    def assert_local_disk(self, path: str | os.PathLike[str]) -> Path:
        """Confirm a local path is really local, and inside the workspace.

        A path being local is not the same as a path being on local disk. If the
        workspace is an sshfs mount, reading and searching it costs the cluster exactly
        what doing the work remotely would cost — plus SFTP overhead.
        """
        target = Path(os.path.expanduser(str(path)))
        resolved = target if target.is_absolute() else Path.cwd() / target
        resolved = Path(posixpath.normpath(str(resolved)))

        local_root = Path(self.config.file_access.local_root)
        if local_root != resolved and local_root not in resolved.parents:
            raise PolicyViolation(
                f"{resolved} is outside the local workspace {local_root}.",
                "Only paths inside file_access.local_root may be read locally.",
            )
        if self.mounts.is_remote(resolved):
            raise PolicyViolation(
                f"{resolved} is on a network filesystem "
                f"({self.mounts.describe(resolved)}), not local disk.",
                "Reading or searching it costs the cluster the same as doing the work "
                "remotely. Use the remote tools for individual files, and do not "
                "traverse this path.",
            )
        return resolved

    def workspace_is_remote(self) -> bool:
        return self.mounts.is_remote(self.config.file_access.local_root)

    # ── Commands ─────────────────────────────────────────────────────────────────

    def _binary(self, argv: list[str]) -> str:
        return posixpath.basename(argv[0])

    def _placement_binary(self, argv: list[str]) -> str:
        """Basename used for denylist lookup, with a trailing version stripped.

        `python3.11` and `gcc-13` are the same programs as `python3` and `gcc`; matching
        only the unversioned spelling is how they used to reach the login node.
        """
        binary = self._binary(argv)
        stripped = _VERSION_SUFFIX.sub("", binary)
        return stripped if stripped else binary

    def validate_argv(
        self, argv: list[str], *, tool: str, enforce_placement: bool = True
    ) -> list[str]:
        """Check an argv list and return it validated for dispatch.

        Raises PolicyViolation with a suggestion for anything refused.

        `enforce_placement=False` is for commands destined for a batch script, where
        compilers and interpreters are the whole point. The traversal checks still apply:
        a recursive search from a compute node hits the same shared metadata servers.
        """
        if not isinstance(argv, list) or not argv or not all(
            isinstance(a, str) for a in argv
        ):
            raise PolicyViolation(
                "A command must be a non-empty list of strings.",
                "Pass the command as argv, for example [\"squeue\", \"-u\", \"$USER\"], "
                "not as a single shell string.",
            )
        for token in argv:
            if "\x00" in token:
                raise PolicyViolation("Command arguments must not contain null bytes.")
            if token in SHELL_OPERATORS:
                raise PolicyViolation(
                    f"{token!r} is a shell operator and there is no shell here.",
                    "Commands run as argv lists, so pipes, redirection and chaining are "
                    "not available. Run the steps as separate calls, or put them in a "
                    "batch script and submit it with sbatch.",
                )

        binary = self._binary(argv)
        if enforce_placement:
            self._reject_placement(binary, argv)
        self._reject_metadata_storms(binary, argv)
        self._reject_out_of_bounds_paths(argv)
        return argv

    def _reject_placement(self, binary: str, argv: list[str]) -> None:
        requested = argv[0]
        if self._is_project_executable(requested):
            raise PolicyViolation(
                f"`{requested}` is a project script and will not run on a login node.",
                "Leave the file where it is and submit it with run_on_compute, or sbatch "
                "it. Scripts belong in the project directory; they run on a compute node, "
                "because the login node is shared by everyone on the machine.",
            )
        canonical = self._placement_binary(argv)
        if canonical in COMPUTE_BINARIES:
            raise PolicyViolation(
                f"`{binary}` is compute work and will not run on a login node.",
                "Submit it with run_on_compute, or write a batch script and use sbatch. "
                "The login node is shared by everyone on the machine.",
            )
        if canonical in CONTAINER_BINARIES:
            raise PolicyViolation(
                f"`{binary}` runs a container, which belongs in a Slurm job.",
                "Use run_on_compute, or sbatch a script that invokes the container.",
            )
        if canonical in ESCAPE_BINARIES:
            raise PolicyViolation(
                f"`{binary}` can launch other programs, so it would put every other "
                f"bound out of reach.",
                "Call the program you actually want directly, or submit the work as a "
                "batch job where a shell is expected.",
            )
        if canonical in {"srun", "salloc"}:
            raise PolicyViolation(
                f"`{binary}` holds an allocation for as long as it runs.",
                "Use run_on_compute or sbatch, which submit a batch job and return, "
                "so nothing is held open on the login node.",
            )
        if self._binary(argv) == "git":
            self._reject_git_launchers(argv)

    def _reject_git_launchers(self, argv: list[str]) -> None:
        """Git is allowed on the login node; the ways it starts another program are not.

        `git -c core.pager=make log` and `git submodule foreach make` are the realistic
        cases. The second does not need a clever agent: clone a repo with submodules,
        ask the assistant to set it up, and the command comes out of `.gitmodules`.
        """
        global_opts, command, rest = _split_git_invocation(argv)
        pager = _git_pager_from_options(global_opts)
        paginate = any(tok in {"-p", "--paginate"} for tok in global_opts)

        if paginate and _is_non_default_git_pager(pager):
            raise PolicyViolation(
                "`git --paginate` with a non-default pager runs that pager, so it would "
                "put every other bound out of reach.",
                "Call git without --paginate, or leave the pager as less, more, or cat.",
            )
        if any(_is_git_config_override(tok) for tok in global_opts):
            raise PolicyViolation(
                "`git -c` can set core.pager, alias, sshCommand and other keys that run "
                "a program, so it would put every other bound out of reach.",
                "Run git without -c on the login node. Configuration that launches a "
                "program belongs in a batch job.",
            )
        if any(tok == "--exec-path" or tok.startswith("--exec-path=") for tok in global_opts):
            raise PolicyViolation(
                "`git --exec-path` replaces Git's helper programs, so it would put every "
                "other bound out of reach.",
                "Call git without --exec-path.",
            )
        if command == "submodule" and _first_git_positional(rest) == "foreach":
            raise PolicyViolation(
                "`git submodule foreach` runs a command from .gitmodules in each "
                "submodule, so it would put every other bound out of reach.",
                "Use `git submodule update --init` on the login node, or run foreach "
                "inside a batch job with run_on_compute.",
            )
        if command == "config" and _git_config_writes_launcher(rest):
            raise PolicyViolation(
                "`git config` can persist core.pager, alias and sshCommand, which is "
                "the same as `git -c` on the next call.",
                "Read git config on the login node if you need to, but set keys that "
                "launch a program in a batch job.",
            )

    def _is_project_executable(self, argv0: str) -> bool:
        """True when argv[0] names a file under the project root, any spelling.

        PATH lookups (`ls`, `squeue`) are not paths. `./run.sh` and
        `/scratch/.../work/run.sh` are the same bytes: something the user or the
        agent authored. Neither belongs on a login node. Absolute paths to system
        tools (`/usr/bin/ls`) resolve outside the root and still use the basename
        check.
        """
        if "/" not in argv0:
            return False
        root = self.config.remote.project_root
        candidate = argv0 if posixpath.isabs(argv0) else posixpath.join(root, argv0)
        resolved = self._canonical_root(posixpath.normpath(candidate))
        return resolved == root or resolved.startswith(root.rstrip("/") + "/")

    def _reject_metadata_storms(self, binary: str, argv: list[str]) -> None:
        """Refuse the traversals that degrade a parallel filesystem for everyone.

        This is the single highest-value guard in the bridge. One recursive search over a
        project tree is enough to slow the shared metadata servers noticeably.
        """
        args = argv[1:]

        if binary == "find":
            depth = _find_maxdepth(args)
            limit = self.config.limits.max_find_depth
            if depth is None:
                raise PolicyViolation(
                    "`find` without -maxdepth walks the whole tree.",
                    f"Add -maxdepth with a value of {limit} or less, and start from the "
                    f"narrowest directory that could contain the answer.",
                )
            if depth > limit:
                raise PolicyViolation(
                    f"-maxdepth {depth} exceeds the configured limit of {limit}.",
                    "Search a narrower subtree, or raise limits.max_find_depth in "
                    ".hpc-assistant.json if you accept the cost.",
                )
            if any(a in {"-exec", "-execdir", "-delete", "-ok", "-okdir"} for a in args):
                raise PolicyViolation(
                    "`find` actions such as -exec and -delete are not available.",
                    "List the matches first, then act on the specific paths you want.",
                )

        if binary in {"grep", "egrep", "fgrep", "rg", "ag", "ack"}:
            recursive = any(
                a in {"-r", "-R", "--recursive", "--dereference-recursive"}
                or (a.startswith("-") and not a.startswith("--") and (
                    "r" in a[1:] or "R" in a[1:]
                ))
                for a in args
            )
            if recursive or binary in {"rg", "ag", "ack"}:
                raise PolicyViolation(
                    "Recursive content search is not available on the cluster.",
                    "Search the working copy on your own machine instead — that is why "
                    "it is there, and it costs the cluster nothing. To find a file by "
                    "name remotely, use find_files.",
                )

        if binary == "ls":
            if any(
                a.startswith("-") and not a.startswith("--") and "R" in a[1:]
                for a in args
            ) or "--recursive" in args:
                raise PolicyViolation(
                    "`ls -R` walks the whole tree.",
                    "Use list_dir, which is depth-limited and entry-capped.",
                )

        if binary in {"du", "ncdu"}:
            raise PolicyViolation(
                f"`{binary}` stats every file beneath its argument.",
                "If you need usage figures, ask site_info for the project's bounds and "
                "use the site quota tools declared in login_commands rather than walking "
                "the tree.",
            )

        if binary in {"tree", "locate", "updatedb"}:
            raise PolicyViolation(
                f"`{binary}` enumerates directory trees.",
                "Use list_dir for a bounded listing, or find_files to look up a name.",
            )

    def _reject_out_of_bounds_paths(self, argv: list[str]) -> None:
        """Keep path operands inside the project root.

        Only tokens that look like paths are checked, so `openai/gpt-oss-120b` is not
        mistaken for one. A leading dash is not a skip: `--follow=/etc/passwd` and
        `git -C/etc` name a path inside the flag, and skipping them is how a bound
        becomes advisory.
        """
        for token in argv[1:]:
            for candidate in _path_operands(token):
                self.normalize_remote_path(candidate)

    def assert_documented_login_command(self, argv: list[str]) -> None:
        """Warn-free check that a command is one of the documented login-node binaries.

        Used for the site_info self-report rather than for enforcement: the named tools
        are the supported path, and run_command is deliberately broader so that an
        unanticipated need does not require a code change.
        """
        binary = self._binary(argv)
        allowed = DOCUMENTED_LOGIN_BINARIES | frozenset(self.config.login_commands)
        if binary not in allowed:
            raise PolicyViolation(
                f"`{binary}` is not in the documented set of login-node commands.",
                "It may still be legitimate. Site-specific tools can be listed under "
                "login_commands in .hpc-assistant.json.",
            )

    def is_scheduler_command(self, argv: list[str]) -> bool:
        return self._binary(argv) in SCHEDULER_BINARIES

    # ── Output and audit ─────────────────────────────────────────────────────────

    def truncate(self, text: str) -> tuple[str, bool]:
        return truncate_output(text, self.config.limits.max_output_bytes)

    def audit(self, tool: str, decision: Decision, **fields: object) -> None:
        """Append one JSON line per decision.

        Best-effort: an unwritable audit log must not take the assistant down, but it is
        reported once rather than swallowed silently.
        """
        record = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "tool": tool,
            "allowed": decision.allowed,
            **({"reason": decision.reason} if decision.reason else {}),
            **fields,
        }
        path = self.config.audit_log_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, default=str) + "\n")
        except OSError:
            pass


_GIT_DEFAULT_PAGERS = frozenset({"less", "more", "cat"})
_GIT_GLOBAL_VALUE_OPTIONS = frozenset(
    {"-C", "-c", "--namespace", "--git-dir", "--work-tree", "--config-env"}
)
_GIT_POSITIONAL_VALUE_OPTIONS = frozenset(
    {"--reference", "--depth", "--jobs", "--prefix"}
)


def _is_git_config_override(token: str) -> bool:
    if token == "-c" or token.startswith("--config-env"):
        return True
    return token.startswith("-c") and not token.startswith("--") and token != "-c"


def _git_pager_from_options(global_opts: tuple[str, ...]) -> str | None:
    """core.pager / pager.* set with -c, if any."""
    pager: str | None = None
    skip_next = False
    for index, tok in enumerate(global_opts):
        if skip_next:
            skip_next = False
            continue
        assignment = None
        if tok == "-c" and index + 1 < len(global_opts):
            assignment = global_opts[index + 1]
            skip_next = True
        elif tok.startswith("-c") and tok != "-c" and not tok.startswith("--"):
            assignment = tok[2:]
        elif tok.startswith("--config-env="):
            assignment = tok.split("=", 1)[1]
        if assignment is None or "=" not in assignment:
            continue
        key, _, value = assignment.partition("=")
        key = key.lower()
        if key == "core.pager" or key.startswith("pager."):
            pager = value
    return pager


def _is_non_default_git_pager(pager: str | None) -> bool:
    if pager is None or not pager.strip():
        return False
    binary = posixpath.basename(pager.strip().split()[0])
    return binary not in _GIT_DEFAULT_PAGERS


def _split_git_invocation(
    argv: list[str],
) -> tuple[tuple[str, ...], str | None, tuple[str, ...]]:
    """Separate git wrapper options from the subcommand.

    `git -p log` paginates; `git log -p` is a patch. Only options before the command
    can replace helpers or force a pager.
    """
    tokens = argv[1:]
    index = 0
    global_opts: list[str] = []
    while index < len(tokens):
        tok = tokens[index]
        if tok == "--":
            index += 1
            break
        if not tok.startswith("-"):
            break
        if tok.startswith("--") and "=" in tok:
            global_opts.append(tok)
            index += 1
            continue
        if tok in _GIT_GLOBAL_VALUE_OPTIONS:
            global_opts.append(tok)
            if index + 1 < len(tokens):
                global_opts.append(tokens[index + 1])
                index += 2
            else:
                index += 1
            continue
        if tok.startswith("-C") and tok != "-C":
            global_opts.append(tok)
            index += 1
            continue
        if tok.startswith("-c") and tok != "-c":
            global_opts.append(tok)
            index += 1
            continue
        global_opts.append(tok)
        index += 1
    command = tokens[index] if index < len(tokens) else None
    rest = tuple(tokens[index + 1 :]) if index < len(tokens) else ()
    return tuple(global_opts), command, rest


def _first_git_positional(args: tuple[str, ...]) -> str | None:
    """First non-option argument, skipping flags that take a separate value."""
    skip_next = False
    for index, tok in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if tok == "--":
            return args[index + 1] if index + 1 < len(args) else None
        if tok in _GIT_POSITIONAL_VALUE_OPTIONS:
            skip_next = True
            continue
        if tok.startswith("-"):
            continue
        return tok
    return None


_GIT_LAUNCHER_CONFIG_KEYS = frozenset(
    {
        "core.pager",
        "core.editor",
        "core.sshcommand",
        "core.fsmonitor",
        "core.hookspath",
        "core.gitproxy",
        "diff.external",
        "gpg.program",
    }
)
_GIT_LAUNCHER_CONFIG_PREFIXES = ("alias.", "pager.", "filter.", "mergetool.", "difftool.")
_GIT_CONFIG_READ_FLAGS = frozenset(
    {"--get", "--list", "-l", "--get-regexp", "--get-all", "--get-urlmatch"}
)


def _git_config_writes_launcher(rest: tuple[str, ...]) -> bool:
    """True when `git config` would persist a key that runs another program."""
    if any(tok in _GIT_CONFIG_READ_FLAGS for tok in rest):
        return False
    positionals: list[str] = []
    skip_next = False
    for tok in rest:
        if skip_next:
            skip_next = False
            continue
        if tok.startswith("-"):
            continue
        positionals.append(tok)
    if len(positionals) < 2:
        return False
    key = positionals[0].lower()
    if key in _GIT_LAUNCHER_CONFIG_KEYS:
        return True
    return key.startswith(_GIT_LAUNCHER_CONFIG_PREFIXES)


def _looks_like_remote_path(token: str) -> bool:
    """True when a token should be checked against the project root.

    Absolute paths and any `..` component. A slash alone is a path (`--git-dir=/`);
    a model id such as `openai/gpt-oss-120b` is not.
    """
    if not token:
        return False
    if token.startswith("/"):
        return True
    return ".." in token.split("/")


def _path_operands(token: str) -> tuple[str, ...]:
    """Path-like operands hidden in one argv token, including flag values.

    Space-separated forms (`git --git-dir /etc`) already appear as their own token.
    GNU long options put the operand after `=`, and some short options glue it on
    (`git -C/etc`, `gcc -I/usr/include`). A glued remainder of only `/` is left
    alone so `cut -d/` stays a delimiter; `--git-dir=/` still goes through because
    it came from `=`.
    """
    if token.startswith("--"):
        if "=" not in token:
            return ()
        value = token.split("=", 1)[1]
        return (value,) if _looks_like_remote_path(value) else ()
    if token.startswith("-"):
        if "=" in token:
            value = token.split("=", 1)[1]
            return (value,) if _looks_like_remote_path(value) else ()
        if len(token) > 2:
            rest = token[2:]
            if _looks_like_remote_path(rest) and rest != "/":
                return (rest,)
        return ()
    if _looks_like_remote_path(token):
        return (token,)
    return ()


def _find_maxdepth(args: list[str]) -> int | None:
    for index, arg in enumerate(args):
        if arg == "-maxdepth" and index + 1 < len(args):
            try:
                return int(args[index + 1])
            except ValueError:
                return None
        if arg.startswith("-maxdepth="):
            try:
                return int(arg.split("=", 1)[1])
            except ValueError:
                return None
    return None


def quote_argv(argv: list[str]) -> str:
    """Reconstruct a validated argv list into a single safely quoted command string."""
    return " ".join(shlex.quote(a) for a in argv)
