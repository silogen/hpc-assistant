#!/usr/bin/env python3
"""Summarize cap-run.sbatch results under RUN_ROOT.

Per run (application window only): elapsed time, GPU energy (pm_counters accelN_energy,
else integrated hwmon package power), node/CPU/memory energy, average power, observed
sclk, cap-bound fraction and an optional figure of merit (--fom-regex). Per configuration:
mean, CV, and runtime/energy/EDP/ED2P ratios to --baseline, plus a Pareto flag.
Runs with a failed application, a cap mismatch or no energy data are excluded and listed.
"""
import argparse
import csv
import glob
import json
import math
import os
import re
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capspec  # noqa: E402

PM_GPU = re.compile(r"^accel\d+_energy$")
SCLK_TOL_MHZ = 25


def read_env(path):
    out = {}
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    out[k] = v
    return out


def load_samples(run_dir):
    series = defaultdict(list)
    for path in glob.glob(os.path.join(run_dir, "samples-*.csv")):
        with open(path, newline="") as fh:
            for row in csv.DictReader(fh):
                try:
                    t, v = float(row["timestamp_s"]), float(row["value"])
                except (TypeError, ValueError, KeyError):
                    continue
                if math.isfinite(t) and math.isfinite(v):
                    series[(row["node"], row["source"], row["sensor"], row["metric"])].append((t, v))
    for s in series.values():
        s.sort()
    return series


def interp(series, t):
    if not series or t < series[0][0] or t > series[-1][0]:
        return None
    for (t0, v0), (t1, v1) in zip(series, series[1:]):
        if t0 <= t <= t1:
            return v0 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return series[-1][1] if t == series[-1][0] else None


def counter_delta(series, start, end):
    a, b = interp(series, start), interp(series, end)
    if a is None or b is None or b < a:
        return None
    return b - a


def integrate(series, start, end, max_gap):
    """Trapezoidal integral of a sampled power series over [start, end]."""
    if not series or series[0][0] > start or series[-1][0] < end:
        return None
    pts = [(start, interp(series, start))] + [p for p in series if start < p[0] < end] + [(end, interp(series, end))]
    total = 0.0
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t1 - t0 > max_gap:
            return None
        total += 0.5 * (v0 + v1) * (t1 - t0)
    return total


def window(series, start, end):
    return [v for t, v in series if start <= t <= end]


def percentile(vals, q):
    if not vals:
        return None
    vals = sorted(vals)
    k = (len(vals) - 1) * q
    f = math.floor(k)
    c = min(f + 1, len(vals) - 1)
    return vals[f] + (vals[c] - vals[f]) * (k - f)


def analyze_run(run_dir, package_cards, max_gap, fom_regex, work_units):
    env = read_env(os.path.join(run_dir, "run.env"))
    timing = read_env(os.path.join(run_dir, "timing.env"))
    status = read_env(os.path.join(run_dir, "status.txt"))
    r = {"run_dir": run_dir, "label": env.get("label", "?"), "rep": env.get("rep", ""),
         "job_id": env.get("job_id", ""), "nodes": env.get("nodes", ""),
         "nodelist": env.get("nodelist", ""), "excluded": ""}
    try:
        lo, hi, cap = capspec.parse_label(r["label"])
    except capspec.CapError:
        lo = hi = cap = None
    r.update(sclk_min_mhz=lo, sclk_max_mhz=hi, power_cap_w=cap)
    reasons = []
    if timing.get("app_rc", status.get("application_exit")) != "0":
        reasons.append(f"app_rc={timing.get('app_rc', status.get('application_exit', 'missing'))}")
    vp = status.get("verify_pre", "")
    if vp not in ("0", "3"):
        reasons.append(f"verify_pre={vp or 'missing'}")
    r["caps_verified"] = {"0": "yes", "3": "unverified"}.get(vp, "no")
    try:
        start, end = float(timing["app_start_s"]), float(timing["app_end_s"])
    except (KeyError, ValueError):
        r["excluded"] = ";".join(reasons + ["no timing"])
        return r
    elapsed = end - start
    r["elapsed_s"] = elapsed
    s = load_samples(run_dir)

    def pm_sum(match):
        keys = [k for k in s if k[1] == "pm_counters" and match(k[2])]
        if not keys:
            return None
        deltas = [counter_delta(s[k], start, end) for k in keys]
        return None if any(d is None for d in deltas) else sum(deltas)

    gpu_pm = pm_sum(lambda n: bool(PM_GPU.match(n)))
    hw_keys = [k for k in s if k[1] == "hwmon" and k[3] == "power_W"
               and int(k[2][4:]) in package_cards]
    gpu_hw = None
    if hw_keys:
        parts = [integrate(s[k], start, end, max_gap) for k in hw_keys]
        gpu_hw = None if any(p is None for p in parts) else sum(parts)
    r["gpu_energy_pm_J"] = gpu_pm
    r["gpu_energy_hwmon_J"] = gpu_hw
    r["gpu_energy_J"] = gpu_pm if gpu_pm is not None else gpu_hw
    r["energy_source"] = "pm_counters" if gpu_pm is not None else ("hwmon" if gpu_hw is not None else "none")
    r["node_energy_J"] = pm_sum(lambda n: n == "energy")
    r["cpu_energy_J"] = pm_sum(lambda n: n == "cpu_energy")
    r["mem_energy_J"] = pm_sum(lambda n: n == "memory_energy")
    r["packages_hwmon"] = len(hw_keys)
    if r["gpu_energy_J"] is None:
        reasons.append("no gpu energy")
    else:
        r["avg_gpu_power_W"] = r["gpu_energy_J"] / elapsed if elapsed > 0 else None

    sclk = [v for k in s if k[1] == "hwmon" and k[3] == "sclk_MHz" for v in window(s[k], start, end)]
    if sclk:
        r["sclk_mean_mhz"] = statistics.fmean(sclk)
        r["sclk_p95_mhz"] = percentile(sclk, 0.95)
        r["sclk_max_mhz_obs"] = max(sclk)
        if hi is not None and max(sclk) > hi + SCLK_TOL_MHZ:
            r["warnings"] = f"observed sclk {max(sclk):.0f} > cap {hi}"
    pkg_power = [v for k in hw_keys for v in window(s[k], start, end)]
    if pkg_power and cap:
        r["pkg_power_mean_W"] = statistics.fmean(pkg_power)
        r["pkg_power_p95_W"] = percentile(pkg_power, 0.95)
        r["cap_bound_frac"] = sum(v >= 0.97 * cap for v in pkg_power) / len(pkg_power)
    if fom_regex:
        out = os.path.join(run_dir, "application.out")
        text = ""
        if os.path.exists(out):
            with open(out, errors="replace") as fh:
                text = fh.read()
        hits = re.findall(fom_regex, text)
        if hits:
            last = hits[-1][0] if isinstance(hits[-1], tuple) else hits[-1]
            try:
                r["fom"] = float(last)
            except ValueError:
                pass
    if work_units and r.get("gpu_energy_J") is not None:
        r["gpu_J_per_unit"] = r["gpu_energy_J"] / work_units
    r["excluded"] = ";".join(reasons)
    return r


def mean_std(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None, None
    return statistics.fmean(vals), (statistics.stdev(vals) if len(vals) > 1 else 0.0)


def summarize(runs, baseline):
    groups = defaultdict(list)
    for r in runs:
        if not r["excluded"]:
            groups[r["label"]].append(r)
    rows = []
    for lab, rs in groups.items():
        t, t_sd = mean_std([r.get("elapsed_s") for r in rs])
        e, e_sd = mean_std([r.get("gpu_energy_J") for r in rs])
        row = {"label": lab, "sclk_min_mhz": rs[0]["sclk_min_mhz"], "sclk_max_mhz": rs[0]["sclk_max_mhz"],
               "power_cap_w": rs[0]["power_cap_w"], "n": len(rs),
               "elapsed_s": t, "elapsed_cv": (t_sd / t if t else None),
               "gpu_energy_J": e, "gpu_energy_cv": (e_sd / e if e else None),
               "avg_gpu_power_W": (e / t if e is not None and t else None),
               "node_energy_J": mean_std([r.get("node_energy_J") for r in rs])[0],
               "sclk_mean_mhz": mean_std([r.get("sclk_mean_mhz") for r in rs])[0],
               "sclk_max_mhz_obs": max((r.get("sclk_max_mhz_obs") or 0) for r in rs) or None,
               "cap_bound_frac": mean_std([r.get("cap_bound_frac") for r in rs])[0],
               "fom": mean_std([r.get("fom") for r in rs])[0],
               "energy_source": ",".join(sorted({r["energy_source"] for r in rs}))}
        row["edp"] = e * t if e is not None and t is not None else None
        row["ed2p"] = e * t * t if e is not None and t is not None else None
        rows.append(row)
    base = next((r for r in rows if r["label"] == baseline), None)
    for row in rows:
        for key, out in (("elapsed_s", "runtime_ratio"), ("gpu_energy_J", "energy_ratio"),
                         ("edp", "edp_ratio"), ("ed2p", "ed2p_ratio"), ("fom", "fom_ratio")):
            row[out] = (row[key] / base[key]) if base and row.get(key) is not None and base.get(key) else None
    for row in rows:
        row["pareto"] = row["elapsed_s"] is not None and row["gpu_energy_J"] is not None and not any(
            o is not row and o["elapsed_s"] is not None and o["gpu_energy_J"] is not None
            and o["elapsed_s"] <= row["elapsed_s"] and o["gpu_energy_J"] <= row["gpu_energy_J"]
            and (o["elapsed_s"] < row["elapsed_s"] or o["gpu_energy_J"] < row["gpu_energy_J"])
            for o in rows)
    rows.sort(key=lambda r: (-(r["sclk_max_mhz"] or 0), -(r["power_cap_w"] or 0), r["sclk_min_mhz"] or 0))
    return rows, base


def fmt(v, nd=3):
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else ""
    if isinstance(v, float):
        return f"{v:.{nd}g}" if abs(v) >= 1e4 or abs(v) < 1e-2 else f"{v:.{nd}f}".rstrip("0").rstrip(".")
    return str(v)


def write_csv(path, rows):
    keys = []
    for r in rows:
        keys += [k for k in r if k not in keys]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_root")
    ap.add_argument("--baseline", default=capspec.DEFAULT_LABEL)
    ap.add_argument("--package-cards", default="0,2,4,6",
                    help="hwmon cards carrying package power (Frontier caps cards 0,2,4,6)")
    ap.add_argument("--max-gap", type=float, default=5.0, help="max seconds between power samples")
    ap.add_argument("--fom-regex", help="regex with one group capturing a number in application.out")
    ap.add_argument("--work-units", type=float, help="useful work per run (samples, steps...) for J/unit")
    ap.add_argument("--out-dir", help="where to write runs.csv, summary.csv, summary.md (default run_root)")
    args = ap.parse_args(argv)
    cards = {int(c) for c in args.package_cards.split(",") if c.strip()}
    dirs = sorted(os.path.dirname(p) for p in glob.glob(os.path.join(args.run_root, "**", "run.env"), recursive=True))
    if not dirs:
        print(f"no run.env under {args.run_root}", file=sys.stderr)
        return 1
    runs = [analyze_run(d, cards, args.max_gap, args.fom_regex, args.work_units) for d in dirs]
    rows, base = summarize(runs, args.baseline)
    out = args.out_dir or args.run_root
    os.makedirs(out, exist_ok=True)
    write_csv(os.path.join(out, "runs.csv"), runs)
    if rows:
        write_csv(os.path.join(out, "summary.csv"), rows)
    cols = [("label", "config"), ("n", "n"), ("elapsed_s", "time s"), ("elapsed_cv", "time CV"),
            ("gpu_energy_J", "GPU J"), ("gpu_energy_cv", "E CV"), ("avg_gpu_power_W", "GPU W"),
            ("sclk_mean_mhz", "sclk MHz"), ("cap_bound_frac", "cap-bound"),
            ("runtime_ratio", "t/t0"), ("energy_ratio", "E/E0"), ("edp_ratio", "EDP/EDP0"),
            ("ed2p_ratio", "ED2P/ED2P0"), ("pareto", "Pareto")]
    if any(r.get("fom") is not None for r in rows):
        cols.insert(-1, ("fom_ratio", "FOM/FOM0"))
    lines = ["| " + " | ".join(h for _, h in cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(fmt(r.get(k)) for k, _ in cols) + " |")
    excluded = [r for r in runs if r["excluded"]]
    notes = [f"Baseline: {args.baseline}" + ("" if base else " (NOT FOUND: ratios are empty)"),
             f"Runs: {len(runs)} found, {len(runs) - len(excluded)} used, {len(excluded)} excluded"]
    notes += [f"- excluded {r['run_dir']}: {r['excluded']}" for r in excluded]
    notes += [f"- warning {r['run_dir']}: {r['warnings']}" for r in runs if r.get("warnings")]
    unverified = [r for r in runs if r.get("caps_verified") == "unverified" and not r["excluded"]]
    if unverified:
        notes.append(f"- {len(unverified)} used run(s) could not read back caps (verify_pre=3)")
    md = "\n".join(lines + [""] + notes) + "\n"
    with open(os.path.join(out, "summary.md"), "w") as fh:
        fh.write(md)
    print(md, end="")
    with open(os.path.join(out, "summary.json"), "w") as fh:
        json.dump({"baseline": args.baseline, "configs": rows}, fh, indent=2, default=str)
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
