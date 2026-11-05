#!/usr/bin/env python3
"""Plan and optionally submit a Frontier GPU frequency/power-cap sweep.

One job per (configuration, repetition); each configuration gets its own copy of
cap-run.sbatch in RUN_ROOT/jobs/ with the caps in the #SBATCH header. With
--runtime-freqs, each job also sweeps max sclk at runtime (set_gpu_max_sclk).
Dry run by default; --submit calls sbatch and appends to RUN_ROOT/manifest.csv.

Modes: grid (every srange x every power cap) or separate (one factor at a time,
baseline included once).

Example:
  cap_sweep.py --account abc123 --run-root /lustre/orion/abc123/scratch/$USER/caps \\
      --power-cap 560 --power-cap 300 --mode grid --runtime-freqs 1300 900 \\
      --reps 3 --shuffle -- srun -N1 -n8 -c7 --gpus-per-task=1 --gpu-bind=closest ./app
"""
import argparse
import csv
import datetime
import os
import random
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import capspec  # noqa: E402

SKILL_DIR = os.path.dirname(HERE)
TEMPLATE = os.path.join(SKILL_DIR, "templates", "cap-run.sbatch")


def walltime_hours(text):
    days = 0
    if "-" in text:
        d, text = text.split("-", 1)
        days = int(d)
    parts = [int(p) for p in text.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts[-3:]
    return days * 24 + h + m / 60 + s / 3600


def build_configs(sranges, powers, mode, allow_above_max=False):
    s_parsed = [capspec.parse_srange(s, allow_above_max) for s in (sranges or ["default"])]
    p_parsed = [capspec.parse_power(p) for p in (powers or ["default"])]
    base = (capspec.SCLK_MIN, capspec.SCLK_DEFAULT_MAX, capspec.POWER_DEFAULT)
    configs = []
    if mode == "grid":
        configs = [(lo, hi, w) for (lo, hi) in s_parsed for w in p_parsed]
    else:
        configs = [base]
        configs += [(lo, hi, capspec.POWER_DEFAULT) for (lo, hi) in s_parsed]
        configs += [(capspec.SCLK_MIN, capspec.SCLK_DEFAULT_MAX, w) for w in p_parsed]
    seen, unique = set(), []
    for c in configs:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


CAP_LINE = r"^#SBATCH\s+--gpu-{}=.*$"


def render_script(template_text, cfg, gpu_counters):
    """Return the template with its #SBATCH --gpu-* header lines set to cfg.

    Existing lines are replaced in place; missing ones are added after the shebang.
    """
    lo, hi, w = cfg
    wanted = {"power-cap": str(w), "srange": f"{lo}-{hi}"}
    if gpu_counters is not None:
        wanted["counters"] = str(gpu_counters)
    if not template_text.startswith("#!"):
        raise ValueError("template must start with a #! line")
    text, missing = template_text, []
    for opt, value in wanted.items():
        line = f"#SBATCH --gpu-{opt}={value}"
        pattern = re.compile(CAP_LINE.format(opt), re.M)
        if len(pattern.findall(text)) > 1:
            raise ValueError(f"template has more than one --gpu-{opt} header line")
        if pattern.search(text):
            text = pattern.sub(line, text)
        else:
            missing.append(line)
    if missing:
        head, _, rest = text.partition("\n")
        text = head + "\n" + "\n".join(missing) + "\n" + rest
    return text


def write_script(args, cfg):
    lab = capspec.label(*cfg)
    path = os.path.join(args.run_root, "jobs", f"cap-{lab}.sbatch")
    with open(args.template) as fh:
        text = render_script(fh.read(), cfg, args.gpu_counters)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)
    os.chmod(path, 0o755)
    return path


def sbatch_command(args, cfg, rep, app, script, freqs=None):
    lab = capspec.label(*cfg)
    export = ",".join([
        "ALL",
        f"CAP_SKILL_DIR={args.skill_dir}",
        f"RUN_ROOT={args.run_root}",
        f"CAP_REP={rep}",
        f"CAP_FREQS={':'.join(str(f) for f in freqs or [])}",
        f"SAMPLE_INTERVAL={args.sample_interval}",
        f"IDLE_SECONDS={args.idle_seconds}",
    ])
    cmd = ["sbatch", "--parsable", f"--account={args.account}",
           f"--job-name={args.job_prefix}-{lab}-r{rep}",
           f"--nodes={args.nodes}", f"--time={args.time}",
           f"--partition={args.partition}",
           f"--output={os.path.join(args.run_root, 'slurm', '%x-%j.out')}"]
    if args.qos:
        cmd.append(f"--qos={args.qos}")
    if args.nodelist:
        cmd.append(f"--nodelist={args.nodelist}")
    cmd += [f"--export={export}"]
    cmd += shlex.split(args.extra_sbatch or "")
    bad = [o for o in cmd if re.match(r"--gpu-(power-cap|srange|counters)", o)]
    if bad:
        raise ValueError(f"set caps with --srange/--power-cap/--gpu-counters, not {bad}")
    cmd += [script] + app
    return lab, cmd


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", required=True)
    ap.add_argument("--run-root", required=True, help="absolute Orion directory for all runs")
    ap.add_argument("--srange", action="append", help="default | MAX | MIN-MAX (repeatable)")
    ap.add_argument("--power-cap", action="append", help="default | watts per package (repeatable)")
    ap.add_argument("--mode", choices=["grid", "separate"], default="separate")
    ap.add_argument("--runtime-freqs", type=int, nargs="+", metavar="MHZ",
                    help="max sclk values set at runtime with set_gpu_max_sclk inside each job")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--shuffle", action="store_true", help="randomize job order (recommended)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--nodes", type=int, default=1)
    ap.add_argument("--time", default="00:30:00")
    ap.add_argument("--partition", default="batch")
    ap.add_argument("--qos")
    ap.add_argument("--nodelist", help="pin every job to these nodes (less variability, longer queue)")
    ap.add_argument("--gpu-counters", choices=["0", "1"], default="0",
                    help="#SBATCH --gpu-counters value for every job (default 0: OLCF counter daemon off)")
    ap.add_argument("--extra-sbatch", help="additional sbatch options, shell-quoted")
    ap.add_argument("--sample-interval", default="0.5")
    ap.add_argument("--idle-seconds", default="10")
    ap.add_argument("--job-prefix", default="cap")
    ap.add_argument("--template", default=TEMPLATE)
    ap.add_argument("--skill-dir", default=SKILL_DIR)
    ap.add_argument("--allow-above-max", action="store_true")
    ap.add_argument("--max-jobs", type=int, default=60, help="refuse larger sweeps (queue limits)")
    ap.add_argument("--submit", action="store_true", help="actually call sbatch")
    ap.add_argument("app", nargs=argparse.REMAINDER, help="-- COMMAND [ARGS...] run inside the job")
    args = ap.parse_args(argv)

    app = args.app[1:] if args.app and args.app[0] == "--" else args.app
    if not app:
        ap.error("give the application command after --")
    for path in (args.run_root, args.skill_dir, args.template):
        if not os.path.isabs(path):
            ap.error(f"{path} must be absolute")
        if "," in path:
            ap.error(f"{path} contains a comma, which breaks sbatch --export")
    if args.reps < 1 or args.nodes < 1:
        ap.error("--reps and --nodes must be >= 1")
    try:
        configs = build_configs(args.srange, args.power_cap, args.mode, args.allow_above_max)
        hours = walltime_hours(args.time)
    except (capspec.CapError, ValueError) as exc:
        ap.error(str(exc))

    freqs = list(dict.fromkeys(args.runtime_freqs or []))
    if freqs:
        if capspec.SCLK_DEFAULT_MAX not in freqs:
            freqs.insert(0, capspec.SCLK_DEFAULT_MAX)
        for f in freqs:
            if f > capspec.SCLK_DEFAULT_MAX or any(f <= lo for lo, _, _ in configs):
                ap.error(f"runtime frequency {f} MHz must be above every srange MIN and <= 1700")
    plan = [(cfg, rep) for rep in range(1, args.reps + 1) for cfg in configs]
    if args.shuffle:
        random.Random(args.seed).shuffle(plan)
    if len(plan) > args.max_jobs:
        ap.error(f"{len(plan)} jobs exceeds --max-jobs {args.max_jobs}; reduce the sweep or raise the limit")

    print(f"# {len(configs)} configurations x {args.reps} reps = {len(plan)} jobs; "
          f"worst-case charge {len(plan) * args.nodes * hours:.1f} node-hours "
          f"({args.nodes} node(s) x {args.time} each)")
    if args.submit:
        os.makedirs(os.path.join(args.run_root, "slurm"), exist_ok=True)
    try:
        scripts = {cfg: write_script(args, cfg) for cfg in configs}
        rng = random.Random(args.seed + 1)
        orders = []
        for _ in plan:
            order = list(freqs)
            if args.shuffle:
                rng.shuffle(order)
            orders.append(order)
        commands = [sbatch_command(args, cfg, rep, app, scripts[cfg], fo)
                    for (cfg, rep), fo in zip(plan, orders)]
    except (OSError, ValueError) as exc:
        ap.error(str(exc))
    print(f"# job scripts (caps in the #SBATCH header): {os.path.join(args.run_root, 'jobs')}")
    if freqs:
        print(f"# each job runs the application {len(freqs)} times (runtime max sclk: "
              f"{' '.join(map(str, freqs))} MHz); size --time for all of them")
    rows = []
    for order, ((cfg, rep), (lab, cmd), fo) in enumerate(zip(plan, commands, orders), 1):
        job_id = "dry-run"
        if args.submit:
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                print(f"sbatch failed for {lab} rep {rep}: {proc.stderr.strip()}", file=sys.stderr)
                job_id = "submit-failed"
            else:
                job_id = proc.stdout.strip().split(";")[0]
        print(f"{order:3d} {lab} rep{rep} {job_id}\n    {shlex.join(cmd)}")
        rows.append({"order": order, "label": lab, "sclk_min_mhz": cfg[0], "sclk_max_mhz": cfg[1],
                     "power_cap_w": cfg[2], "rep": rep, "job_id": job_id,
                     "runtime_freqs": " ".join(map(str, fo)),
                     "script": scripts[cfg], "sbatch": shlex.join(cmd)})

    if args.submit:
        manifest = os.path.join(args.run_root, "manifest.csv")
        new = not os.path.exists(manifest)
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        with open(manifest, "a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["submitted"] + list(rows[0].keys()))
            if new:
                w.writeheader()
            for r in rows:
                w.writerow({"submitted": stamp, **r})
        print(f"# manifest: {manifest}")
    else:
        print("# dry run: nothing submitted (add --submit)")
    return 1 if any(r["job_id"] == "submit-failed" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
