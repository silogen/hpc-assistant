#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT
"""Submit, inspect, watch and cancel GEAK kernel_workflow jobs on Frontier.

  geak_job.py submit TASK [--task TEXT] [--budget N] [--gpus N] [--options TEXT]
                          [--account A] [--time HH:MM:SS] [--sbatch-args "..."]
  geak_job.py status ID | watch ID [--interval S] [--timeout S] | cancel ID | list

Output is one JSON object on stdout; errors are JSON on stderr with exit code 1.
Jobs live in <project>/geak-output/{jobs,runs}/<id>/. The project is --project-dir,
$GEAK_PROJECT_DIR, or the nearest ancestor with .geak-frontier.env, .claude, .opencode
or .hpc-assistant.
"""
import argparse
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN_SCRIPT = HERE / "run-geak.sbatch"
MARKERS = (".geak-frontier.env", ".claude", ".opencode", ".hpc-assistant")
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}
FAILED_SLURM = {"FAILED", "TIMEOUT", "CANCELLED", "NODE_FAIL", "OUT_OF_MEMORY", "PREEMPTED",
                "BOOT_FAIL", "DEADLINE"}


class JobError(Exception):
    pass


def out(obj):
    print(json.dumps(obj, indent=2))


def find_project(explicit=None):
    if explicit:
        return Path(explicit).resolve()
    if os.environ.get("GEAK_PROJECT_DIR"):
        return Path(os.environ["GEAK_PROJECT_DIR"]).resolve()
    for start in (Path.cwd().resolve(), HERE):
        for d in (start, *start.parents):
            if any((d / m).exists() for m in MARKERS) and d != Path.home():
                return d
    return Path.cwd().resolve()


def job_paths(project, job_id):
    if not re.fullmatch(r"[A-Za-z0-9._-]+", job_id or ""):
        raise JobError(f"invalid job id: {job_id!r}")
    jdir = project / "geak-output" / "jobs" / job_id
    if not jdir.is_dir():
        raise JobError(f"job directory not found: {jdir}")
    return jdir, project / "geak-output" / "runs" / job_id


def run(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def slurm_state(slurm_id):
    """Return (state, detail) from squeue, else sacct, else (None, None)."""
    rc, so, _ = run(["squeue", "-j", slurm_id, "-h", "-o", "%T %M %R"])
    if rc == 0 and so:
        return so.split()[0], so
    rc, so, _ = run(["sacct", "-j", slurm_id, "-X", "-n", "-P", "-o", "State,Elapsed,ExitCode"])
    if rc == 0 and so:
        line = so.splitlines()[0]
        return line.split("|")[0].split()[0], line
    return None, None


def read(path, default=""):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return default


def cmd_submit(args, project):
    src = Path(args.task_path)
    if not src.exists():
        raise JobError(f"kernel file or task directory not found: {src}")
    if not 1 <= args.gpus <= 8:
        raise JobError("--gpus must be between 1 and 8 (GCDs on one node)")
    if args.budget < 1:
        raise JobError("--budget must be >= 1")
    if not RUN_SCRIPT.is_file():
        raise JobError(f"job script missing: {RUN_SCRIPT}")
    if not (project / ".geak-frontier.env").is_file() and not os.environ.get("GEAK_HOME"):
        raise JobError(f"{project}/.geak-frontier.env not found: run scripts/frontier/setup-geak.sh first")
    key = project / ".anthropic_api_key"
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")):
        if not key.is_file():
            raise JobError(f"no credential: create {key} (mode 600) or export ANTHROPIC_API_KEY")
        if key.stat().st_mode & 0o777 != 0o600:
            raise JobError(f"{key} must be mode 600 (chmod 600 {key})")
    job_id = time.strftime("%Y%m%d-%H%M%S") + f"-{random.randrange(16**4):04x}"
    jdir = project / "geak-output" / "jobs" / job_id
    rdir = project / "geak-output" / "runs" / job_id
    jdir.mkdir(parents=True)
    rdir.mkdir(parents=True)
    name = src.resolve().name
    if src.is_dir():
        shutil.copytree(src, jdir / name, symlinks=True)
    else:
        shutil.copy2(src, jdir / name)
    config = {"kernel_filename": name, "task": args.task, "budget": args.budget,
              "gpus": args.gpus, "options": args.options or "", "source": str(src.resolve())}
    (jdir / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    cmd = ["sbatch", "--parsable", f"--output={jdir}/slurm-%j.out", f"--error={jdir}/slurm-%j.err"]
    if args.account:
        cmd.append(f"--account={args.account}")
    if args.time:
        cmd.append(f"--time={args.time}")
    cmd += shlex.split(args.sbatch_args or os.environ.get("GEAK_SBATCH_ARGS", ""))
    cmd += [str(RUN_SCRIPT), str(jdir), str(project)]
    rc, so, se = run(cmd)
    slurm_id = (so.splitlines()[-1].split(";")[0].strip() if so else "")
    if rc != 0 or not slurm_id.isdigit():
        (jdir / "FAILED").write_text(f"sbatch failed: {se or so}\n")
        raise JobError(f"sbatch failed: {se or so}")
    (jdir / "slurm_job_id").write_text(slurm_id + "\n")
    out({"ok": True, "id": job_id, "slurm_job_id": slurm_id, "job_dir": str(jdir),
         "run_dir": str(rdir), "budget": args.budget, "gpus": args.gpus})


def job_status(project, job_id):
    jdir, rdir = job_paths(project, job_id)
    slurm_id = read(jdir / "slurm_job_id") or None
    res = {"ok": True, "id": job_id, "slurm_job_id": slurm_id, "job_dir": str(jdir), "run_dir": str(rdir)}
    try:
        res["config"] = json.loads(read(jdir / "config.json", "{}"))
    except ValueError:
        pass
    if (jdir / "DONE").exists():
        res["state"] = "COMPLETED"
        try:
            res["report"] = json.loads(read(rdir / "final_report.json", "null"))
        except ValueError:
            res["report"] = read(rdir / "final_report.json") or None
        reports = sorted(rdir.glob("exp/**/final_report.md"))
        res["final_report_md"] = str(reports[0]) if reports else None
        patches = sorted(rdir.glob("exp/**/final_patch.diff"))
        res["final_patch"] = str(patches[0]) if patches else None
        res["result_files"] = sorted(p.name for p in rdir.iterdir()) if rdir.is_dir() else []
        return res
    if (jdir / "FAILED").exists():
        msg = read(jdir / "FAILED", "unknown error")
        res["state"] = "CANCELLED" if msg.startswith("CANCELLED") else "FAILED"
        res["error"] = msg
        errs = sorted(jdir.glob("slurm-*.err"))
        if errs:
            res["slurm_stderr_tail"] = "\n".join(read(errs[-1]).splitlines()[-30:])
        return res
    state, detail = slurm_state(slurm_id) if slurm_id else (None, None)
    res["slurm_status"] = detail
    if state in FAILED_SLURM or state == "COMPLETED":
        res["state"] = "FAILED"
        res["error"] = f"Slurm job ended ({detail}) without a DONE/FAILED marker"
    elif state == "PENDING":
        res["state"] = "PENDING"
    else:
        res["state"] = "RUNNING"
    return res


def cmd_status(args, project):
    out(job_status(project, args.id))


def cmd_watch(args, project):
    if args.interval <= 0 or args.timeout < 0:
        raise JobError("--interval must be > 0 and --timeout >= 0")
    start = time.monotonic()
    while True:
        res = job_status(project, args.id)
        if res["state"] in TERMINAL:
            res["watch"] = "finished"
            break
        if args.timeout and time.monotonic() - start + args.interval > args.timeout:
            res["watch"] = "timeout: job still active; run watch again"
            break
        time.sleep(args.interval)
    res["watched_s"] = round(time.monotonic() - start)
    out(res)


def cmd_cancel(args, project):
    jdir, _ = job_paths(project, args.id)
    if (jdir / "DONE").exists():
        raise JobError(f"job {args.id} has already completed")
    slurm_id = read(jdir / "slurm_job_id")
    if not slurm_id:
        raise JobError(f"no Slurm job id recorded for {args.id}")
    rc, _, se = run(["scancel", slurm_id])
    if rc != 0:
        state, _ = slurm_state(slurm_id)
        raise JobError(f"scancel failed ({se}); Slurm state: {state or 'not in queue'}")
    (jdir / "FAILED").write_text("CANCELLED by user\n")
    out({"ok": True, "id": args.id, "slurm_job_id": slurm_id, "state": "CANCELLED"})


def cmd_list(args, project):
    root = project / "geak-output" / "jobs"
    jobs = []
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if d.is_dir():
            try:
                s = job_status(project, d.name)
                jobs.append({k: s.get(k) for k in ("id", "state", "slurm_job_id")})
            except JobError:
                pass
    out({"ok": True, "project_dir": str(project), "jobs": jobs})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project-dir")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit")
    s.add_argument("task_path")
    s.add_argument("--task", default="Optimize this kernel for performance.",
                   help="natural-language steering text; pass the user's request verbatim")
    s.add_argument("--budget", type=int, default=int(os.environ.get("GEAK_BUDGET", "2")))
    s.add_argument("--gpus", type=int, default=1, help="GCDs for the job (1-8)")
    s.add_argument("--options", help="extra kernel_workflow arguments, e.g. 'mode bakeoff, min_improve 0.05'")
    s.add_argument("--account")
    s.add_argument("--time")
    s.add_argument("--sbatch-args", help="extra sbatch options (default $GEAK_SBATCH_ARGS)")
    for name in ("status", "cancel"):
        sub.add_parser(name).add_argument("id")
    w = sub.add_parser("watch")
    w.add_argument("id")
    w.add_argument("--interval", type=float, default=60)
    w.add_argument("--timeout", type=float, default=1200, help="seconds; 0 = until the job ends")
    sub.add_parser("list")
    args = ap.parse_args(argv)
    project = find_project(args.project_dir)
    try:
        {"submit": cmd_submit, "status": cmd_status, "watch": cmd_watch,
         "cancel": cmd_cancel, "list": cmd_list}[args.cmd](args, project)
    except JobError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
