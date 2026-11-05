#!/usr/bin/env python3
"""Offline tests for profile_rank.py: no GPU/ROCm/Slurm cluster required."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
FAKE = HERE / "fake_rocprofv3.py"
HELPER = SCRIPTS / "profile_rank.py"
# Run the helper with a fixed compute-node hostname so results do not depend on
# whether the tests are executed on a login node.
LAUNCH = ("import runpy, socket, sys; socket.gethostname = lambda: 'frontier00001'; "
          "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')")


def helper_cmd(*args):
    return [sys.executable, "-c", LAUNCH, str(HELPER), *args]


def make_fake_bin(tmp, name="rocprofv3"):
    bindir = Path(tmp) / "bin"
    bindir.mkdir(exist_ok=True)
    target = bindir / name
    target.write_text(f"#!/usr/bin/env python3\nimport runpy, sys\nsys.argv[0] = {str(FAKE)!r}\nrunpy.run_path({str(FAKE)!r}, run_name='__main__')\n")
    target.chmod(target.stat().st_mode | stat.S_IEXEC)
    return bindir


def run_helper(env_extra, args, tmp):
    env = dict(os.environ)
    env.pop("ROCR_VISIBLE_DEVICES", None)
    env.update({
        "SLURM_JOB_ID": "12345",
        "SLURM_STEP_ID": "0",
        "SLURM_PROCID": "0",
        "PROFILE_ROOT": str(Path(tmp) / "profiles"),
        "PROFILE_TOOL": "auto",
        "PROFILE_MODE": "runtime",
    })
    env.update(env_extra)
    return subprocess.run(helper_cmd(*args), env=env, capture_output=True, text=True)


class ProfileRankTests(unittest.TestCase):
    def test_rejects_login_like_host_without_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env.pop("SLURM_JOB_ID", None)
            env["PROFILE_ROOT"] = str(Path(tmp) / "profiles")
            result = subprocess.run(helper_cmd("--", "./app"), env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("compute task", result.stderr)

    def test_runs_fake_profiler_and_isolates_rank_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            bindir = make_fake_bin(tmp)
            env = {"PATH": f"{bindir}:{os.environ['PATH']}"}
            result = run_helper(env, ["--", "./myapp", "--flag"], tmp)
            self.assertEqual(result.returncode, 0, result.stderr)
            out_root = Path(tmp) / "profiles" / "job_12345" / "step_0"
            ranks = list(out_root.glob("rank_0_*"))
            self.assertEqual(len(ranks), 1)
            rank_dir = ranks[0]
            self.assertTrue((rank_dir / "trace.csv").exists())
            manifest = json.loads((rank_dir / "launch.json").read_text())
            self.assertEqual(manifest["exit_code"], 0)
            self.assertIn("--", manifest["command"])
            ran = json.loads((rank_dir / "ran.json").read_text())
            self.assertEqual(ran["app"], ["./myapp", "--flag"])

    def test_two_ranks_do_not_collide(self):
        with tempfile.TemporaryDirectory() as tmp:
            bindir = make_fake_bin(tmp)
            dirs = []
            for rank in ("0", "1"):
                env = {"PATH": f"{bindir}:{os.environ['PATH']}", "SLURM_PROCID": rank}
                result = run_helper(env, ["--", "./myapp"], tmp)
                self.assertEqual(result.returncode, 0, result.stderr)
                dirs.append(Path(result.stdout.strip().split("Profiler output: ")[1]))
            self.assertNotEqual(dirs[0], dirs[1])
            self.assertTrue(dirs[0].exists() and dirs[1].exists())

    def test_rejects_rocm_6_2_for_legacy_rocprof(self):
        with tempfile.TemporaryDirectory() as tmp:
            bindir = make_fake_bin(tmp, name="rocprof")
            env = {"PATH": f"{bindir}:{os.environ['PATH']}", "PROFILE_TOOL": "rocprof",
                   "ROCM_VERSION": "6.2.0"}
            result = run_helper(env, ["--", "./myapp"], tmp)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("6.2", result.stderr)

    def test_propagates_nonzero_application_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            bindir = make_fake_bin(tmp)
            # Fake profiler returns 1 when required flags are missing; force that by
            # overriding PROFILE_MODE to an invalid value caught by the helper itself.
            env = {"PATH": f"{bindir}:{os.environ['PATH']}", "PROFILE_MODE": "bogus"}
            result = run_helper(env, ["--", "./myapp"], tmp)
            self.assertEqual(result.returncode, 2)
            self.assertIn("PROFILE_MODE", result.stderr)

    def test_rejects_missing_profile_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            bindir = make_fake_bin(tmp)
            env = dict(os.environ)
            env.update({"SLURM_JOB_ID": "1", "SLURM_STEP_ID": "0", "SLURM_PROCID": "0",
                        "PATH": f"{bindir}:{os.environ['PATH']}"})
            env.pop("PROFILE_ROOT", None)
            result = subprocess.run(helper_cmd("--", "./app"), env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("PROFILE_ROOT", result.stderr)


if __name__ == "__main__":
    unittest.main()
