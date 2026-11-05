#!/usr/bin/env python3
"""Check the GPU sclk range and power caps on this node against the request.

Reads /sys/class/drm/cardN/device/pp_od_clk_voltage (OD_SCLK) and
hwmon/power1_cap (set on cards 0, 2, 4, 6: one per MI250X package). Read-only.

Exit codes (higher = worse, so srun's max task code stays meaningful):
  0 match, 3 could not verify, 4 mismatch, 2 usage error.
"""
import argparse
import glob
import json
import os
import re
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capspec  # noqa: E402

AMD_VENDOR = "0x1002"


def read(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return None


def amd_cards(root):
    cards = []
    for path in glob.glob(os.path.join(root, "sys/class/drm/card*")):
        name = os.path.basename(path)
        if not re.fullmatch(r"card\d+", name):
            continue
        vendor = (read(os.path.join(path, "device/vendor")) or "").strip()
        if vendor != AMD_VENDOR:
            continue
        cards.append((int(name[4:]), os.path.join(path, "device")))
    return sorted(cards)


def parse_od_sclk(text):
    """Return (min, max) MHz from the OD_SCLK section of pp_od_clk_voltage."""
    if not text:
        return None
    m = re.search(r"OD_SCLK:\s*\n(.*?)(?:\n[A-Z_]+:|\Z)", text, re.S)
    if not m:
        return None
    vals = [int(v) for v in re.findall(r"^\s*\d+:\s*(\d+)\s*[Mm][Hh]z", m.group(1), re.M)]
    if len(vals) < 2:
        return None
    return min(vals), max(vals)


def parse_od_range(text):
    if not text:
        return None
    m = re.search(r"SCLK:\s*(\d+)\s*[Mm][Hh]z\s+(\d+)\s*[Mm][Hh]z", text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_dpm(text):
    if not text:
        return None
    levels, current = [], None
    for line in text.splitlines():
        m = re.match(r"\s*\d+:\s*(\d+)\s*[Mm][Hh]z(\s*\*)?", line)
        if m:
            levels.append(int(m.group(1)))
            if m.group(2):
                current = int(m.group(1))
    return {"levels_mhz": levels, "current_mhz": current} if levels else None


def hwmon_value(dev, name, scale):
    for path in sorted(glob.glob(os.path.join(dev, "hwmon/hwmon*", name))):
        text = read(path)
        if text is not None and text.strip().lstrip("-").isdigit():
            return int(text.strip()) / scale
    return None


def inspect(root):
    cards = []
    for idx, dev in amd_cards(root):
        od_text = read(os.path.join(dev, "pp_od_clk_voltage"))
        cards.append({
            "card": idx,
            "sclk_range_mhz": parse_od_sclk(od_text),
            "sclk_od_limits_mhz": parse_od_range(od_text),
            "dpm_sclk": parse_dpm(read(os.path.join(dev, "pp_dpm_sclk"))),
            "power_cap_w": hwmon_value(dev, "power1_cap", 1e6),
            "power_cap_max_w": hwmon_value(dev, "power1_cap_max", 1e6),
            "power_cap_min_w": hwmon_value(dev, "power1_cap_min", 1e6),
            "perf_level": (read(os.path.join(dev, "power_dpm_force_performance_level")) or "").strip() or None,
        })
    return cards


def check(cards, lo, hi, watts, tol_w=1.0):
    problems, verified_s, verified_p = [], 0, 0
    for c in cards:
        rng = c["sclk_range_mhz"]
        if rng is not None:
            verified_s += 1
            if tuple(rng) != (lo, hi):
                problems.append(f"card{c['card']}: sclk range {rng[0]}-{rng[1]} MHz, expected {lo}-{hi}")
        cap = c["power_cap_w"]
        if cap is not None and cap > 0:
            verified_p += 1
            if abs(cap - watts) > tol_w:
                problems.append(f"card{c['card']}: power cap {cap:g} W, expected {watts} W")
    if problems:
        status = "mismatch"
    elif not cards or verified_s == 0 or verified_p == 0:
        status = "unverified"
    else:
        status = "ok"
    return status, problems, verified_s, verified_p


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--expect-srange", default="default", help="default | MAX | MIN-MAX (MHz)")
    ap.add_argument("--expect-power-cap", default="default", help="default | watts per package")
    ap.add_argument("--allow-above-max", action="store_true")
    ap.add_argument("--sysfs-root", default="/", help="prefix for /sys (tests)")
    ap.add_argument("--json", dest="json_out", help="write the full report here")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    try:
        lo, hi = capspec.parse_srange(args.expect_srange, args.allow_above_max)
        watts = capspec.parse_power(args.expect_power_cap)
    except capspec.CapError as exc:
        print(f"verify_caps: {exc}", file=sys.stderr)
        return 2
    cards = inspect(args.sysfs_root)
    status, problems, ns, np_ = check(cards, lo, hi, watts)
    report = {
        "host": socket.gethostname().split(".")[0],
        "time_s": time.time(),
        "expected": {"sclk_min_mhz": lo, "sclk_max_mhz": hi, "power_cap_w": watts},
        "status": status,
        "problems": problems,
        "cards_seen": len(cards),
        "cards_with_sclk_range": ns,
        "cards_with_power_cap": np_,
        "cards": cards,
        "env": {k: os.environ[k] for k in ("GPU_SRANGE", "GPU_SRANGE_SRC", "GPU_POWER_CAP",
                                           "GPU_POWER_CAP_SRC", "GPU_COUNTERS") if k in os.environ},
    }
    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(report, fh, indent=2)
    if not args.quiet:
        print(f"{report['host']}: {status}; {len(cards)} AMD cards, {ns} with sclk range, "
              f"{np_} with power cap; expected sclk {lo}-{hi} MHz, cap {watts} W/package")
        for p in problems:
            print("  " + p)
        if status == "unverified":
            print("  could not read pp_od_clk_voltage or power1_cap; check the node and driver", file=sys.stderr)
    return {"ok": 0, "unverified": 3, "mismatch": 4}[status]


if __name__ == "__main__":
    sys.exit(main())
