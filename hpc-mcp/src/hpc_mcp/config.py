# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# Loader for .hpc-assistant.json.
#
# This file is the policy boundary, so validation is strict and the errors are explicit.
# A config that is silently half-applied would make the policy untrue, which is worse
# than refusing to start.

from __future__ import annotations

import json
import os
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError

CONFIG_FILENAME = ".hpc-assistant.json"
# Only rsync is supported. git and sshfs were designed but never tested end to end, so
# they are rejected rather than half-supported. Re-adding one means listing it here and
# in LOCAL_MODE_FILE_ACCESS_MODES in setup/config.sh.
FILE_ACCESS_MODES = ("rsync",)
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COMMAND_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
DEFAULT_ENV_PROBES = ("ROCM_PATH",)


@dataclass(frozen=True)
class Remote:
    host: str
    project_root: str
    account: str = ""
    # Directories outside project_root that may be READ. Sites keep the software everyone
    # shares outside any one project — container images on LUMI live in /appl/local/
    # containers — and without this the assistant cannot use them at all. Reads only:
    # writes stay inside project_root, so the blast radius is unchanged.
    readable_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class SlurmDefaults:
    partition: str
    time: str = "00:30:00"
    nodes: int = 1
    gpus_per_node: int = 1
    extra_directives: tuple[str, ...] = ()


@dataclass(frozen=True)
class Preamble:
    modules: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    commands: tuple[str, ...] = ()
    # Scheduler clients live in the default PATH at every site we know of, and running
    # `module load` before each squeue roughly triples what the call costs. Sites where
    # Slurm arrives by module can turn this on.
    apply_to_scheduler: bool = False

    def is_empty(self) -> bool:
        return not (self.modules or self.env or self.commands)


@dataclass(frozen=True)
class FileAccess:
    mode: str = "git"
    local_root: str = ""
    sshfs_mountpoint: str = ""


@dataclass(frozen=True)
class Limits:
    max_read_bytes: int = 262_144
    max_output_bytes: int = 65_536
    max_list_entries: int = 500
    max_find_depth: int = 3
    max_fetch_bytes: int = 10_485_760
    scheduler_min_interval_seconds: float = 10.0
    await_job_initial_interval_seconds: float = 10.0
    await_job_max_interval_seconds: float = 120.0
    command_timeout_seconds: float = 120.0


@dataclass(frozen=True)
class Config:
    path: Path
    site: str
    remote: Remote
    slurm: SlurmDefaults
    preamble: Preamble
    file_access: FileAccess
    limits: Limits
    audit_log: str = ".hpc-assistant/audit.log"
    rsync_excludes: tuple[str, ...] = field(default=())
    login_commands: tuple[str, ...] = ()
    env_probes: tuple[str, ...] = DEFAULT_ENV_PROBES

    @property
    def project_dir(self) -> Path:
        return self.path.parent

    @property
    def audit_log_path(self) -> Path:
        log = Path(self.audit_log)
        return log if log.is_absolute() else self.project_dir / log


def find_config(start: Path | None = None) -> Path:
    """Walk up from `start` looking for .hpc-assistant.json.

    The agent's working directory is wherever the user launched it, which is not
    necessarily the project root, so searching upwards is friendlier than requiring an
    explicit path — and it stops at the filesystem root rather than guessing.
    """
    here = (start or Path.cwd()).resolve()
    for directory in (here, *here.parents):
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    raise ConfigError(
        f"No {CONFIG_FILENAME} found in {here} or any parent directory. "
        f"Run `hpc-assistant-init --local` in your project directory to create one."
    )


def _strip_comments(value: object) -> object:
    """Drop the _comment keys the template uses to document itself.

    Any key beginning with _comment, so that a template can annotate one field —
    _comment_apply_to_scheduler next to apply_to_scheduler — rather than only a section.
    This file is what a user reads to find out what the assistant may do, so it is worth
    letting it explain itself.
    """
    if isinstance(value, dict):
        return {
            k: _strip_comments(v)
            for k, v in value.items()
            if not k.startswith("_comment")
        }
    if isinstance(value, list):
        return [_strip_comments(v) for v in value]
    return value


def _require(mapping: dict, key: str, where: str) -> object:
    if key not in mapping or mapping[key] in (None, ""):
        raise ConfigError(f"{where}.{key} is required and must not be empty.")
    return mapping[key]


def _positive(mapping: dict, key: str, default: float) -> float:
    raw = mapping.get(key, default)
    if not isinstance(raw, (int, float)) or isinstance(raw, bool) or raw <= 0:
        raise ConfigError(f"limits.{key} must be a positive number, got {raw!r}.")
    return raw


def normalize_sbatch_directives(raw: object, *, where: str) -> tuple[str, ...]:
    """Validate extra #SBATCH options. Shared by the config loader and the tools."""
    if raw in (None, []):
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ConfigError(f"{where} must be a list of strings.")
    directives: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{where} entries must be non-empty strings.")
        text = item.strip()
        if "\n" in text or "\x00" in text:
            raise ConfigError(f"{where} entries must be a single line.")
        if not text.startswith("-"):
            raise ConfigError(
                f"{where} entries must be sbatch options such as --gres=gpu:1, "
                f"got {text!r}."
            )
        directives.append(text)
    return tuple(directives)


def _string_tuple(raw: object, *, where: str, pattern: re.Pattern[str]) -> tuple[str, ...]:
    if raw in (None, []):
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ConfigError(f"{where} must be a list of strings.")
    values: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{where} entries must be non-empty strings.")
        text = item.strip()
        if not pattern.fullmatch(text):
            raise ConfigError(f"{where} contains an invalid name: {text!r}.")
        values.append(text)
    return tuple(values)


def load_config(path: Path | None = None) -> Config:
    config_path = (path or find_config()).resolve()

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Cannot read {config_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{config_path} is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path} must contain a JSON object.")
    data = _strip_comments(raw)
    assert isinstance(data, dict)

    remote_raw = data.get("remote") or {}
    if not isinstance(remote_raw, dict):
        raise ConfigError("remote must be an object.")

    host = str(_require(remote_raw, "host", "remote"))
    if "@" in host:
        raise ConfigError(
            f"remote.host must be an SSH host alias, not {host!r}. Define a Host block "
            f"in ~/.ssh/config and name it here, so authentication stays with your "
            f"ssh-agent and no credential is ever passed through this bridge."
        )
    if any(c.isspace() for c in host):
        raise ConfigError(f"remote.host must not contain whitespace, got {host!r}.")

    project_root = str(_require(remote_raw, "project_root", "remote"))
    # Checked before absoluteness, because "$SCRATCH/work" is both unexpanded and
    # relative, and the unexpanded part is the one the user needs to hear about.
    if "$" in project_root or project_root.startswith("~"):
        raise ConfigError(
            f"remote.project_root must be a concrete path, got {project_root!r}. "
            f"Shell variables and ~ are not expanded here, because a path that means "
            f"different things in different contexts cannot bound anything."
        )
    if not project_root.startswith("/"):
        raise ConfigError(
            f"remote.project_root must be an absolute path, got {project_root!r}."
        )

    readable_raw = remote_raw.get("readable_paths") or []
    if not isinstance(readable_raw, (list, tuple)):
        raise ConfigError("remote.readable_paths must be a list of absolute paths.")
    readable: list[str] = []
    for item in readable_raw:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError("remote.readable_paths entries must be non-empty strings.")
        entry = item.strip()
        if "$" in entry or entry.startswith("~"):
            raise ConfigError(
                f"remote.readable_paths must be concrete paths, got {entry!r}. Shell "
                f"variables and ~ are not expanded here."
            )
        if not entry.startswith("/"):
            raise ConfigError(
                f"remote.readable_paths entries must be absolute, got {entry!r}."
            )
        normalised = posixpath.normpath(entry).rstrip("/")
        # "/" would make the whole filesystem readable, which is the thing this bounds.
        if not normalised or normalised == "/":
            raise ConfigError("remote.readable_paths must not contain the filesystem root.")
        readable.append(normalised)

    slurm_raw = data.get("slurm") or {}
    if not isinstance(slurm_raw, dict):
        raise ConfigError("slurm must be an object.")
    partition = str(slurm_raw.get("default_partition") or "").strip()
    if not partition:
        raise ConfigError(
            "slurm.default_partition is required and must not be empty. Partition "
            "names are local to each cluster, so there is no built-in default."
        )
    try:
        nodes = int(slurm_raw.get("default_nodes", 1))
        gpus_per_node = int(slurm_raw.get("default_gpus_per_node", 1))
    except (TypeError, ValueError) as exc:
        raise ConfigError("slurm.default_nodes and default_gpus_per_node must be integers.") from exc
    if nodes <= 0:
        raise ConfigError("slurm.default_nodes must be a positive integer.")
    if gpus_per_node < 0:
        raise ConfigError("slurm.default_gpus_per_node must not be negative.")
    slurm = SlurmDefaults(
        partition=partition,
        time=str(slurm_raw.get("default_time") or "00:30:00"),
        nodes=nodes,
        gpus_per_node=gpus_per_node,
        extra_directives=normalize_sbatch_directives(
            slurm_raw.get("extra_directives"), where="slurm.extra_directives"
        ),
    )

    preamble_raw = data.get("preamble") or {}
    env_raw = preamble_raw.get("env") or {}
    if not isinstance(env_raw, dict):
        raise ConfigError("preamble.env must be an object of NAME: value pairs.")
    apply_to_scheduler = preamble_raw.get("apply_to_scheduler", False)
    if not isinstance(apply_to_scheduler, bool):
        raise ConfigError(
            f"preamble.apply_to_scheduler must be true or false, got "
            f"{apply_to_scheduler!r}."
        )
    preamble = Preamble(
        modules=tuple(str(m) for m in preamble_raw.get("modules") or ()),
        env=tuple((str(k), str(v)) for k, v in env_raw.items()),
        commands=tuple(str(c) for c in preamble_raw.get("commands") or ()),
        apply_to_scheduler=apply_to_scheduler,
    )

    fa_raw = data.get("file_access") or {}
    mode = str(fa_raw.get("mode", FILE_ACCESS_MODES[0]))
    if mode not in FILE_ACCESS_MODES:
        raise ConfigError(
            f"file_access.mode must be one of {', '.join(FILE_ACCESS_MODES)}, got {mode!r}."
        )
    local_root = str(fa_raw.get("local_root") or config_path.parent)
    sshfs_raw = fa_raw.get("sshfs") or {}
    mountpoint = str(sshfs_raw.get("mountpoint") or "")
    if mode == "sshfs" and not mountpoint:
        raise ConfigError(
            "file_access.sshfs.mountpoint is required when file_access.mode is sshfs."
        )
    file_access = FileAccess(
        mode=mode,
        local_root=str(Path(os.path.expanduser(local_root))),
        sshfs_mountpoint=str(Path(os.path.expanduser(mountpoint))) if mountpoint else "",
    )

    rsync_raw = fa_raw.get("rsync") or {}
    excludes = tuple(str(e) for e in rsync_raw.get("excludes") or ())

    lim = data.get("limits") or {}
    if not isinstance(lim, dict):
        raise ConfigError("limits must be an object.")
    defaults = Limits()
    limits = Limits(
        max_read_bytes=int(_positive(lim, "max_read_bytes", defaults.max_read_bytes)),
        max_output_bytes=int(
            _positive(lim, "max_output_bytes", defaults.max_output_bytes)
        ),
        max_list_entries=int(
            _positive(lim, "max_list_entries", defaults.max_list_entries)
        ),
        max_find_depth=int(_positive(lim, "max_find_depth", defaults.max_find_depth)),
        max_fetch_bytes=int(_positive(lim, "max_fetch_bytes", defaults.max_fetch_bytes)),
        scheduler_min_interval_seconds=_positive(
            lim, "scheduler_min_interval_seconds", defaults.scheduler_min_interval_seconds
        ),
        await_job_initial_interval_seconds=_positive(
            lim,
            "await_job_initial_interval_seconds",
            defaults.await_job_initial_interval_seconds,
        ),
        await_job_max_interval_seconds=_positive(
            lim, "await_job_max_interval_seconds", defaults.await_job_max_interval_seconds
        ),
        command_timeout_seconds=_positive(
            lim, "command_timeout_seconds", defaults.command_timeout_seconds
        ),
    )
    if limits.await_job_max_interval_seconds < limits.await_job_initial_interval_seconds:
        raise ConfigError(
            "limits.await_job_max_interval_seconds must not be smaller than "
            "limits.await_job_initial_interval_seconds."
        )

    audit_raw = data.get("audit") or {}
    audit_log = str(audit_raw.get("log") or ".hpc-assistant/audit.log")

    login_commands = _string_tuple(
        data.get("login_commands"), where="login_commands", pattern=_COMMAND_NAME
    )
    if "env_probes" in data:
        env_probes = _string_tuple(data.get("env_probes"), where="env_probes", pattern=_ENV_NAME)
    else:
        env_probes = DEFAULT_ENV_PROBES

    return Config(
        path=config_path,
        site=str(data.get("site") or "unknown"),
        remote=Remote(
            host=host,
            project_root=project_root.rstrip("/") or "/",
            account=str(remote_raw.get("account") or ""),
            readable_paths=tuple(readable),
        ),
        slurm=slurm,
        preamble=preamble,
        file_access=file_access,
        limits=limits,
        audit_log=audit_log,
        rsync_excludes=excludes,
        login_commands=login_commands,
        env_probes=env_probes,
    )
