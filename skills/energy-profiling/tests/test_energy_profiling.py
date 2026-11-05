#!/usr/bin/env python3
"""Offline tests for integrate_power.py and with_omnistat.sh syntax."""
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
INTEGRATE = SCRIPTS / "integrate_power.py"
WITH_OMNISTAT = SCRIPTS / "with_omnistat.sh"


def write_csv(path, rows):
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["timestamp_s", "node", "sensor", "power_watts"])
        writer.writerows(rows)


def four_package_map(node="frontier00001"):
    return {node: {"oam0": "sensor0", "oam1": "sensor1", "oam2": "sensor2", "oam3": "sensor3"}}


class IntegratePowerTests(unittest.TestCase):
    def test_integrates_constant_power_over_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            node = "frontier00001"
            csv_path = Path(tmp) / "power.csv"
            rows = []
            for sensor, watts in zip(("sensor0", "sensor1", "sensor2", "sensor3"), (100, 200, 300, 400)):
                rows += [[0, node, sensor, watts], [10, node, sensor, watts]]
            write_csv(csv_path, rows)
            mapping_path = Path(tmp) / "map.json"
            mapping_path.write_text(json.dumps(four_package_map(node)))
            result = subprocess.run([sys.executable, str(INTEGRATE), str(csv_path), str(mapping_path),
                                     "--start", "0", "--end", "10", "--max-gap", "60"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(len(payload["packages"]), 4)
            self.assertAlmostEqual(payload["total_joules"], (100 + 200 + 300 + 400) * 10)

    def test_rejects_fewer_than_four_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "power.csv"
            write_csv(csv_path, [[0, "n1", "s0", 100], [10, "n1", "s0", 100]])
            mapping_path = Path(tmp) / "map.json"
            mapping_path.write_text(json.dumps({"n1": {"oam0": "s0"}}))
            result = subprocess.run([sys.executable, str(INTEGRATE), str(csv_path), str(mapping_path),
                                     "--start", "0", "--end", "10", "--max-gap", "60"],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exactly four", result.stderr)

    def test_rejects_duplicate_sensor_across_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "power.csv"
            write_csv(csv_path, [[0, "n1", "s0", 100], [10, "n1", "s0", 100]])
            mapping_path = Path(tmp) / "map.json"
            mapping_path.write_text(json.dumps({"n1": {"oam0": "s0", "oam1": "s0", "oam2": "s0", "oam3": "s0"}}))
            result = subprocess.run([sys.executable, str(INTEGRATE), str(csv_path), str(mapping_path),
                                     "--start", "0", "--end", "10", "--max-gap", "60"],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("duplicate sensor", result.stderr)

    def test_rejects_window_not_fully_covered(self):
        with tempfile.TemporaryDirectory() as tmp:
            node = "frontier00001"
            csv_path = Path(tmp) / "power.csv"
            rows = []
            for sensor in ("sensor0", "sensor1", "sensor2", "sensor3"):
                rows += [[2, node, sensor, 100], [8, node, sensor, 100]]  # misses [0,10]
            write_csv(csv_path, rows)
            mapping_path = Path(tmp) / "map.json"
            mapping_path.write_text(json.dumps(four_package_map(node)))
            result = subprocess.run([sys.executable, str(INTEGRATE), str(csv_path), str(mapping_path),
                                     "--start", "0", "--end", "10", "--max-gap", "60"],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("coverage", result.stderr)

    def test_rejects_sampling_gap_over_max(self):
        with tempfile.TemporaryDirectory() as tmp:
            node = "frontier00001"
            csv_path = Path(tmp) / "power.csv"
            rows = []
            for sensor in ("sensor0", "sensor1", "sensor2", "sensor3"):
                rows += [[0, node, sensor, 100], [100, node, sensor, 100]]
            write_csv(csv_path, rows)
            mapping_path = Path(tmp) / "map.json"
            mapping_path.write_text(json.dumps(four_package_map(node)))
            result = subprocess.run([sys.executable, str(INTEGRATE), str(csv_path), str(mapping_path),
                                     "--start", "0", "--end", "100", "--max-gap", "10"],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("gap", result.stderr)

    def test_does_not_sum_unselected_companion_sensor(self):
        with tempfile.TemporaryDirectory() as tmp:
            node = "frontier00001"
            csv_path = Path(tmp) / "power.csv"
            rows = []
            for sensor, watts in zip(("sensor0", "sensor1", "sensor2", "sensor3"), (100, 200, 300, 400)):
                rows += [[0, node, sensor, watts], [10, node, sensor, watts]]
            rows += [[0, node, "unmapped_gcd_sensor", 9999], [10, node, "unmapped_gcd_sensor", 9999]]
            write_csv(csv_path, rows)
            mapping_path = Path(tmp) / "map.json"
            mapping_path.write_text(json.dumps(four_package_map(node)))
            result = subprocess.run([sys.executable, str(INTEGRATE), str(csv_path), str(mapping_path),
                                     "--start", "0", "--end", "10", "--max-gap", "60"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertAlmostEqual(payload["total_joules"], (100 + 200 + 300 + 400) * 10)


class ShellSyntaxTests(unittest.TestCase):
    def test_with_omnistat_bash_syntax(self):
        result = subprocess.run(["bash", "-n", str(WITH_OMNISTAT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_with_omnistat_rejects_missing_run_dir(self):
        import os
        env = dict(os.environ)
        env.update({"SLURM_JOB_ID": "1"})
        env.pop("RUN_DIR", None)
        result = subprocess.run(["bash", str(WITH_OMNISTAT), "echo", "hi"], env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
