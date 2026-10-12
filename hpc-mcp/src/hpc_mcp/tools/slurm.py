# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# Scheduler tools.
#
# Each is named after the command it wraps. That is deliberate and it is the reason a
# skill written for the on-cluster placement keeps working here: a skill that says "run
# squeue to check the queue" maps onto a tool called squeue without translation, and the
# same skill still works when the agent is on a node and running the real command.

from __future__ import annotations

from ..config import ConfigError, normalize_sbatch_directives
from ..errors import PolicyViolation
from ..session import Session

_SQUEUE_FORMAT = "%.18i %.9P %.24j %.8T %.10M %.6D %R"


def register(server, session: Session) -> None:
    config = session.config

    @server.tool()
    async def sinfo(partition: str | None = None, summarize: bool = True) -> str:
        """Show partition and node availability.

        Args:
            partition: Restrict to one partition. Omit for all partitions.
            summarize: Group nodes by state instead of listing them individually.
        """
        argv = ["sinfo"]
        if summarize:
            argv.append("--summarize")
        if partition:
            argv += ["--partition", partition]
        outcome = await session.execute(argv, tool="sinfo", check=True)
        return outcome.stdout or "sinfo returned no output."

    @server.tool()
    async def squeue(job_id: str | None = None, user: str | None = None) -> str:
        """List queued and running jobs. Defaults to your own jobs.

        Call this once when the user asks how a job is doing. Do not call it after
        submit, and do not call it in a loop.

        Args:
            job_id: Show a single job.
            user: Show another user's jobs. Omit for your own.
        """
        argv = ["squeue", "-o", _SQUEUE_FORMAT]
        if job_id:
            argv += ["--job", _validate_job_id(job_id)]
        elif user:
            argv += ["--user", user]
        else:
            argv.append("--me")
        outcome = await session.execute(argv, tool="squeue", check=True)
        return outcome.stdout or "No matching jobs in the queue."

    @server.tool()
    async def sacct(job_id: str | None = None, days: int = 1) -> str:
        """Show accounting records for finished jobs, including exit codes.

        Args:
            job_id: A specific job. Omit to list recent jobs.
            days: How far back to look when job_id is omitted.
        """
        argv = [
            "sacct",
            "--format",
            "JobID,JobName%24,Partition,State,ExitCode,Elapsed,NodeList",
        ]
        if job_id:
            argv += ["--jobs", _validate_job_id(job_id)]
        else:
            if days < 1:
                raise PolicyViolation("days must be at least 1.")
            argv += ["--starttime", f"now-{int(days)}days", "--allocations"]
        outcome = await session.execute(argv, tool="sacct", check=True)
        return outcome.stdout or "No accounting records matched."

    @server.tool()
    async def scontrol_show_job(job_id: str) -> str:
        """Show the scheduler's full detail for one job.

        Args:
            job_id: The job to inspect.
        """
        outcome = await session.execute(
            ["scontrol", "show", "job", _validate_job_id(job_id)],
            tool="scontrol_show_job",
            check=True,
        )
        return outcome.stdout

    @server.tool()
    async def scancel(job_id: str) -> str:
        """Cancel a job.

        Args:
            job_id: The job to cancel.
        """
        job = _validate_job_id(job_id)
        outcome = await session.execute(["scancel", job], tool="scancel")
        if not outcome.ok:
            return f"scancel failed (exit {outcome.exit_code}): {outcome.stderr.strip()}"
        return f"Cancelled job {job}."

    @server.tool()
    async def sbatch(
        script_path: str,
        account: str | None = None,
        partition: str | None = None,
        time_limit: str | None = None,
        extra_directives: list[str] | None = None,
        dry_run: bool = False,
    ) -> str:
        """Submit a batch script that already exists on the cluster. Does not wait.

        Runs `sbatch --test-only` first and reports what the scheduler says before
        submitting, so a request that would be rejected or would sit in the queue for a
        week is visible before it is made rather than after. After a successful submit,
        report the job id and stop. Do not call await_job. Do not poll squeue.

        Args:
            script_path: Path to the batch script, inside the project root.
            account: Account to bill. Defaults to remote.account from the config.
            partition: Override the partition in the script.
            time_limit: Override the time limit, as HH:MM:SS.
            extra_directives: Extra sbatch options for this submission, such as
                ["--qos=debug"]. Not taken from the config — the script is the source
                of truth, and these are one-off overrides.
            dry_run: Validate only; do not submit.
        """
        path = session.policy.normalize_remote_path(script_path, write=True)
        billing_account = account or config.remote.account

        overrides: list[str] = []
        if billing_account:
            overrides += ["--account", billing_account]
        if partition:
            overrides += ["--partition", partition]
        if time_limit:
            overrides += ["--time", time_limit]
        try:
            extras = normalize_sbatch_directives(
                extra_directives, where="extra_directives"
            )
        except ConfigError as exc:
            raise PolicyViolation(str(exc)) from exc
        overrides.extend(extras)

        preflight = await session.execute(
            ["sbatch", "--test-only", *overrides, path], tool="sbatch_preflight"
        )
        # --test-only reports on stderr and exits non-zero when the request is invalid.
        verdict = (preflight.stderr or preflight.stdout).strip()
        if not preflight.ok:
            return (
                f"The scheduler rejected this request, so nothing was submitted:\n"
                f"{verdict}"
            )
        if dry_run:
            return f"Dry run — not submitted. The scheduler would accept it:\n{verdict}"

        outcome = await session.execute(
            ["sbatch", "--parsable", *overrides, path], tool="sbatch", check=True
        )
        job_id = outcome.stdout.strip().split(";")[0]
        return (
            f"Submitted job {job_id}.\nPre-flight said: {verdict}\n"
            "Not waiting. Report the job id and stop. Check with squeue or sacct "
            "only when the user asks."
        )


def _validate_job_id(job_id: str) -> str:
    """Slurm job ids are digits, optionally with an array or step suffix."""
    candidate = job_id.strip()
    if not candidate:
        raise PolicyViolation("A job id is required.")
    allowed = set("0123456789_+.")
    if not set(candidate) <= allowed or not candidate[0].isdigit():
        raise PolicyViolation(
            f"{job_id!r} is not a Slurm job id.",
            "Job ids are numeric, optionally with an array or step suffix such as "
            "12345_7 or 12345.batch.",
        )
    return candidate
