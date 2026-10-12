# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# Submitting work to a compute node, and an optional waiter the agent must not use by
# default.
#
# run_on_compute exists because policy by ergonomics beats policy by prohibition. The
# batch path wins when it is one call and running it on the login node is a refusal. It
# submits and returns the job id. Waiting is opt-in (`wait=true`) and only when the user
# asked to wait until the job finishes.
#
# await_job still exists for that explicit wait. It is not the path after submit: a model
# told to wait will sit in the chat, and a model told to check will poll squeue. Neither
# is what the user asked for. Check status with squeue or sacct, once, when asked.

from __future__ import annotations

import asyncio
import time

from .. import remote_fs
from ..config import ConfigError, normalize_sbatch_directives
from ..errors import PolicyViolation
from ..preamble import build_batch_script
from ..session import Session

TERMINAL_STATES = frozenset(
    {
        "BOOT_FAIL",
        "CANCELLED",
        "COMPLETED",
        "DEADLINE",
        "FAILED",
        "NODE_FAIL",
        "OUT_OF_MEMORY",
        "PREEMPTED",
        "REVOKED",
        "SPECIAL_EXIT",
        "TIMEOUT",
    }
)

JOB_DIR = ".hpc-assistant/jobs"


def _parse_scontrol_job(text: str) -> tuple[str, str | None] | None:
    """Read JobState and ExitCode from `scontrol show job` output."""
    state = None
    exit_code = None
    for token in text.replace("\n", " ").split():
        name, _, value = token.partition("=")
        if name == "JobState" and value:
            state = value
        elif name == "ExitCode" and value:
            exit_code = value
    if not state:
        return None
    return state, exit_code


async def query_state(session: Session, job_id: str) -> tuple[str, str | None]:
    """Return (state, exit_code). squeue first, then sacct, then scontrol.

    squeue knows about pending and running jobs; a finished job disappears from it.
    sacct is the usual source for how it ended, but sites without slurmdbd have no
    sacct, and even with accounting there is a lag. scontrol still knows about a job
    for MinJobAge after it finishes. UNKNOWN means none of the three had an answer
    yet — it is not a terminal state.
    """
    live = await session.execute(
        ["squeue", "-h", "-j", job_id, "-o", "%T"], tool="await_job"
    )
    state = live.stdout.strip().splitlines()
    if live.ok and state:
        return state[0].strip(), None

    record = await session.execute(
        ["sacct", "-n", "-P", "-j", job_id, "-o", "State,ExitCode"], tool="await_job"
    )
    if record.ok:
        for line in record.stdout.splitlines():
            fields = line.split("|")
            if len(fields) >= 2 and fields[0].strip():
                # "CANCELLED by 12345" is a state; keep the first word.
                return fields[0].strip().split()[0], fields[1].strip()

    detail = await session.execute(
        ["scontrol", "show", "job", job_id], tool="await_job"
    )
    if detail.ok:
        parsed = _parse_scontrol_job(detail.stdout)
        if parsed is not None:
            return parsed
    return "UNKNOWN", None


async def wait_for_job(
    session: Session, job_id: str, timeout_seconds: int = 1800
) -> dict:
    """Poll a job with exponential backoff until it reaches a terminal state.

    Lives at module level rather than inside the tool so run_on_compute can reuse it
    without depending on what the tool decorator hands back.
    """
    limits = session.config.limits
    started = time.monotonic()
    interval = limits.await_job_initial_interval_seconds
    checks = 0

    while True:
        state, exit_code = await query_state(session, job_id)
        checks += 1
        elapsed = time.monotonic() - started

        if state in TERMINAL_STATES:
            return {
                "job_id": job_id,
                "state": state,
                "exit_code": exit_code,
                "waited_seconds": round(elapsed, 1),
                "checks": checks,
                "note": "",
            }

        if elapsed + interval > timeout_seconds:
            note = (
                f"Stopped waiting after {timeout_seconds}s. The job is still {state} "
                f"and is unaffected — call await_job again to keep waiting."
            )
            if state == "UNKNOWN":
                note = (
                    f"Stopped waiting after {timeout_seconds}s; job state is still "
                    "UNKNOWN. squeue, sacct and scontrol had no record yet — the job "
                    "id may be wrong, accounting may be unavailable on this site, or "
                    "the scheduler has not caught up. Call await_job again to keep "
                    "waiting."
                )
            return {
                "job_id": job_id,
                "state": state,
                "exit_code": None if state == "UNKNOWN" else exit_code,
                "waited_seconds": round(elapsed, 1),
                "checks": checks,
                "note": note,
            }

        await asyncio.sleep(interval)
        interval = min(interval * 2, limits.await_job_max_interval_seconds)


def register(server, session: Session) -> None:
    config = session.config

    @server.tool()
    async def await_job(job_id: str, timeout_seconds: int = 1800) -> dict:
        """Wait until a job reaches a terminal state. Do not call this after submit.

        Only use this if the user explicitly asked you to wait until the job finishes.
        Checking how a job is doing is squeue or sacct, once — not this tool, and not a
        loop.

        Args:
            job_id: The job to wait for.
            timeout_seconds: Give up waiting after this long. The job keeps running.
        """
        return await wait_for_job(session, job_id, timeout_seconds)

    # The "not on this machine" paragraph below assumes remote placement (agent local,
    # cluster over SSH), which is the only placement setup/ wires up. Under
    # --placement local the agent's own file tools can read these paths; see the matching
    # note on site_info.
    @server.tool()
    async def run_on_compute(
        command: list[str],
        partition: str | None = None,
        time_limit: str | None = None,
        nodes: int | None = None,
        gpus_per_node: int | None = None,
        account: str | None = None,
        job_name: str | None = None,
        extra_directives: list[str] | None = None,
        wait: bool = False,
        timeout_seconds: int = 1800,
    ) -> dict:
        """Run a command on a compute node: writes a batch script, submits it, returns.

        This is the path for every build, test, benchmark and profiling run. It is one call
        because the alternative — running it on the login node — is refused, and a refusal
        without an easy alternative just gets worked around. It does not wait. Report the
        job id and stop. Check with squeue or sacct only when the user asks.

        The script and log paths returned are on the cluster, not on this machine. Read
        them with the remote file tools; your own file tools cannot see them.

        Args:
            command: The command as an argv list, for example ["make", "-j", "8"].
            partition: Slurm partition. Defaults to the project's configured partition.
            time_limit: Wall clock limit as HH:MM:SS.
            nodes: Node count.
            gpus_per_node: GPUs per node. Pass 0 for a CPU-only job.
            account: Account to bill. Defaults to remote.account from the config.
            job_name: Name for the job and its script.
            extra_directives: Extra #SBATCH options for this job, such as ["--mem=32G"].
                Appended to slurm.extra_directives from the config.
            wait: Wait until the job finishes. Default false. Set true only if the user
                asked you to wait until it ends.
            timeout_seconds: How long to wait when wait is true.
        """
        # Placement checks are skipped: compilers and interpreters are the point of a job.
        # Everything else still applies, including path bounds and traversal limits.
        argv = session.policy.validate_argv(
            command, tool="run_on_compute", enforce_placement=False
        )

        billing_account = account or config.remote.account
        chosen_partition = partition or config.slurm.partition
        if not chosen_partition:
            raise PolicyViolation(
                "No partition to submit this job to.",
                "Pass partition, or set slurm.default_partition in .hpc-assistant.json.",
            )
        try:
            extras = normalize_sbatch_directives(
                extra_directives, where="extra_directives"
            )
        except ConfigError as exc:
            raise PolicyViolation(str(exc)) from exc
        extra = tuple(config.slurm.extra_directives) + extras

        name = job_name or f"hpc-mcp-{argv[0].replace('/', '_')[:24]}"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        script_path = f"{JOB_DIR}/{name}-{stamp}.sbatch"
        output_pattern = f"{JOB_DIR}/{name}-{stamp}-%j.out"

        script = build_batch_script(
            argv,
            cwd=config.remote.project_root,
            preamble=config.preamble,
            job_name=name,
            account=billing_account,
            partition=chosen_partition,
            time_limit=time_limit or config.slurm.time,
            nodes=nodes if nodes is not None else config.slurm.nodes,
            gpus_per_node=(
                gpus_per_node
                if gpus_per_node is not None
                else config.slurm.gpus_per_node
            ),
            output_pattern=output_pattern,
            extra_directives=extra,
        )
        await remote_fs.write_file(session, script_path, script)

        remote_script = session.policy.normalize_remote_path(script_path, write=True)
        preflight = await session.execute(
            ["sbatch", "--test-only", remote_script], tool="run_on_compute"
        )
        if not preflight.ok:
            return {
                "submitted": False,
                "script": remote_script,
                "scheduler_says": (preflight.stderr or preflight.stdout).strip(),
                "note": "The scheduler rejected the request, so nothing was submitted.",
            }

        submitted = await session.execute(
            ["sbatch", "--parsable", remote_script], tool="run_on_compute", check=True
        )
        job_id = submitted.stdout.strip().split(";")[0]
        log_path = output_pattern.replace("%j", job_id)

        result: dict = {
            "submitted": True,
            "job_id": job_id,
            "script": remote_script,
            "log": session.policy.normalize_remote_path(log_path, write=True),
            "scheduler_says": (preflight.stderr or preflight.stdout).strip(),
        }
        if not wait:
            result["note"] = (
                "Not waiting. Report the job id and stop. Check with squeue or sacct "
                "only when the user asks."
            )
            return result

        outcome = await wait_for_job(session, job_id, timeout_seconds)
        result.update(
            {
                "state": outcome["state"],
                "exit_code": outcome["exit_code"],
                "waited_seconds": outcome["waited_seconds"],
                "checks": outcome["checks"],
            }
        )

        try:
            result["log_tail"] = await remote_fs.tail_file(session, log_path)
        except PolicyViolation as exc:
            result["log_tail"] = f"(could not read {log_path}: {exc})"
        return result
