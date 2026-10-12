#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
#
# Cluster validation harness.
#
# The unit tests establish that the policy engine decides correctly and that the scripts it
# builds say what they should. What they cannot establish is that those scripts mean the
# same thing on a real login node. Whether lmod is where the bootstrap looks for it,
# whether this site's coreutils understand `find -printf` and `dd iflag=skip_bytes`,
# whether the scheduler accepts a pre-flight, whether a multiplexed connection survives
# between calls — none of that is a property of this repository, so none of it can be
# settled here. It needs a run against a cluster.
#
# Every check goes through the assembled MCP server via call_tool, which is the same path
# an agent takes, so a passing run says the agent's path works rather than that some
# internal function does.
#
# Writes are confined to a scratch directory beneath remote.project_root and removed at the
# end. Nothing is submitted to the scheduler unless --submit-job is passed, so the default
# run costs no billing units.

from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from hpc_mcp.config import Config, load_config  # noqa: E402
from hpc_mcp.errors import HpcMcpError  # noqa: E402
from hpc_mcp.server import build_server  # noqa: E402
from hpc_mcp.transport import SSHTransport  # noqa: E402

PASS = "pass"
FAIL = "FAIL"
WARN = "warn"
SKIP = "skip"

# Awkward on purpose. A quoted heredoc should carry all of this through untouched; if the
# delimiter or the quoting were wrong, these are the characters that would prove it.
AWKWARD = (
    "plain ascii line\n"
    "single 'quotes' and double \"quotes\"\n"
    "a dollar $HOME and a brace ${HOME} that must not expand\n"
    "a backtick `hostname` and a substitution $(hostname) that must not run\n"
    "a backslash \\ and a tab\tcharacter\n"
    "unicode: ✓ é 日本語\n"
    "a line that says EOF all by itself:\n"
    "EOF\n"
)


@dataclass
class Result:
    name: str
    status: str
    detail: str = ""


@dataclass
class Validator:
    config: Config
    server: object
    session: object
    transport: SSHTransport
    args: argparse.Namespace
    results: list[Result] = field(default_factory=list)
    scratch: str = ""

    # ── Plumbing ─────────────────────────────────────────────────────────────────

    def record(self, name: str, status: str, detail: str = "") -> Result:
        result = Result(name, status, detail)
        self.results.append(result)
        mark = {PASS: "  ok  ", FAIL: " FAIL ", WARN: " warn ", SKIP: " skip "}[status]
        print(f"[{mark}] {name}" + (f"\n         {detail}" if detail else ""), flush=True)
        return result

    async def check(self, name: str, coro_factory, *, warn_only: bool = False) -> object:
        """Run one check. A raised exception is a failure; a returned string is its detail.

        Returning the detail rather than printing it keeps each check a single expression
        about the cluster, which is what makes them readable as a list.
        """
        try:
            detail = await coro_factory()
        except Exception as exc:  # noqa: BLE001 — a harness reports, it does not propagate
            message = f"{type(exc).__name__}: {exc}"
            self.record(name, WARN if warn_only else FAIL, message.strip())
            return None
        if isinstance(detail, tuple):
            status, text = detail
            self.record(name, status, text)
            return text
        self.record(name, PASS, str(detail or ""))
        return detail

    async def call(self, tool: str, arguments: dict | None = None):
        return await self.server.call_tool(tool, arguments or {})

    async def refused(self, tool: str, arguments: dict) -> str:
        """Assert a call is refused, and return the refusal so it can be eyeballed."""
        try:
            await self.call(tool, arguments)
        except (ToolError, HpcMcpError) as exc:
            return str(exc).splitlines()[0]
        raise AssertionError(f"{tool}({arguments}) was allowed and should not have been")

    # ── Checks ───────────────────────────────────────────────────────────────────

    async def configuration(self) -> str:
        remote = self.config.remote
        limits = self.config.limits
        return (
            f"{self.config.path}\n"
            f"         site {self.config.site}, host alias {remote.host!r}, "
            f"account {remote.account or 'unset'}\n"
            f"         project root {remote.project_root}\n"
            f"         file access {self.config.file_access.mode}, caps: "
            f"read {limits.max_read_bytes}B, output {limits.max_output_bytes}B, "
            f"depth {limits.max_find_depth}, scheduler every "
            f"{limits.scheduler_min_interval_seconds:g}s"
        )

    async def workspace_placement(self) -> tuple[str, str]:
        policy = self.session.policy
        local_root = self.config.file_access.local_root
        if policy.workspace_is_remote():
            description = policy.mounts.describe(local_root)
            if self.config.file_access.mode == "sshfs":
                return PASS, f"{local_root} is on {description}, as sshfs mode expects"
            return (
                WARN,
                f"{local_root} is on {description} but file_access.mode is "
                f"{self.config.file_access.mode!r}; local search would cost the cluster",
            )
        return PASS, f"{local_root} is local disk, so local search is free"

    async def root_alias(self) -> tuple[str, str]:
        physical = await self.session.learn_root_aliases()
        if physical is None:
            return PASS, "the configured root is already the physical path"
        return PASS, f"also reachable as {physical}, now accepted as the same directory"

    async def ssh_master(self) -> str:
        started = time.monotonic()
        await self.transport.ensure_master()
        control_path = await self.transport.control_path()
        elapsed = time.monotonic() - started
        # Private, deliberately: the harness is checking the mechanism, not using the API.
        alive = await self.transport._master_is_alive(control_path)
        if not alive:
            raise AssertionError("ensure_master returned but `ssh -O check` says no master")
        return f"master established in {elapsed:.2f}s at {control_path}"

    async def project_root_reachable(self) -> str:
        """A dispatch lands in the project root — under whichever name the cluster uses.

        `pwd` reports the physical path, so on a site that reaches storage through a
        symlink this also exercises the alias learned at startup: the reported path has to
        normalise back onto the configured root rather than being refused as foreign.
        """
        payload = await self.run_command(["pwd"])
        root = self.config.remote.project_root
        reported = payload["stdout"].strip()
        canonical = self.session.policy.normalize_remote_path(reported)
        if canonical != root:
            raise AssertionError(f"expected to land in {root}, landed in {reported!r}")
        if reported != root:
            return f"lands in {reported}, recognised as {root}"
        return f"a dispatch lands in {root}"

    async def _median_dispatch(self, *, include_preamble: bool) -> float:
        """Time a bare dispatch. Goes through Session rather than a tool, because no tool
        exposes the preamble switch and the point here is to price the preamble."""
        timings = []
        for _ in range(3):
            started = time.monotonic()
            await self.session.execute(
                ["hostname"], tool="validate", include_preamble=include_preamble
            )
            timings.append(time.monotonic() - started)
        return sorted(timings)[1]

    async def round_trip(self) -> tuple[str, str]:
        median = await self._median_dispatch(include_preamble=False)
        detail = f"{median * 1000:.0f}ms median for a bare dispatch"
        # Without multiplexing every call re-authenticates, which is well over a second to
        # any real site; a plausible round trip is the evidence that the master is in use.
        if median > 1.5:
            return WARN, f"{detail} — high enough to suspect the connection is not reused"
        return PASS, detail

    async def preamble_surcharge(self) -> tuple[str, str]:
        if self.config.preamble.is_empty():
            return SKIP, "no preamble declared"
        bare = await self._median_dispatch(include_preamble=False)
        full = await self._median_dispatch(include_preamble=True)
        surcharge = full - bare
        detail = (
            f"the preamble adds {surcharge * 1000:.0f}ms to every command that needs it "
            f"({bare * 1000:.0f}ms → {full * 1000:.0f}ms)"
        )
        if surcharge > 1.0:
            return WARN, (
                f"{detail}; `module load` is usually the whole of it, so declare only the "
                f"modules the project needs"
            )
        return PASS, detail

    async def preamble_modules(self) -> tuple[str, str]:
        modules = self.config.preamble.modules
        if not modules:
            return SKIP, "no modules declared in preamble.modules"
        payload = await self.run_command(["printenv", "LOADEDMODULES"])
        if payload["exit_code"] != 0:
            raise AssertionError(
                "LOADEDMODULES is unset after the preamble ran, so `module load` did "
                "not take effect"
            )
        loaded = payload["stdout"].strip().split(":")
        missing = [m for m in modules if not any(entry.startswith(m) for entry in loaded)]
        if missing:
            raise AssertionError(f"declared but not loaded: {', '.join(missing)}")
        return PASS, f"lmod bootstrapped and loaded {', '.join(modules)}"

    async def module_command(self) -> tuple[str, str]:
        """`module` is a shell function, and dispatch ends in `exec`.

        Worth a check of its own because run_command's own docstring offers
        ["module", "list"] as its example, so it is the first thing an agent will try.
        """
        payload = await self.run_command(["module", "list"])
        if payload["exit_code"] == 0:
            return PASS, "run_command(['module', 'list']) works"
        return (
            WARN,
            f"run_command(['module','list']) exits {payload['exit_code']}: "
            f"{(payload['stderr'] or payload['stdout']).strip().splitlines()[0]}",
        )

    async def write_and_read_back(self) -> str:
        path = f"{self.scratch}/awkward.txt"
        report = self.text(await self.call("write_file", {"path": path, "content": AWKWARD}))
        got = self.text(await self.call("read_file", {"path": path}))
        if got != AWKWARD:
            raise AssertionError(
                f"round trip differs.\n         wrote {AWKWARD!r}\n         read  {got!r}"
            )
        return f"{report.strip()}; content survived the heredoc byte for byte"

    async def read_continuation(self) -> str:
        path = f"{self.scratch}/ascii.txt"
        body = "".join(f"{n:07d}\n" for n in range(1, 401))  # 3200 bytes, ascii only
        await self.call("write_file", {"path": path, "content": body})

        first = self.strip_note(
            self.text(await self.call("read_file", {"path": path, "max_bytes": 800}))
        )
        second = self.strip_note(
            self.text(
                await self.call(
                    "read_file", {"path": path, "offset": 800, "max_bytes": 800}
                )
            )
        )
        if len(first.encode()) != 800:
            raise AssertionError(f"asked for 800 bytes, got {len(first.encode())}")
        if first + second != body[:1600]:
            raise AssertionError("the two slices do not reassemble into the original")
        return "a capped read reports its offset and the next read continues from it"

    async def tail(self) -> str:
        path = f"{self.scratch}/ascii.txt"
        got = self.text(await self.call("read_file", {"path": path}))
        del got
        tail = self.text(await self.call("tail_file", {"path": path, "max_bytes": 80}))
        if "0000400" not in tail:
            raise AssertionError(f"the tail does not contain the last line: {tail!r}")
        return "tail_file returns the end of the file, which is where a job log says how it went"

    async def listing(self) -> str:
        listing = self.text(await self.call("list_dir", {"path": self.scratch, "depth": 1}))
        for expected in ("awkward.txt", "ascii.txt"):
            if expected not in listing:
                raise AssertionError(f"{expected} missing from the listing:\n{listing}")
        return f"list_dir found both files ({len(listing.splitlines()) - 1} entries)"

    async def listing_depth_refused(self) -> str:
        limit = self.config.limits.max_find_depth
        refusal = await self.refused(
            "list_dir", {"path": self.scratch, "depth": limit + 5}
        )
        return refusal

    async def find_by_name(self) -> str:
        found = self.text(
            await self.call("find_files", {"name": "*.txt", "path": self.scratch})
        )
        if "awkward.txt" not in found:
            raise AssertionError(f"find_files did not find the file:\n{found}")
        return "find_files matched by name at bounded depth"

    async def output_cap(self) -> str:
        cap = self.config.limits.max_output_bytes
        payload = await self.run_command(["seq", "1", "50000"])
        if not payload["truncated"]:
            raise AssertionError("50000 lines came back untruncated")
        if "omitted by hpc-mcp output cap" not in payload["stdout"]:
            raise AssertionError("output was truncated without saying so")
        returned = len(payload["stdout"].encode())
        return f"288KB of output capped to {returned}B against a {cap}B limit, with a marker"

    async def refusals(self) -> str:
        """The refusals that matter, exercised against the real config.

        The unit tests cover the decisions; this confirms they still hold with this
        cluster's project root and this site's limits, and that each refusal names an
        alternative rather than only saying no.
        """
        cases = [
            ("compute on the login node", "run_command", {"command": ["python3", "-c", "1"]}),
            ("a compiler", "run_command", {"command": ["make", "-j", "8"]}),
            ("recursive content search", "run_command", {"command": ["grep", "-r", "x", "."]}),
            ("unbounded find", "run_command", {"command": ["find", ".", "-name", "*.py"]}),
            ("a tree walker", "run_command", {"command": ["du", "-sh", "."]}),
            ("a launcher", "run_command", {"command": ["bash", "-c", "echo hi"]}),
            ("an interactive allocation", "run_command", {"command": ["srun", "hostname"]}),
            ("a shell operator", "run_command", {"command": ["ls", ";", "whoami"]}),
            ("a path outside the root", "read_file", {"path": "/etc/passwd"}),
            ("a traversal escape", "read_file", {"path": "../../../etc/passwd"}),
            ("a bogus job id", "scancel", {"job_id": "; rm -rf /"}),
        ]
        lines = []
        for label, tool, arguments in cases:
            refusal = await self.refused(tool, arguments)
            lines.append(f"{label}: {refusal}")
        return "\n         ".join([f"{len(cases)} refusals held:", *lines])

    async def sinfo(self) -> str:
        text = self.text(await self.call("sinfo", {"summarize": True}))
        if "PARTITION" not in text:
            raise AssertionError(f"sinfo output does not look like sinfo:\n{text[:200]}")
        return f"{len(text.splitlines()) - 1} partition rows"

    async def squeue(self) -> str:
        text = self.text(await self.call("squeue", {}))
        return text.strip().splitlines()[0] if text.strip() else "no jobs queued"

    async def sacct(self) -> str:
        text = self.text(await self.call("sacct", {"days": 7}))
        rows = [line for line in text.splitlines() if line.strip()]
        return f"{max(0, len(rows) - 2)} accounting rows in the last 7 days"

    async def scheduler_throttle(self) -> tuple[str, str]:
        """The rate limiter has to be observable, or the claim about it is unverifiable."""
        interval = self.config.limits.scheduler_min_interval_seconds
        await self.call("sinfo", {"summarize": True})
        started = time.monotonic()
        await self.call("sinfo", {"summarize": True})
        waited = time.monotonic() - started
        detail = f"back-to-back scheduler calls were spaced {waited:.1f}s apart"
        if waited < interval * 0.8:
            return WARN, f"{detail}, below the configured {interval:g}s"
        return PASS, f"{detail} (configured minimum {interval:g}s)"

    async def sbatch_preflight(self) -> str:
        """`sbatch --test-only` asks the scheduler without spending anything."""
        path = f"{self.scratch}/preflight.sbatch"
        script = (
            "#!/usr/bin/env bash\n"
            f"#SBATCH --job-name=hpc-mcp-validate\n"
            f"#SBATCH --partition={self.args.partition or self.config.slurm.partition}\n"
            "#SBATCH --time=00:02:00\n"
            "#SBATCH --nodes=1\n"
            "hostname\n"
        )
        await self.call("write_file", {"path": path, "content": script})
        text = self.text(
            await self.call("sbatch", {"script_path": path, "dry_run": True})
        )
        if "rejected" in text:
            raise AssertionError(text)
        return text.strip().splitlines()[-1][:160]

    async def submit_job(self) -> tuple[str, str]:
        if not self.args.submit_job:
            return SKIP, "pass --submit-job to submit a real job (costs billing units)"
        arguments = {
            "command": ["hostname"],
            "time_limit": "00:02:00",
            "nodes": 1,
            "gpus_per_node": self.args.gpus,
            "wait": True,
            "timeout_seconds": self.args.job_timeout,
        }
        if self.args.partition:
            arguments["partition"] = self.args.partition
        payload = self.payload(await self.call("run_on_compute", arguments))
        if not payload.get("submitted"):
            raise AssertionError(f"not submitted: {payload.get('scheduler_says')}")
        state = payload.get("state")
        if state != "COMPLETED":
            raise AssertionError(f"job {payload.get('job_id')} ended {state}: {payload}")
        tail = (payload.get("log_tail") or "").strip().splitlines()
        return PASS, (
            f"job {payload['job_id']} {state} after "
            f"{payload.get('waited_seconds')}s in {payload.get('checks')} checks; "
            f"log says {tail[-1] if tail else '(empty)'}"
        )

    async def audit_log(self) -> str:
        path = self.config.audit_log_path
        if not path.is_file():
            raise AssertionError(f"no audit log at {path}")
        tools: dict[str, int] = {}
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise AssertionError(f"{path}:{number} is not JSON: {exc}") from exc
                tools[record.get("tool", "?")] = tools.get(record.get("tool", "?"), 0) + 1
        busiest = sorted(tools.items(), key=lambda kv: -kv[1])[:4]
        return (
            f"{sum(tools.values())} JSON records at {path}; "
            f"busiest: {', '.join(f'{t}×{n}' for t, n in busiest)}"
        )

    # ── Helpers ──────────────────────────────────────────────────────────────────

    async def run_command(self, argv: list[str]) -> dict:
        return self.payload(await self.call("run_command", {"command": argv}))

    def text(self, result) -> str:
        content = getattr(result, "content", result)
        if isinstance(content, (list, tuple)):
            parts = []
            for block in content:
                value = getattr(block, "text", None)
                parts.append(value if value is not None else str(block))
            return "\n".join(parts)
        return str(content)

    def payload(self, result) -> dict:
        for attribute in ("structured_content", "structuredContent", "structured"):
            value = getattr(result, attribute, None)
            if isinstance(value, dict):
                # FastMCP wraps a bare return value under "result" for non-object types.
                return value.get("result", value) if set(value) == {"result"} else value
        try:
            parsed = json.loads(self.text(result))
        except json.JSONDecodeError as exc:
            raise AssertionError(f"expected a JSON payload, got {self.text(result)!r}") from exc
        return parsed if isinstance(parsed, dict) else {"result": parsed}

    @staticmethod
    def strip_note(text: str) -> str:
        """Drop the continuation note read_file prepends to a partial read."""
        first, separator, rest = text.partition("\n")
        return rest if first.startswith("[bytes ") and separator else text

    # ── Orchestration ────────────────────────────────────────────────────────────

    async def prepare(self) -> None:
        root = self.config.remote.project_root
        if self.args.create_root:
            # The harness's own privilege, not the agent's: creating the project root is
            # by definition an operation on the boundary rather than inside it.
            await self.transport.run_script(
                f"set -e\nmkdir -p {shlex.quote(root)}\n", timeout=30
            )
        self.scratch = f".hpc-assistant/validate-{uuid.uuid4().hex[:8]}"

    async def cleanup(self) -> None:
        if not self.scratch or self.args.keep:
            return
        target = self.session.policy.normalize_remote_path(self.scratch)
        # Belt and braces before an rm -rf: the path came from uuid4 above, but a recursive
        # delete built from a variable deserves a second look regardless.
        if "validate-" not in target or target == self.config.remote.project_root:
            print(f"refusing to remove {target}", file=sys.stderr)
            return
        result = await self.transport.run_script(
            f"set -e\nrm -rf -- {shlex.quote(target)}\n", timeout=60
        )
        if result.exit_code != 0:
            print(f"could not remove {target}: {result.stderr}", file=sys.stderr)

    async def run(self) -> int:
        print(f"\nValidating the hpc-mcp bridge against {self.config.remote.host!r}\n")
        await self.prepare()

        print("configuration")
        await self.check("config loads and declares its bounds", self.configuration)
        await self.check("workspace placement", self.workspace_placement)

        print("\nconnection")
        await self.check("ssh ControlMaster", self.ssh_master)
        await self.check("the project root's physical path", self.root_alias)
        await self.check("a dispatch reaches the project root", self.project_root_reachable)
        await self.check("connection reuse", self.round_trip)
        await self.check("what the preamble costs", self.preamble_surcharge)

        print("\nenvironment")
        await self.check("preamble loads its modules", self.preamble_modules)
        await self.check("the module command through run_command", self.module_command)

        print("\nfile tools")
        await self.check("write then read back", self.write_and_read_back)
        await self.check("capped read and continuation", self.read_continuation)
        await self.check("tail_file", self.tail)
        await self.check("list_dir", self.listing)
        await self.check("list_dir refuses excessive depth", self.listing_depth_refused)
        await self.check("find_files", self.find_by_name)
        await self.check("output cap", self.output_cap)

        print("\npolicy")
        await self.check("refusals hold against the real config", self.refusals)

        print("\nscheduler")
        await self.check("sinfo", self.sinfo)
        await self.check("squeue", self.squeue)
        await self.check("sacct", self.sacct)
        await self.check("scheduler throttling", self.scheduler_throttle)
        await self.check("sbatch pre-flight", self.sbatch_preflight)
        await self.check("a job on a compute node", self.submit_job)

        print("\naudit")
        await self.check("audit log is one JSON record per dispatch", self.audit_log)

        await self.cleanup()

        failures = [r for r in self.results if r.status == FAIL]
        warnings = [r for r in self.results if r.status == WARN]
        skipped = [r for r in self.results if r.status == SKIP]
        print(
            f"\n{len(self.results) - len(failures) - len(warnings) - len(skipped)} passed, "
            f"{len(warnings)} warned, {len(skipped)} skipped, {len(failures)} failed"
        )
        for failure in failures:
            print(f"  FAIL {failure.name}: {failure.detail.splitlines()[0]}")
        if self.args.json:
            Path(self.args.json).write_text(
                json.dumps([r.__dict__ for r in self.results], indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"\nwrote {self.args.json}")
        return 1 if failures else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the hpc-mcp bridge against a real cluster. Read-only by default: "
            "writes go to a scratch directory under remote.project_root and are removed, "
            "and no job is submitted unless --submit-job is given."
        )
    )
    parser.add_argument("--config", type=Path, help="path to .hpc-assistant.json")
    parser.add_argument(
        "--submit-job",
        action="store_true",
        help="submit one short job with run_on_compute and wait for it (costs billing units)",
    )
    parser.add_argument(
        "--partition", help="partition for the pre-flight and the job (default: config)"
    )
    parser.add_argument(
        "--gpus", type=int, default=0, help="GPUs per node for the job (default 0)"
    )
    parser.add_argument(
        "--job-timeout", type=int, default=900, help="how long to wait for the job"
    )
    parser.add_argument(
        "--create-root",
        action="store_true",
        help="mkdir -p remote.project_root first, for a fresh project",
    )
    parser.add_argument(
        "--keep", action="store_true", help="leave the remote scratch directory in place"
    )
    parser.add_argument("--json", help="also write results to this file as JSON")
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    transport = SSHTransport(config.remote.host)
    server, session = build_server(config, transport=transport)
    validator = Validator(config, server, session, transport, args)
    try:
        return await validator.run()
    finally:
        # Leaves the multiplexed connection as it was found rather than holding one open.
        await session.close()


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(main_async(args))
    except HpcMcpError as exc:
        print(f"\n{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
