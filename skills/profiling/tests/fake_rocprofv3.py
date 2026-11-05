#!/usr/bin/env python3
"""Fake rocprofv3-like profiler for offline tests: no GPU, no ROCm."""
import sys
import json
from pathlib import Path

NAME = Path(sys.argv[0]).name

HELP = """usage: rocprofv3 [options] -- <app>
--kernel-trace --hip-trace --memory-copy-trace --runtime-trace --sys-trace
--output-format --output-directory --output-file --list-avail --pmc
--pid --attach --attach-duration-msec
"""


def main():
    args = sys.argv[1:]
    if args == ["--help"]:
        print(HELP)
        return 0
    if args == ["--version"]:
        print(f"{NAME} fake-1.0.0")
        return 0
    if "--output-directory" not in args or "--" not in args:
        print("missing required flags", file=sys.stderr)
        return 1
    out = Path(args[args.index("--output-directory") + 1])
    out.mkdir(parents=True, exist_ok=True)
    app = args[args.index("--") + 1:]
    (out / "trace.csv").write_text("kernel,duration_ns\nadd,123\n")
    (out / "ran.json").write_text(json.dumps({"app": app}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
