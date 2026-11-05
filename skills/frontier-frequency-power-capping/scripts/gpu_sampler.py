#!/usr/bin/env python3
"""Sample GPU and node telemetry from sysfs on one node into a long-form CSV.

Columns: timestamp_s,node,source,sensor,metric,value
  hwmon:       cardN power_W, sclk_MHz, mclk_MHz, temp_*_C (package power on one GCD per pair)
  pm_counters: HPE Cray node/cpu/memory/accelN energy (J) and power (W)
Stops on --stop-file, --max-seconds, SIGTERM or SIGINT. Run once per node.
"""
import argparse
import glob
import os
import re
import signal
import socket
import sys
import time

HWMON_FILES = {
    "power1_average": ("power_W", 1e6),
    "power1_input": ("power_W", 1e6),
    "freq1_input": ("sclk_MHz", 1e6),
    "freq2_input": ("mclk_MHz", 1e6),
    "temp1_input": ("temp_edge_C", 1e3),
    "temp2_input": ("temp_junction_C", 1e3),
    "temp3_input": ("temp_mem_C", 1e3),
}
PM_SKIP = {"freshness", "generation", "raw_scan_hz", "startup", "version"}

_stop = False


def _handle(signum, frame):
    global _stop
    _stop = True


def read(path):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def discover(root):
    """Return [(card_index, {metric: (path, scale)})] for AMD cards."""
    out = []
    for card in glob.glob(os.path.join(root, "sys/class/drm/card*")):
        name = os.path.basename(card)
        if not re.fullmatch(r"card\d+", name):
            continue
        if read(os.path.join(card, "device/vendor")) != "0x1002":
            continue
        files = {}
        for hw in sorted(glob.glob(os.path.join(card, "device/hwmon/hwmon*"))):
            for fname, (metric, scale) in HWMON_FILES.items():
                path = os.path.join(hw, fname)
                if os.path.exists(path) and (metric not in files or fname == "power1_input"):
                    files[metric] = (path, scale)
        out.append((int(name[4:]), files))
    return sorted(out)


def pm_files(root):
    base = os.path.join(root, "sys/cray/pm_counters")
    if not os.path.isdir(base):
        return []
    return sorted(p for p in glob.glob(os.path.join(base, "*"))
                  if os.path.isfile(p) and os.path.basename(p) not in PM_SKIP)


def parse_pm(text):
    """pm_counters files look like '1234 J 1699999999123456 us'. Return (value, unit)."""
    if not text:
        return None
    parts = text.split()
    try:
        value = float(parts[0])
    except (ValueError, IndexError):
        return None
    unit = parts[1] if len(parts) > 1 and not parts[1].isdigit() else "raw"
    return value, unit


def sample_once(node, cards, pms, now):
    rows = []
    for idx, files in cards:
        for metric, (path, scale) in files.items():
            text = read(path)
            if text is None or not text.lstrip("-").isdigit():
                continue
            rows.append((now, node, "hwmon", f"card{idx}", metric, int(text) / scale))
    for path in pms:
        parsed = parse_pm(read(path))
        if parsed:
            rows.append((now, node, "pm_counters", os.path.basename(path), parsed[1], parsed[0]))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--stop-file")
    ap.add_argument("--max-seconds", type=float, default=0, help="0 = until stopped")
    ap.add_argument("--sysfs-root", default="/")
    ap.add_argument("--node", default=socket.gethostname().split(".")[0])
    args = ap.parse_args(argv)
    if not args.interval > 0:
        ap.error("--interval must be positive")
    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    cards, pms = discover(args.sysfs_root), pm_files(args.sysfs_root)
    if not cards and not pms:
        print(f"gpu_sampler: no AMD hwmon or pm_counters under {args.sysfs_root}", file=sys.stderr)
        return 3
    t0 = time.time()
    n = 0
    with open(args.out, "w") as fh:
        fh.write("timestamp_s,node,source,sensor,metric,value\n")
        next_t = time.monotonic()
        while True:
            now = time.time()
            for r in sample_once(args.node, cards, pms, now):
                fh.write(f"{r[0]:.6f},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]:.6g}\n")
            fh.flush()
            n += 1
            if _stop or (args.stop_file and os.path.exists(args.stop_file)):
                break
            if args.max_seconds and now - t0 >= args.max_seconds:
                break
            next_t += args.interval
            delay = next_t - time.monotonic()
            if delay > 0:
                end = time.monotonic() + delay
                while not _stop and time.monotonic() < end:
                    time.sleep(min(0.1, end - time.monotonic()))
            else:
                next_t = time.monotonic()
    print(f"gpu_sampler: {args.node}: {n} samples, {len(cards)} cards, {len(pms)} pm_counters files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
