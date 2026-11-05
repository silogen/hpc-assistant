#!/usr/bin/env python3
"""Offline tests for the frontier-frequency-power-capping scripts (no GPU, no Slurm).

  python3 -B -m unittest discover -s skills/frontier-frequency-power-capping/tests -v
"""
import csv
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))

import analyze_caps  # noqa: E402
import cap_sweep  # noqa: E402
import capspec  # noqa: E402
import gpu_sampler  # noqa: E402
import verify_caps  # noqa: E402


def fake_sysfs(root, lo=500, hi=1700, cap_w=560, cards=8, pm=True):
    """MI250X-like node: package power/cap on even cards only."""
    for i in range(cards):
        dev = Path(root, f"sys/class/drm/card{i}/device")
        hw = dev / f"hwmon/hwmon{i}"
        hw.mkdir(parents=True)
        (dev / "vendor").write_text("0x1002\n")
        (dev / "pp_od_clk_voltage").write_text(
            f"OD_SCLK:\n0: {lo}Mhz\n1: {hi}Mhz\nOD_RANGE:\nSCLK:     500Mhz       1700Mhz\n")
        (dev / "pp_dpm_sclk").write_text(f"0: {lo}Mhz\n1: {hi}Mhz *\n")
        (hw / "freq1_input").write_text(f"{hi * 1000000}\n")
        (hw / "temp1_input").write_text("45000\n")
        if i % 2 == 0:
            (hw / "power1_cap").write_text(f"{cap_w * 1000000}\n")
            (hw / "power1_average").write_text("300000000\n")
    Path(root, "sys/class/drm/card0-DP-1").mkdir(parents=True)
    if pm:
        pmd = Path(root, "sys/cray/pm_counters")
        pmd.mkdir(parents=True)
        for k in range(4):
            (pmd / f"accel{k}_energy").write_text(f"{1000 * k} J 1700000000000000 us\n")
            (pmd / f"accel{k}_power").write_text("300 W 1700000000000000 us\n")
        (pmd / "energy").write_text("50000 J 1700000000000000 us\n")
        (pmd / "freshness").write_text("42\n")


class CapSpecTests(unittest.TestCase):
    def test_plugin_rules(self):
        self.assertEqual(capspec.parse_srange("default"), (500, 1700))
        self.assertEqual(capspec.parse_srange("1300"), (500, 1300))
        self.assertEqual(capspec.parse_srange("900-1700"), (900, 1700))
        for bad in ("1300-1300", "1400-1300", "400-1000", "abc", "500-900-1000", "1300.5"):
            with self.assertRaises(capspec.CapError, msg=bad):
                capspec.parse_srange(bad)
        with self.assertRaises(capspec.CapError):
            capspec.parse_srange("500-1800")
        self.assertEqual(capspec.parse_srange("500-1800", allow_above_max=True), (500, 1800))

    def test_power(self):
        self.assertEqual(capspec.parse_power("default"), 560)
        self.assertEqual(capspec.parse_power("100"), 100)
        self.assertEqual(capspec.parse_power("600"), 600)
        for bad in ("99", "601", "-5", "300W"):
            with self.assertRaises(capspec.CapError, msg=bad):
                capspec.parse_power(bad)

    def test_label_roundtrip_and_cli(self):
        self.assertEqual(capspec.parse_label(capspec.label(500, 1300, 400)), (500, 1300, 400))
        out = subprocess.run([sys.executable, str(SKILL / "scripts/capspec.py"), "flags", "1300", "default"],
                             capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "--gpu-srange=500-1300 --gpu-power-cap=560")
        hdr = subprocess.run([sys.executable, str(SKILL / "scripts/capspec.py"), "header", "default", "300"],
                             capture_output=True, text=True)
        self.assertEqual(hdr.stdout, "#SBATCH --gpu-power-cap=300\n#SBATCH --gpu-srange=500-1700\n")
        bad = subprocess.run([sys.executable, str(SKILL / "scripts/capspec.py"), "label", "1300-1300", "560"],
                             capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)


class VerifyTests(unittest.TestCase):
    def run_verify(self, root, *args):
        with redirect_stdout(io.StringIO()):
            return verify_caps.main(["--sysfs-root", root, "--quiet", *args])

    def test_match(self):
        with tempfile.TemporaryDirectory() as d:
            fake_sysfs(d, 500, 1300, 400)
            self.assertEqual(self.run_verify(d, "--expect-srange", "1300", "--expect-power-cap", "400"), 0)
            report = os.path.join(d, "r.json")
            self.run_verify(d, "--expect-srange", "1300", "--expect-power-cap", "400", "--json", report)
            data = json.loads(Path(report).read_text())
            self.assertEqual(data["cards_seen"], 8)
            self.assertEqual(data["cards_with_power_cap"], 4)

    def test_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            fake_sysfs(d, 500, 1700, 560)
            self.assertEqual(self.run_verify(d, "--expect-srange", "1300", "--expect-power-cap", "560"), 4)
            self.assertEqual(self.run_verify(d, "--expect-power-cap", "300"), 4)

    def test_unverified(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_verify(d), 3)


class SamplerTests(unittest.TestCase):
    def test_samples(self):
        with tempfile.TemporaryDirectory() as d:
            fake_sysfs(d)
            out = os.path.join(d, "s.csv")
            with redirect_stdout(io.StringIO()):
                rc = gpu_sampler.main(["--sysfs-root", d, "--out", out, "--interval", "0.05",
                                       "--max-seconds", "0.12", "--node", "n1"])
            self.assertEqual(rc, 0)
            rows = list(csv.DictReader(io.StringIO(Path(out).read_text())))
            power = {r["sensor"] for r in rows if r["metric"] == "power_W"}
            self.assertEqual(power, {"card0", "card2", "card4", "card6"})
            self.assertTrue(any(r["sensor"] == "accel3_energy" and r["metric"] == "J" for r in rows))
            self.assertFalse(any(r["sensor"] == "freshness" for r in rows))
            self.assertEqual(float(next(r for r in rows if r["metric"] == "power_W")["value"]), 300.0)

    def test_stop_file(self):
        with tempfile.TemporaryDirectory() as d:
            fake_sysfs(d, pm=False)
            stop = os.path.join(d, "stop")
            open(stop, "w").close()
            with redirect_stdout(io.StringIO()):
                rc = gpu_sampler.main(["--sysfs-root", d, "--out", os.path.join(d, "s.csv"),
                                       "--stop-file", stop])
            self.assertEqual(rc, 0)

    def test_no_sensors(self):
        with tempfile.TemporaryDirectory() as d:
            with redirect_stderr(io.StringIO()):
                rc = gpu_sampler.main(["--sysfs-root", d, "--out", os.path.join(d, "s.csv")])
            self.assertEqual(rc, 3)


class SweepTests(unittest.TestCase):
    def test_separate_mode_includes_baseline_once(self):
        cfgs = cap_sweep.build_configs(["default", "1300"], ["default", "300"], "separate")
        self.assertEqual(cfgs, [(500, 1700, 560), (500, 1300, 560), (500, 1700, 300)])
        self.assertEqual(len(cap_sweep.build_configs(["1500", "1300"], ["560", "300"], "grid")), 4)

    def test_dry_run_writes_header_scripts(self):
        with tempfile.TemporaryDirectory() as d:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cap_sweep.main(["--account", "abc123", "--run-root", d, "--srange", "1300",
                                     "--power-cap", "300", "--reps", "2", "--shuffle",
                                     "--", "srun", "-N1", "./app"])
            self.assertEqual(rc, 0)
            text = buf.getvalue()
            self.assertIn("6 jobs", text)
            self.assertNotIn("--gpu-power-cap", text.split("job scripts")[1].replace(".sbatch", ""))
            scripts = sorted(Path(d, "jobs").glob("cap-*.sbatch"))
            self.assertEqual([p.name for p in scripts], ["cap-s500-1300_p560.sbatch",
                             "cap-s500-1700_p300.sbatch", "cap-s500-1700_p560.sbatch"])
            body = Path(d, "jobs/cap-s500-1700_p300.sbatch").read_text()
            self.assertEqual(len(re.findall(r"^#SBATCH --gpu-power-cap=", body, re.M)), 1)
            self.assertIn("#SBATCH --gpu-power-cap=300\n", body)
            self.assertIn("#SBATCH --gpu-srange=500-1700\n", body)
            self.assertIn("#SBATCH --gpu-counters=0\n", body)
            p = subprocess.run(["bash", "-n", str(scripts[0])], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertFalse(os.path.exists(os.path.join(d, "manifest.csv")))

    def test_render_adds_missing_header_lines(self):
        out = cap_sweep.render_script("#!/bin/bash\n#SBATCH -N 1\necho hi\n", (900, 1300, 250), None)
        self.assertTrue(out.startswith("#!/bin/bash\n#SBATCH --gpu-power-cap=250\n#SBATCH --gpu-srange=900-1300\n"))
        with self.assertRaises(ValueError):
            cap_sweep.render_script("echo no shebang\n", (500, 1700, 560), None)

    def test_runtime_freqs_export(self):
        with tempfile.TemporaryDirectory() as d:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cap_sweep.main(["--account", "a", "--run-root", d, "--power-cap", "300", "--mode", "grid",
                                     "--runtime-freqs", "1300", "900", "--reps", "1", "--", "./app"])
            self.assertEqual(rc, 0)
            self.assertIn("CAP_FREQS=1700:1300:900", buf.getvalue())
            self.assertIn("runs the application 3 times", buf.getvalue())
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                cap_sweep.main(["--account", "a", "--run-root", d, "--srange", "1000-1700", "--mode", "grid",
                                "--runtime-freqs", "900", "--", "./app"])

    def test_rejects_cap_flags_in_extra_sbatch(self):
        with tempfile.TemporaryDirectory() as d, redirect_stdout(io.StringIO()), \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cap_sweep.main(["--account", "a", "--run-root", d, "--extra-sbatch", "--gpu-power-cap=200",
                            "--", "./app"])

    def test_rejects_invalid(self):
        with tempfile.TemporaryDirectory() as d, redirect_stdout(io.StringIO()):
            for extra in (["--srange", "1300-1300"], ["--power-cap", "50"], ["--reps", "100"]):
                with self.assertRaises(SystemExit, msg=extra), redirect_stderr(io.StringIO()):
                    cap_sweep.main(["--account", "a", "--run-root", d, *extra, "--", "./app"])

    def test_walltime(self):
        self.assertAlmostEqual(cap_sweep.walltime_hours("00:30:00"), 0.5)
        self.assertAlmostEqual(cap_sweep.walltime_hours("1-00:00:00"), 24)


def make_run(root, lo, hi, cap, rep, elapsed, pkg_power, app_rc=0, verify_pre=0, nodes=("n1",)):
    lab = capspec.label(lo, hi, cap)
    d = Path(root, lab, f"rep{rep}-job{rep}{lo}{hi}{cap}")
    d.mkdir(parents=True)
    (d / "run.env").write_text(f"label={lab}\nrep={rep}\njob_id=1\nnodes={len(nodes)}\n")
    t0 = 1000.0
    (d / "timing.env").write_text(f"app_start_s={t0}\napp_end_s={t0 + elapsed}\napp_rc={app_rc}\n")
    (d / "status.txt").write_text(f"application_exit={app_rc}\nverify_pre={verify_pre}\n")
    for node in nodes:
        with open(d / f"samples-{node}.csv", "w") as fh:
            fh.write("timestamp_s,node,source,sensor,metric,value\n")
            t = t0 - 5
            while t <= t0 + elapsed + 5:
                for c in (0, 2, 4, 6):
                    fh.write(f"{t},{node},hwmon,card{c},power_W,{pkg_power}\n")
                for c in range(8):
                    fh.write(f"{t},{node},hwmon,card{c},sclk_MHz,{hi}\n")
                for k in range(4):
                    fh.write(f"{t},{node},pm_counters,accel{k}_energy,J,{pkg_power * (t - t0) + 1e6}\n")
                t += 0.5
    return d


class AnalyzeTests(unittest.TestCase):
    def test_summary(self):
        with tempfile.TemporaryDirectory() as d:
            for rep in (1, 2):
                make_run(d, 500, 1700, 560, rep, 10.0, 400.0)
                make_run(d, 500, 1300, 560, rep, 12.0, 250.0)
            make_run(d, 500, 1100, 560, 1, 10.0, 300.0, app_rc=1)
            make_run(d, 500, 1700, 300, 1, 10.0, 300.0, verify_pre=4)
            with redirect_stdout(io.StringIO()):
                rc = analyze_caps.main([d])
            self.assertEqual(rc, 0)
            rows = {r["label"]: r for r in csv.DictReader(io.StringIO(Path(d, "summary.csv").read_text()))}
            base, low = rows["s500-1700_p560"], rows["s500-1300_p560"]
            self.assertAlmostEqual(float(base["gpu_energy_J"]), 4 * 400 * 10, places=3)
            self.assertAlmostEqual(float(low["gpu_energy_J"]), 4 * 250 * 12, places=3)
            self.assertAlmostEqual(float(low["energy_ratio"]), 12000 / 16000, places=6)
            self.assertAlmostEqual(float(low["runtime_ratio"]), 1.2, places=6)
            self.assertAlmostEqual(float(low["edp_ratio"]), (12000 * 12) / (16000 * 10), places=6)
            self.assertEqual(base["energy_source"], "pm_counters")
            self.assertEqual(low["pareto"], "True")
            self.assertNotIn("s500-1100_p560", rows)
            self.assertNotIn("s500-1700_p300", rows)
            md = Path(d, "summary.md").read_text()
            self.assertIn("app_rc=1", md)
            self.assertIn("verify_pre=4", md)

    def test_hwmon_fallback_matches_pm(self):
        with tempfile.TemporaryDirectory() as d:
            run = make_run(d, 500, 1700, 560, 1, 8.0, 200.0, nodes=("n1", "n2"))
            r = analyze_caps.analyze_run(str(run), {0, 2, 4, 6}, 5.0, None, None)
            self.assertAlmostEqual(r["gpu_energy_pm_J"], 2 * 4 * 200 * 8, places=3)
            self.assertAlmostEqual(r["gpu_energy_hwmon_J"], r["gpu_energy_pm_J"], places=3)
            self.assertAlmostEqual(r["cap_bound_frac"], 0.0)

    def test_integrate_rejects_gaps_and_partial_coverage(self):
        s = [(0, 100.0), (1, 100.0), (10, 100.0)]
        self.assertIsNone(analyze_caps.integrate(s, 0, 10, 5))
        self.assertAlmostEqual(analyze_caps.integrate(s, 0, 10, 20), 1000.0)
        self.assertIsNone(analyze_caps.integrate(s, -1, 10, 20))
        self.assertIsNone(analyze_caps.counter_delta([(0, 10.0), (5, 2.0)], 0, 5))


FAKE_SRUN = r"""#!/bin/bash
while (( $# )); do
    case $1 in
        -N|-n|-c) shift 2;;
        -*) shift;;
        *) break;;
    esac
done
exec "$@"
"""


FAKE_SET_SCLK = r"""#!/bin/bash
echo "$1" >> "$CAP_SYSFS_ROOT/set_sclk_calls"
for f in "$CAP_SYSFS_ROOT"/sys/class/drm/card*/device/pp_od_clk_voltage; do
    sed -i "s/^1: [0-9]*Mhz/1: ${1}Mhz/" "$f"
done
"""


class TemplateTests(unittest.TestCase):
    def test_bash_syntax(self):
        p = subprocess.run(["bash", "-n", str(SKILL / "templates/cap-run.sbatch")], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)

    @staticmethod
    def admin(srange, power, src="user"):
        return (f"GPU_SRANGE={srange},GPU_SRANGE_SRC={src},GPU_POWER_CAP={power},"
                f"GPU_POWER_CAP_SRC={src},GPU_COUNTERS=0,GPU_COUNTERS_SRC=user")

    def run_template(self, d, sysfs, admin, app, strict="1", **extra):
        bindir = Path(d, "bin")
        bindir.mkdir(exist_ok=True)
        (bindir / "srun").write_text(FAKE_SRUN)
        (bindir / "set_gpu_max_sclk").write_text(FAKE_SET_SCLK)
        (bindir / "scontrol").write_text(
            "#!/bin/bash\necho \"JobId=$SLURM_JOB_ID JobName=cap\"\n"
            "[[ -n $FAKE_ADMIN ]] && echo \"   AdminComment=$FAKE_ADMIN\"\nexit 0\n")
        for f in bindir.iterdir():
            f.chmod(0o755)
        env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", SLURM_JOB_ID="42",
                   SLURM_JOB_NUM_NODES="1", SLURM_SUBMIT_DIR=d, CAP_SKILL_DIR=str(SKILL),
                   RUN_ROOT=os.path.join(d, "runs"), FAKE_ADMIN=admin,
                   SET_SCLK_BIN=str(bindir / "set_gpu_max_sclk"),
                   CAP_SYSFS_ROOT=sysfs, SAMPLE_INTERVAL="0.1", IDLE_SECONDS="0.3", CAP_STRICT=strict,
                   **extra)
        for k in ("CAP_SRANGE", "CAP_POWER", "CAP_FREQS"):
            if k not in extra:
                env.pop(k, None)
        return subprocess.run(["bash", str(SKILL / "templates/cap-run.sbatch"), *app],
                              env=env, capture_output=True, text=True, timeout=120)

    def test_end_to_end_with_fake_node(self):
        with tempfile.TemporaryDirectory() as d:
            sysfs = os.path.join(d, "node")
            fake_sysfs(sysfs, 500, 1300, 560)
            p = self.run_template(d, sysfs, self.admin("500-1300", 560),
                                  ["bash", "-c", "sleep 0.5; echo 'FOM: 12.5'"])
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            run = Path(d, "runs/s500-1300_p560/rep0-job42")
            status = analyze_caps.read_env(run / "status.txt")
            self.assertEqual(status["application_exit"], "0")
            self.assertEqual(status["verify_pre"], "0")
            self.assertEqual(status["verify_post"], "0")
            self.assertTrue(list(run.glob("samples-*.csv")))
            with redirect_stdout(io.StringIO()):
                rc = analyze_caps.main([os.path.join(d, "runs"), "--fom-regex", r"FOM:\s*([0-9.]+)",
                                        "--baseline", "s500-1300_p560"])
            self.assertEqual(rc, 0)
            row = next(csv.DictReader(io.StringIO(Path(d, "runs/summary.csv").read_text())))
            self.assertEqual(float(row["fom"]), 12.5)
            self.assertEqual(row["energy_source"], "pm_counters")

    def test_strict_mismatch_aborts_before_app(self):
        with tempfile.TemporaryDirectory() as d:
            sysfs = os.path.join(d, "node")
            fake_sysfs(sysfs, 500, 1700, 560)
            marker = os.path.join(d, "ran")
            p = self.run_template(d, sysfs, self.admin("500-1300", 560), ["touch", marker])
            self.assertEqual(p.returncode, 4, p.stdout + p.stderr)
            self.assertFalse(os.path.exists(marker))
            status = analyze_caps.read_env(Path(d, "runs/s500-1300_p560/rep0-job42/status.txt"))
            self.assertEqual(status["verify_pre"], "4")
            self.assertEqual(status["application_exit"], "not_run")

    def test_app_failure_propagates(self):
        with tempfile.TemporaryDirectory() as d:
            sysfs = os.path.join(d, "node")
            fake_sysfs(sysfs)
            p = self.run_template(d, sysfs, self.admin("500-1700", 560, "default"), ["bash", "-c", "exit 7"])
            self.assertEqual(p.returncode, 7, p.stdout + p.stderr)

    def test_power_cap_from_header_labels_run(self):
        with tempfile.TemporaryDirectory() as d:
            sysfs = os.path.join(d, "node")
            fake_sysfs(sysfs, 500, 1700, 200)
            p = self.run_template(d, sysfs, self.admin("500-1700", 200), ["true"])
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            env = analyze_caps.read_env(Path(d, "runs/s500-1700_p200/rep0-job42/run.env"))
            self.assertEqual(env["power_cap_w"], "200")
            self.assertIn("GPU_POWER_CAP=200", env["admin_comment"])

    def test_runtime_frequency_loop(self):
        with tempfile.TemporaryDirectory() as d:
            sysfs = os.path.join(d, "node")
            fake_sysfs(sysfs, 500, 1700, 300)
            p = self.run_template(d, sysfs, self.admin("500-1700", 300), ["bash", "-c", "echo ok"],
                                  CAP_FREQS="1300:900 1700")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            calls = Path(sysfs, "set_sclk_calls").read_text().split()
            self.assertEqual(calls, ["1300", "900", "1700", "1700"])
            for i, lab in enumerate(["s500-1300_p300", "s500-900_p300", "s500-1700_p300"], 1):
                run = Path(d, f"runs/{lab}/rep0-job42-i{i}")
                status = analyze_caps.read_env(run / "status.txt")
                self.assertEqual(status["verify_pre"], "0", lab)
                self.assertEqual(status["application_exit"], "0", lab)
                self.assertEqual(analyze_caps.read_env(run / "run.env")["freq_control"], "set_gpu_max_sclk")
                self.assertEqual((run / "application.out").read_text().strip(), "ok")

    def test_runtime_frequency_rejects_bad_values(self):
        with tempfile.TemporaryDirectory() as d:
            sysfs = os.path.join(d, "node")
            fake_sysfs(sysfs)
            for bad in ("500", "1800", "abc"):
                p = self.run_template(d, sysfs, self.admin("500-1700", 560), ["true"], CAP_FREQS=bad)
                self.assertEqual(p.returncode, 2, bad)
            self.assertFalse(Path(sysfs, "set_sclk_calls").exists())

    def test_variables_disagreeing_with_header_abort(self):
        with tempfile.TemporaryDirectory() as d:
            sysfs = os.path.join(d, "node")
            fake_sysfs(sysfs, 500, 1700, 200)
            marker = os.path.join(d, "ran")
            p = self.run_template(d, sysfs, self.admin("500-1700", 200), ["touch", marker], CAP_POWER="300")
            self.assertEqual(p.returncode, 5, p.stdout + p.stderr)
            self.assertFalse(os.path.exists(marker))

    def test_missing_admin_comment_falls_back_to_variables(self):
        with tempfile.TemporaryDirectory() as d:
            sysfs = os.path.join(d, "node")
            fake_sysfs(sysfs, 500, 1700, 300)
            p = self.run_template(d, sysfs, "", ["true"], CAP_POWER="300")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("AdminComment", p.stderr)
            self.assertTrue(Path(d, "runs/s500-1700_p300").is_dir())


if __name__ == "__main__":
    unittest.main()
