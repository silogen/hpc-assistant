#!/usr/bin/env python3
"""Offline tests for the Frontier GEAK helpers (no Slurm, GPU, network or Claude).

  python3 -B -m unittest discover -s skills/geak-optimization/tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
FRONTIER = SKILL / "scripts" / "frontier"
GEAK_JOB = FRONTIER / "geak_job.py"

FAKES = {
    "sbatch": '#!/bin/bash\nprintf "%s\\n" "$@" > "$FAKE_DIR/sbatch.args"\n'
              '[[ -n "${FAKE_SBATCH_FAIL:-}" ]] && { echo "sbatch: error: invalid account" >&2; exit 1; }\n'
              'echo 4242\n',
    "squeue": '#!/bin/bash\n[[ -f "$FAKE_DIR/squeue.out" ]] && cat "$FAKE_DIR/squeue.out"\nexit 0\n',
    "sacct": '#!/bin/bash\n[[ -f "$FAKE_DIR/sacct.out" ]] && cat "$FAKE_DIR/sacct.out"\nexit 0\n',
    "scancel": '#!/bin/bash\necho "$1" > "$FAKE_DIR/scancel.args"\nexit "${FAKE_SCANCEL_RC:-0}"\n',
}


class GeakJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.fake = root / "fake"
        self.fake.mkdir()
        for name, body in FAKES.items():
            (self.fake / name).write_text(body)
            (self.fake / name).chmod(0o755)
        self.project = root / "project"
        (self.project / ".claude").mkdir(parents=True)
        (self.project / ".geak-frontier.env").write_text("GEAK_HOME=/x\nGEAK_VENV=/y\n")
        key = self.project / ".anthropic_api_key"
        key.write_text("sk-ant-api-test\n")
        key.chmod(0o600)
        self.task = self.project / "matmul_task"
        self.task.mkdir()
        (self.task / "matmul.py").write_text("# kernel\n")
        self.env = {"PATH": f"{self.fake}:{os.environ['PATH']}", "FAKE_DIR": str(self.fake),
                    "HOME": str(root)}

    def geak(self, *args, env=None, cwd=None):
        p = subprocess.run([sys.executable, str(GEAK_JOB), *args], capture_output=True, text=True,
                           env={**self.env, **(env or {})}, cwd=cwd or self.project, timeout=60)
        payload = json.loads(p.stdout) if p.stdout.strip() else json.loads(p.stderr)
        return p.returncode, payload

    def submit(self, *extra):
        rc, res = self.geak("submit", str(self.task), *extra)
        self.assertEqual(rc, 0, res)
        return res

    def test_submit_builds_job(self):
        res = self.submit("--task", "Use split-K", "--budget", "3", "--gpus", "2",
                          "--options", "mode bakeoff", "--account", "abc123", "--time", "01:00:00")
        self.assertEqual(res["slurm_job_id"], "4242")
        jdir = Path(res["job_dir"])
        self.assertTrue((jdir / "matmul_task" / "matmul.py").is_file())
        cfg = json.loads((jdir / "config.json").read_text())
        self.assertEqual((cfg["task"], cfg["budget"], cfg["gpus"], cfg["options"]),
                         ("Use split-K", 3, 2, "mode bakeoff"))
        argv = (self.fake / "sbatch.args").read_text().split("\n")
        self.assertIn("--account=abc123", argv)
        self.assertIn("--time=01:00:00", argv)
        self.assertEqual(argv[-4:-1], [str(FRONTIER / "run-geak.sbatch"), str(jdir), str(self.project)])
        self.assertEqual((jdir / "slurm_job_id").read_text().strip(), "4242")

    def test_submit_single_file_and_env_args(self):
        rc, res = self.geak("submit", str(self.task / "matmul.py"),
                            env={"GEAK_SBATCH_ARGS": "-A xyz -q debug"})
        self.assertEqual(rc, 0, res)
        argv = (self.fake / "sbatch.args").read_text().split("\n")
        self.assertIn("-q", argv)
        self.assertTrue((Path(res["job_dir"]) / "matmul.py").is_file())

    def test_submit_preconditions(self):
        (self.project / ".anthropic_api_key").chmod(0o644)
        rc, res = self.geak("submit", str(self.task))
        self.assertEqual(rc, 1)
        self.assertIn("mode 600", res["error"])
        (self.project / ".anthropic_api_key").chmod(0o600)
        for args, msg in ((["--gpus", "9"], "--gpus"), (["--budget", "0"], "--budget")):
            rc, res = self.geak("submit", str(self.task), *args)
            self.assertEqual(rc, 1)
            self.assertIn(msg, res["error"])
        rc, res = self.geak("submit", str(self.project / "missing"))
        self.assertIn("not found", res["error"])
        (self.project / ".geak-frontier.env").unlink()
        rc, res = self.geak("submit", str(self.task))
        self.assertIn("setup-geak.sh", res["error"])

    def test_sbatch_failure_marks_job(self):
        rc, res = self.geak("submit", str(self.task), env={"FAKE_SBATCH_FAIL": "1"})
        self.assertEqual(rc, 1)
        self.assertIn("invalid account", res["error"])
        jobs = list((self.project / "geak-output/jobs").iterdir())
        self.assertTrue((jobs[0] / "FAILED").exists())

    def test_status_life_cycle(self):
        job = self.submit()
        jdir, rdir = Path(job["job_dir"]), Path(job["run_dir"])
        (self.fake / "squeue.out").write_text("PENDING 0:00 (Priority)\n")
        self.assertEqual(self.geak("status", job["id"])[1]["state"], "PENDING")
        (self.fake / "squeue.out").write_text("RUNNING 5:00 frontier00001\n")
        self.assertEqual(self.geak("status", job["id"])[1]["state"], "RUNNING")
        (self.fake / "squeue.out").unlink()
        (self.fake / "sacct.out").write_text("TIMEOUT|02:00:00|0:0\n")
        res = self.geak("status", job["id"])[1]
        self.assertEqual(res["state"], "FAILED")
        self.assertIn("without a DONE/FAILED marker", res["error"])
        (rdir / "exp/team_x/matmul").mkdir(parents=True)
        (rdir / "exp/team_x/matmul/final_report.md").write_text("# report\n")
        (rdir / "exp/team_x/matmul/final_patch.diff").write_text("diff\n")
        (rdir / "final_report.json").write_text('{"claude_exit_code": 0}')
        (jdir / "DONE").touch()
        res = self.geak("status", job["id"])[1]
        self.assertEqual(res["state"], "COMPLETED")
        self.assertEqual(res["report"]["claude_exit_code"], 0)
        self.assertTrue(res["final_report_md"].endswith("final_report.md"))
        self.assertTrue(res["final_patch"].endswith("final_patch.diff"))

    def test_failed_reports_stderr_tail(self):
        job = self.submit()
        jdir = Path(job["job_dir"])
        (jdir / "FAILED").write_text("GEAK optimization failed: auth\n")
        (jdir / "slurm-4242.err").write_text("\n".join(f"line {i}" for i in range(50)))
        res = self.geak("status", job["id"])[1]
        self.assertEqual(res["state"], "FAILED")
        self.assertIn("auth", res["error"])
        self.assertTrue(res["slurm_stderr_tail"].startswith("line 20"))

    def test_cancel(self):
        job = self.submit()
        rc, res = self.geak("cancel", job["id"])
        self.assertEqual((rc, res["state"]), (0, "CANCELLED"))
        self.assertEqual((self.fake / "scancel.args").read_text().strip(), "4242")
        self.assertEqual(self.geak("status", job["id"])[1]["state"], "CANCELLED")
        done = self.submit()
        (Path(done["job_dir"]) / "DONE").touch()
        rc, res = self.geak("cancel", done["id"])
        self.assertIn("already completed", res["error"])
        other = self.submit()
        rc, res = self.geak("cancel", other["id"], env={"FAKE_SCANCEL_RC": "1"})
        self.assertEqual(rc, 1)
        self.assertIn("scancel failed", res["error"])

    def test_watch_and_list(self):
        job = self.submit()
        (self.fake / "squeue.out").write_text("RUNNING 1:00 n\n")
        rc, res = self.geak("watch", job["id"], "--interval", "0.2", "--timeout", "0.5")
        self.assertEqual(res["state"], "RUNNING")
        self.assertIn("timeout", res["watch"])
        (Path(job["job_dir"]) / "DONE").touch()
        rc, res = self.geak("watch", job["id"], "--interval", "0.2", "--timeout", "5")
        self.assertEqual((res["state"], res["watch"]), ("COMPLETED", "finished"))
        rc, res = self.geak("list")
        self.assertEqual([j["id"] for j in res["jobs"]], [job["id"]])

    def test_project_discovery_and_bad_ids(self):
        sub = self.project / "deep" / "dir"
        sub.mkdir(parents=True)
        job = self.submit()
        rc, res = self.geak("status", job["id"], cwd=sub)
        self.assertEqual(rc, 0, res)
        for bad in ("../x", "a/b", ""):
            rc, res = self.geak("status", bad)
            self.assertEqual(rc, 1)


class ScriptSyntaxTests(unittest.TestCase):
    def test_bash_syntax(self):
        for name in ("run-geak.sbatch", "setup-geak.sh"):
            p = subprocess.run(["bash", "-n", str(FRONTIER / name)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, f"{name}: {p.stderr}")

    def test_run_script_keeps_safeguards(self):
        text = (FRONTIER / "run-geak.sbatch").read_text()
        for needle in ("CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0", "proxy.ccs.ornl.gov:3128",
                       "GEAK_IDLE_EXIT_S", "CLAUDE_CONFIG_DIR", "must be mode 600",
                       "mark_unfinished", "--gpus-per-task=\"${GPUS}\"", "gfx90a"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
