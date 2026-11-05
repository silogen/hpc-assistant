#!/usr/bin/env python3
"""Launch one rank under an installed ROCm profiler. No downloads or GPU remapping."""
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys


def require_task(env, hostname):
    if not env.get("SLURM_JOB_ID") or not env.get("SLURM_PROCID"):
        raise ValueError("Run inside a Slurm compute task (srun), not on a login node")
    if env.get("SLURM_STEP_ID", "batch") in ("batch", "extern") or "login" in hostname.lower():
        raise ValueError("A compute srun step is required")
    for key in ("SLURM_JOB_ID", "SLURM_STEP_ID", "SLURM_PROCID"):
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+", env.get(key, "")):
            raise ValueError(f"Invalid or missing {key}")


def make_command(tool, help_text, mode, out, app, rocm_version):
    if mode not in ("runtime", "kernel"):
        raise ValueError("PROFILE_MODE must be runtime or kernel")
    if Path(tool).name == "rocprofv3":
        flags = ["--kernel-trace"]
        if mode == "runtime":
            flags += ["--hip-trace", "--memory-copy-trace"]
        required = flags + ["--output-format", "--output-directory", "--output-file"]
        for flag in required:
            if flag not in help_text:
                raise ValueError(f"Installed rocprofv3 does not advertise {flag}")
        return [tool, *flags, "--output-format", "csv", "--output-directory", str(out),
                "--output-file", "trace", "--", *app]
    if not re.match(r"^\d+\.\d+", rocm_version):
        raise ValueError("Legacy rocprof requires CRAY_ROCM_VERSION or ROCM_VERSION")
    if re.match(r"^6\.2(?:\D|$)", rocm_version):
        raise ValueError("OLCF warns legacy rocprof is inaccurate with ROCm 6.2")
    flags = ["--hip-trace"] if mode == "runtime" else ["--stats", "--timestamp", "on"]
    for flag in flags:
        if flag.startswith("--") and flag not in help_text:
            raise ValueError(f"Installed rocprof does not advertise {flag}")
    return [tool, *flags, "-d", str(out), "-o", str(out / "trace.csv"), *app]


def main(argv=None):
    app = list(sys.argv[1:] if argv is None else argv)
    if app and app[0] == "--":
        app.pop(0)
    if not app:
        raise ValueError("Usage: profile_rank.py APPLICATION [ARGS...]")
    env = os.environ
    host = socket.gethostname()
    require_task(env, host)
    root = Path(env.get("PROFILE_ROOT", ""))
    if not env.get("PROFILE_ROOT") or not root.is_absolute():
        raise ValueError("Set PROFILE_ROOT to an absolute path on a shared filesystem")
    selection = env.get("PROFILE_TOOL", "auto")
    if selection not in ("auto", "rocprofv3", "rocprof"):
        raise ValueError("PROFILE_TOOL must be auto, rocprofv3, or rocprof")
    tool = (shutil.which("rocprofv3") or shutil.which("rocprof")) if selection == "auto" else shutil.which(selection)
    if not tool:
        raise ValueError("No requested profiler found; load the application's compatible ROCm module")
    help_result = subprocess.run([tool, "--help"], capture_output=True, text=True, timeout=30)
    help_text = help_result.stdout + help_result.stderr
    if help_result.returncode:
        raise ValueError("Profiler --help failed; check the module/runtime compatibility")
    safe_host = re.sub(r"[^A-Za-z0-9_.-]", "_", host)
    out = root / ("job_" + env["SLURM_JOB_ID"]) / ("step_" + env["SLURM_STEP_ID"]) / f"rank_{env['SLURM_PROCID']}_{safe_host}_{os.getpid()}"
    command = make_command(tool, help_text, env.get("PROFILE_MODE", "runtime"), out, app,
                           env.get("CRAY_ROCM_VERSION") or env.get("ROCM_VERSION", ""))
    out.mkdir(parents=True, exist_ok=False)
    (out / "profiler-help.txt").write_text(help_text)
    version = subprocess.run([tool, "--version"], capture_output=True, text=True, timeout=30)
    (out / "profiler-version.txt").write_text(version.stdout + version.stderr)
    metadata = {"command": command, "host": host, "cwd": os.getcwd(),
                "affinity": sorted(os.sched_getaffinity(0)),
                "version_command_exit": version.returncode,
                "environment": {k: env.get(k) for k in (
                    "SLURM_JOB_ID", "SLURM_STEP_ID", "SLURM_PROCID", "SLURM_LOCALID",
                    "ROCR_VISIBLE_DEVICES", "HIP_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES",
                    "CRAY_ROCM_VERSION", "ROCM_VERSION", "ROCM_PATH", "LOADEDMODULES")}}
    manifest = out / "launch.json"
    manifest.write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"Profiler output: {out}", flush=True)
    result = subprocess.run(command)
    metadata["exit_code"] = result.returncode
    manifest.write_text(json.dumps(metadata, indent=2) + "\n")
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"profile_rank: {exc}", file=sys.stderr)
        sys.exit(2)
