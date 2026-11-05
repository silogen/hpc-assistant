#!/usr/bin/env python3
"""Integrate normalized, mapped OAM power; never infer MI250X sensor identities.

CSV: timestamp_s,node,sensor,power_watts (long form, seconds and watts).
JSON map: {"node-name": {"package-label": "sensor-label", ...}, ...}.
Exactly four package entries per Frontier node are required. The caller must verify
that selected sensors represent distinct whole MI250X packages, not GCD duplicates.
"""
import argparse
import csv
import json
import math
from pathlib import Path
import sys


def integrate(rows, mapping, start, end, max_gap):
    if not all(math.isfinite(x) for x in (start, end, max_gap)) or end <= start or max_gap <= 0:
        raise ValueError("Require finite start < end and positive max-gap")
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("Provide a nonempty node/package/sensor map")
    selected = {}
    for node, packages in mapping.items():
        if not isinstance(node, str) or not node or not isinstance(packages, dict) or len(packages) != 4:
            raise ValueError("Each Frontier node requires exactly four mapped packages")
        sensors = list(packages.values())
        if any(not isinstance(s, str) or not s for s in sensors) or len(set(sensors)) != 4:
            raise ValueError("Select one distinct sensor per package (no duplicate sensor)")
        for package, sensor in packages.items():
            if not isinstance(package, str) or not package:
                raise ValueError("Package labels must be nonempty strings")
            selected[(node, sensor)] = (package, [])
    for row in rows:
        key = (row["node"], row["sensor"])
        if key not in selected:
            # Unselected companion-GCD streams are intentionally not summed.
            continue
        t, p = float(row["timestamp_s"]), float(row["power_watts"])
        if not math.isfinite(t) or not math.isfinite(p) or p < 0:
            raise ValueError(f"Invalid sample for {key}")
        selected[key][1].append((t, p))
    result = []
    for (node, sensor), (package, samples) in selected.items():
        samples.sort()
        if len(samples) < 2 or samples[0][0] > start or samples[-1][0] < end:
            raise ValueError(f"Missing full-window coverage for {node}/{sensor}")
        if any(a[0] == b[0] for a, b in zip(samples, samples[1:])):
            raise ValueError(f"Duplicate timestamp for {node}/{sensor}")
        energy = 0.0
        for (t0, p0), (t1, p1) in zip(samples, samples[1:]):
            left, right = max(t0, start), min(t1, end)
            if right <= left:
                continue
            if t1 - t0 > max_gap:
                raise ValueError(f"Sampling gap exceeds max-gap for {node}/{sensor}")
            # Linear interpolation clips bracketing intervals to the common window.
            pl = p0 + (p1 - p0) * (left - t0) / (t1 - t0)
            pr = p0 + (p1 - p0) * (right - t0) / (t1 - t0)
            energy += (pl + pr) * 0.5 * (right - left)
        result.append({"node": node, "package": package, "sensor": sensor,
                       "energy_joules": energy, "energy_kwh": energy / 3_600_000})
    total = sum(item["energy_joules"] for item in result)
    return {"scope": "mapped MI250X package energy only, not whole-node energy",
            "start_s": start, "end_s": end, "duration_s": end - start,
            "max_gap_s": max_gap, "edge_policy": "linear interpolation; no extrapolation",
            "nodes": len(mapping), "packages": result,
            "total_joules": total, "total_kwh": total / 3_600_000}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("mapping", type=Path)
    parser.add_argument("--start", type=float, required=True)
    parser.add_argument("--end", type=float, required=True)
    parser.add_argument("--max-gap", type=float, required=True)
    args = parser.parse_args()
    with args.csv.open(newline="") as source:
        result = integrate(csv.DictReader(source), json.loads(args.mapping.read_text()),
                           args.start, args.end, args.max_gap)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print(f"integrate_power: {exc}", file=sys.stderr)
        sys.exit(2)
