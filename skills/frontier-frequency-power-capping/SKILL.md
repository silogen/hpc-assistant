---
name: frontier-frequency-power-capping
description: Use when running GPU frequency-cap or power-cap experiments on Frontier (AMD MI250X). Covers the power cap in the job header (#SBATCH --gpu-power-cap, one job per cap), the max frequency changed at runtime with /usr/bin/set_gpu_max_sclk, the sclk min/max range (#SBATCH --gpu-srange), checking which caps were applied, sysfs/pm_counters telemetry, sweep planning and submission, and runtime/energy/EDP analysis. Do NOT use for rocprofv3 kernel profiling (profiling) or general Omnistat setup (energy-profiling).
---

# Frequency and power capping on Frontier

Frontier gives users three GPU power-management controls:

| Control | How | Scope | Values |
| --- | --- | --- | --- |
| **Power cap** | `#SBATCH --gpu-power-cap=W` in the job header | whole job; **one job per power cap** | integer 100–600 W **per MI250X package**, default 560 |
| **Max frequency** (freq cap) | `srun -N $SLURM_JOB_NUM_NODES --ntasks-per-node=1 --exclusive /usr/bin/set_gpu_max_sclk MHz` | runtime; can change between steps of the same job | above the current MIN (e.g. 501) up to 1700 MHz |
| **Min/max frequency range** | `#SBATCH --gpu-srange=MIN-MAX` in the job header | whole job | MIN ≥ 500, MIN < MAX, MAX ≤ 1700; default 500-1700 |
| (not a cap) | `#SBATCH --gpu-counters=0` | whole job | turns off the OLCF counter daemon; use 0 for measurements |

```bash
#!/bin/bash
#SBATCH -A abc123
#SBATCH -N 1
#SBATCH -t 01:00:00
#SBATCH --gpu-power-cap=300        # fixed for this job; another cap = another job
#SBATCH --gpu-counters=0

for maxfreq in 1700 1300 900; do   # max frequency can change inside the job
    srun -N "$SLURM_JOB_NUM_NODES" --ntasks-per-node=1 --exclusive /usr/bin/set_gpu_max_sclk "$maxfreq"
    srun -N1 -n8 -c7 --gpus-per-task=1 --gpu-bind=closest ./app > "out-${maxfreq}MHz.txt"
done
srun -N "$SLURM_JOB_NUM_NODES" --ntasks-per-node=1 --exclusive /usr/bin/set_gpu_max_sclk 1700   # restore
```

- **Power cap: one job per value.** It is applied by the job prolog and reset by the epilog. To test 560, 400 and 250 W, submit three jobs. A `--gpu-power-cap` on the `sbatch` command line overrides the header.
- **Max frequency: runtime.** `set_gpu_max_sclk` exists only on compute nodes. Run it once per node (`--ntasks-per-node=1`) **before** the application step, not inside it. It changes only the max; the min stays as set by the job (500 by default), so the smallest usable value is 501. The setting persists until changed again or until the job ends.
- **Min frequency** can only be raised with `#SBATCH --gpu-srange=MIN-MAX`. The OLCF guide documents this option, the Slurm plugin validates it at submission (checked with `sbatch --test-only`), and the prolog applies it with `rocm-smi --setsrange`. Its effect on a compute node has **not been confirmed yet**: the template's `verify_caps.py` reads `pp_od_clk_voltage` on every node and stops the job if the range is not the one requested. Check its output on the first run.

Sources: the [OLCF Frontier User Guide](https://docs.olcf.ornl.gov/systems/frontier_user_guide.html#olcf-custom-slurm-plugins) (section *OLCF Custom Slurm Plugins*; in a project also under `.hpc-assistant/docs/frontier/`), the plugin code on the login nodes, `/etc/slurm/lua.d/gpu-srange.lua` and `/etc/slurm/lua.d/gpu-power-cap.lua` (read 2026-11-05), and existing Frontier job scripts that use `set_gpu_max_sclk`. If the behavior described here looks wrong, re-read those two files; they are the source of truth. The full rules are in [references/mechanisms.md](references/mechanisms.md).

## When to Use This Skill

- Measuring how an application's runtime, energy, or power changes under a lower **max** frequency, a raised **min** frequency, or a power cap
- Planning and submitting a cap sweep (frequency only, power only, or a grid of both)
- Checking that the requested caps were actually applied on every node
- Computing energy, average power, EDP/ED2P, and energy per unit of work against a baseline
- Explaining the difference between frequency capping and power capping, or choosing values to test

## How the caps behave (essential rules)

1. **Header options apply to the whole job.** The job prolog sets `--gpu-power-cap` and `--gpu-srange` on every GPU of every node before the script starts, and the epilog resets them to 500-1700 MHz and 560 W. `srun --gpu-power-cap` on a step has **no effect**, and `rocm-smi --setpoweroverdrive`/`--setsrange` need root. A script that loops over several *power caps* inside one job measures the header's cap every time, so use one job per power cap. Only the max frequency has a runtime command (`set_gpu_max_sclk`, rule 7). Slurm records the accepted values in the job's `AdminComment` (`GPU_SRANGE=...,GPU_POWER_CAP=...,..._SRC=user|default`); check them with `sacct -j <jobid> -X -o JobID,AdminComment%150`.
2. **`--gpu-srange` syntax:** `MIN-MAX`, or a single number, which sets only MAX (`--gpu-srange=1300` means `500-1300`). MIN below 500 is rejected, and so is MIN ≥ MAX, so **MIN = MAX (a pinned clock) is impossible**. The nearest option is a narrow band such as `1299-1300`. The plugin accepts MAX > 1700 for vendor margin testing; do not use it for experiments. An invalid value makes the submission fail.
3. **The power cap is per package, not per GCD.** The plugin writes `power1_cap` for cards 0, 2, 4 and 6, one per MI250X. `--gpu-power-cap=300` lets each pair of GCDs draw up to 300 W, so 1200 W of GPU power per node. A one-GCD job still shares its package budget with the idle sibling GCD.
4. **The two knobs act differently.** A frequency cap limits the clock at all times, so it lowers power even for workloads far below 560 W. A power cap only changes anything when a package would otherwise exceed the cap; the firmware then lowers clock and voltage dynamically. A power cap of 500 W on a workload drawing 250 W does nothing.
5. **Min vs. max frequency answer different questions.**
   - Lowering **MAX** (`500-X`) is the energy-saving knob. It mostly costs runtime on compute-bound kernels and costs little on memory- or communication-bound phases.
   - Raising **MIN** (`X-1700`) keeps the clock from dropping during idle, communication, or launch gaps. It tests sensitivity to DVFS ramp-up latency in bursty workloads. It usually **raises** idle power and energy.
   - A **narrow band** (`X-1-X`) approximates a fixed clock, which is good for reproducible per-frequency measurements.
   - When combined, whichever limit binds first wins: a low power cap can hold the clock below MIN under heavy load.
6. **Memory clock is not capped.** `--gpu-srange` and `set_gpu_max_sclk` change only sclk. HBM bandwidth stays the same, which is why memory-bound codes often lose little performance at lower sclk.
7. **Runtime max frequency (`set_gpu_max_sclk`).** Run `srun -N "$SLURM_JOB_NUM_NODES" --ntasks-per-node=1 --exclusive /usr/bin/set_gpu_max_sclk MHz` from the batch script between application steps; it covers every node of the job. The value must stay above the job's sclk MIN, so use 501 rather than 500 with the default range. Restore 1700 (or the header max) at the end, so later steps of the same job are not left slow. Changing frequency inside a job means the same nodes and power cap apply to every frequency, which reduces node-to-node noise. However, each point inherits the thermal state of the previous one, so keep an idle gap between points and shuffle their order.

## Quick start: one configuration

Copy the template and edit the cap lines in its header:

```bash
export PROJECT=abc123
export CAP_SKILL_DIR=/abs/path/to/skills/frontier-frequency-power-capping   # must be readable from compute nodes
export RUN_ROOT=/lustre/orion/$PROJECT/scratch/$USER/caps/myapp

cp "$CAP_SKILL_DIR/templates/cap-run.sbatch" cap-300W.sbatch
sed -i 's/^#SBATCH --gpu-power-cap=.*/#SBATCH --gpu-power-cap=300/' cap-300W.sbatch   # or edit by hand
sbatch --account="$PROJECT" \
  --export=ALL,CAP_SKILL_DIR="$CAP_SKILL_DIR",RUN_ROOT="$RUN_ROOT" \
  cap-300W.sbatch srun -N1 -n8 -c7 --gpus-per-task=1 --gpu-bind=closest ./myapp input.dat
```

To sweep the max frequency inside the same job, add `CAP_FREQS` (space- or colon-separated MHz). The template then calls `set_gpu_max_sclk` before each point, runs the application once per value into its own result directory (`.../rep<N>-job<JOBID>-i<k>/`), and restores the header max at the end:

```bash
sbatch --account="$PROJECT" \
  --export=ALL,CAP_SKILL_DIR="$CAP_SKILL_DIR",RUN_ROOT="$RUN_ROOT",CAP_FREQS=1700:1500:1300:1100:900 \
  cap-300W.sbatch srun -N1 -n8 -c7 --gpus-per-task=1 --gpu-bind=closest ./myapp input.dat
```

`python3 $CAP_SKILL_DIR/scripts/capspec.py header 1300 300` prints valid header lines and rejects values the plugin would refuse. [templates/cap-run.sbatch](templates/cap-run.sbatch) does the following:

1. Reads the caps Slurm accepted from the job's `AdminComment` and uses them to label the run. If you also set `CAP_SRANGE`/`CAP_POWER` as a cross-check and they disagree, it stops (exit 5) before doing anything.
2. Writes `run.env`, `command.txt`, and `scontrol show job` into `$RUN_ROOT/<label>/rep<N>-job<JOBID>/`. The label looks like `s500-1300_p560`.
3. Runs [scripts/verify_caps.py](scripts/verify_caps.py) once per node. It reads the active sclk range (`pp_od_clk_voltage`) and the package power caps (`hwmon/power1_cap`) from sysfs. On a mismatch it **aborts before the application** (exit 4, `CAP_STRICT=1`). If the files cannot be read it continues with a warning (exit 3).
4. Starts [scripts/gpu_sampler.py](scripts/gpu_sampler.py) on every node (`srun --overlap`, 1 core). It records hwmon package power, sclk, mclk, and temperatures, plus HPE Cray `pm_counters` energy (node, CPU, memory, `accelN_energy` per package).
5. Records an idle window (`IDLE_SECONDS`, default 10 s) before and after the run, so the idle power of a raised MIN shows up.
6. Runs the application from the submit directory, saving `application.out/.err` and `timing.env` (start, end, exit code).
7. Verifies the caps again, stops the sampler through a stop file, and writes `status.txt`. With `CAP_FREQS`, steps 3–7 repeat for each frequency after `set_gpu_max_sclk`. If the application fails at one frequency, the template moves on to the next and the job exits with the first non-zero code.

The verification can also be run alone in any job: `srun --overlap -N$SLURM_NNODES --ntasks-per-node=1 -c1 python3 $CAP_SKILL_DIR/scripts/verify_caps.py --expect-srange 500-1300 --expect-power-cap 560`.

## Running a sweep

[scripts/cap_sweep.py](scripts/cap_sweep.py) submits one job per (configuration, repetition). For each configuration it writes a copy of the template, `$RUN_ROOT/jobs/cap-<label>.sbatch`, whose header holds that configuration's `#SBATCH --gpu-power-cap=`, `--gpu-srange=` and `--gpu-counters=` lines. Every value is checked against the plugin rules first. It does a **dry run by default**: it writes the job scripts for review and prints every `sbatch` line and the worst-case node-hours. `--submit` actually submits and appends to `$RUN_ROOT/manifest.csv`.

```bash
S=$CAP_SKILL_DIR/scripts
# Max-frequency sweep at 560 W plus power-cap sweep at default clocks (one factor at a time):
python3 $S/cap_sweep.py --account "$PROJECT" --run-root "$RUN_ROOT" \
  --srange 1500 --srange 1300 --srange 1100 --srange 900 --srange 700 \
  --power-cap 450 --power-cap 350 --power-cap 250 --power-cap 150 \
  --mode separate --reps 3 --shuffle --seed 1 --time 00:20:00 \
  -- srun -N1 -n8 -c7 --gpus-per-task=1 --gpu-bind=closest ./myapp input.dat

# One job per power cap; inside each job the max frequency is swept at runtime
# (set_gpu_max_sclk). 1700 is added automatically as the in-job baseline. Size --time for all points.
python3 $S/cap_sweep.py --account "$PROJECT" --run-root "$RUN_ROOT/rt" \
  --power-cap 560 --power-cap 400 --power-cap 250 --mode grid \
  --runtime-freqs 1500 1300 1100 900 700 501 --reps 3 --shuffle --time 02:00:00 \
  -- srun -N1 -n8 -c7 --gpus-per-task=1 --gpu-bind=closest ./myapp input.dat

# Min-frequency (floor) study, full grid with two power caps:
python3 $S/cap_sweep.py --account "$PROJECT" --run-root "$RUN_ROOT/floor" \
  --srange 500-1700 --srange 1000-1700 --srange 1400-1700 --power-cap 560 --power-cap 300 \
  --mode grid --reps 3 --shuffle -- srun -N1 -n8 -c7 --gpus-per-task=1 --gpu-bind=closest ./myapp
```

`--mode separate` always includes the baseline (`500-1700`, 560 W) once. `--max-jobs` (default 60) guards against flooding the queue. Other options: `--nodelist` pins all jobs to the same nodes (less node-to-node variation, longer queue waits), `--gpu-counters 1` (default 0), `--qos debug`, and `--extra-sbatch "..."`, which refuses `--gpu-*` options so the header stays the only source of the caps. Show the dry run to the user and get confirmation before `--submit`, since a sweep spends allocation.

## Analysis

```bash
python3 $S/analyze_caps.py "$RUN_ROOT" --fom-regex 'Throughput:\s*([0-9.]+)' --work-units 1e6
```

[scripts/analyze_caps.py](scripts/analyze_caps.py) writes `runs.csv` (one row per run, with exclusion reasons), `summary.csv`, `summary.json`, and `summary.md` (one row per configuration). Metrics are computed over the application window only:

| Metric | Definition |
| --- | --- |
| `gpu_energy_J` | Σ over nodes and packages of `accelN_energy` deltas from pm_counters (preferred). Fallback: the time integral of hwmon package power on cards `--package-cards` (default 0,2,4,6) |
| `avg_gpu_power_W` | GPU energy / elapsed time (whole job) |
| `runtime_ratio`, `energy_ratio` | t/t₀ and E/E₀ against `--baseline` (default `s500-1700_p560`) |
| `edp_ratio`, `ed2p_ratio` | (E·t)/(E₀·t₀) and (E·t²)/(E₀·t₀²); below 1 is better |
| `cap_bound_frac` | Fraction of package power samples ≥ 97 % of the cap. Near 0 means the power cap never acted |
| `sclk_mean/p95/max` | Observed engine clock; a max above the requested MAX + 25 MHz is flagged |
| `pareto` | No other configuration is both faster and lower-energy |
| `node_energy_J` | Node total from pm_counters (GPU + CPU + memory + rest). Do not add the GPU energy to it |

A run is excluded when the application exit code is non-zero, the pre-run check found a mismatch, or energy could not be computed (no counters, a counter reset, or sample gaps over `--max-gap`). Report excluded runs; do not drop them silently.

## Experiment design rules

Details, recommended value grids, and statistics are in [references/experiment-design.md](references/experiment-design.md).

1. **Validate first.** Run one unmodified configuration and confirm correct output, then one job at the default caps with the template to confirm verification and telemetry work on this node and driver.
2. **Fix the workload.** Use the same inputs, steps, batch size, precision, rank/GPU binding, module stack, and node count in every configuration. Change only the caps.
3. **Make runs long enough.** Aim for ≥ 60 s of steady GPU work per run (at least 30 s), so DVFS and thermal transients and sampling granularity matter less.
4. **Repeat and randomize.** Use ≥ 3 repetitions (5 when effects are < 5 %) in a shuffled order. Report the mean and CV, and treat differences smaller than about 2× the run-to-run CV as noise.
5. **Always include the baseline** (`500-1700`, 560 W, passed explicitly) in the same sweep, on the same software stack.
6. **Report time and energy together,** along with EDP and energy per unit of useful work. Never report energy savings without the runtime cost.
7. **Use work done for the figure of merit,** not GPU utilization. Low utilization at a low clock can still mean slower time-to-solution.

## Pitfalls

- **One job per power cap:** looping over power caps inside one job (with `srun --gpu-power-cap` or `rocm-smi --set*`) does not change anything; every iteration runs at the header value. Only the max frequency can change at runtime, with `set_gpu_max_sclk`.
- **`set_gpu_max_sclk` placement:** call it from the batch script with one task per node (`--ntasks-per-node=1 --exclusive`), never inside the application's `srun`. It does not exist on login nodes. A value at or below the sclk MIN (500 by default) is invalid; use 501.
- **Restore the clock:** a job that exits early after `set_gpu_max_sclk 900` leaves later steps of the same job at 900 MHz. The template restores the header max in its EXIT trap.
- **Header vs. command line:** a `--gpu-power-cap` on the `sbatch` command line overrides the `#SBATCH` line. Check `AdminComment` (`GPU_POWER_CAP_SRC=user` means the value was requested explicitly).
- **Commented-out lines do nothing:** `##SBATCH --gpu-power-cap=...` is ignored, and so is any `#SBATCH` line after the first command in the script.
- **Pinned clocks are rejected:** `--gpu-srange=1300-1300` fails (MIN must be < MAX). Use `1299-1300`.
- **Package vs. GCD:** do not sum hwmon power from both GCDs of a package. The sibling card may read 0, be missing, or duplicate the package value. The cap is per package.
- **Power caps above the draw do nothing:** if `cap_bound_frac` ≈ 0, the power cap did not bind. That is a valid result, not an error.
- **Node energy ≠ GPU energy:** node energy includes CPU, memory, NIC, and other components. Report each from its own counter.
- **OLCF counter daemon:** jobs with more than 500 nodes have `--gpu-counters=1` by default, which runs a rocprofiler-based sampler. Set `--gpu-counters=0` for clean measurements and when using rocprofv3, and use the same value in every configuration of a sweep.
- **`rocm-smi --set*` is not available:** users cannot change clocks or power caps with `rocm-smi`/`amd-smi` (that needs root). Use the header options and `set_gpu_max_sclk`. Read-only `rocm-smi`/`amd-smi` queries are fine.
- **Telemetry jobs are not application results:** a run with good telemetry but a failed application is excluded.
- **Short runs** mostly measure startup, I/O, and idle time. Separate them, or lengthen the measured phase.
- **CPU frequency is a different knob:** Slurm `--cpu-freq` is per step, and its governors are `OnDemand`, `Performance`, and `UserSpace`. Verify that it took effect before relying on it (see [references/mechanisms.md](references/mechanisms.md)).

## Workflow checklist

- [ ] Application validated at default caps (correct output, finite FOM)
- [ ] `CAP_SKILL_DIR` and `RUN_ROOT` on Orion, absolute paths without commas
- [ ] One default-cap template job: `verify_pre=0`, samples for all nodes, `energy_source=pm_counters`, `AdminComment` shows the requested caps
- [ ] Sweep planned with `cap_sweep.py` (dry run reviewed, node-hours accepted by the user)
- [ ] Sweep submitted; monitor with `squeue --me` and `sacct`
- [ ] `analyze_caps.py` run; excluded runs, CVs, and `cap_bound_frac` checked
- [ ] Results reported with baseline, runtime and energy ratios, EDP, repetitions, node list, and software stack

## Related skills

- `frontier-slurm-job-management`: accounts, partitions/QOS, binding (`-c7 --gpu-bind=closest`)
- `energy-profiling`: Omnistat on Frontier ([references/omnistat-frontier.md](../energy-profiling/references/omnistat-frontier.md)). It can replace `gpu_sampler.py` (`CAP_SAMPLER=0` plus `with_omnistat.sh`) when dashboards are needed
- `profiling`: rocprofv3, to explain why a kernel is or is not sensitive to the clock

## Testing the helper scripts

The offline tests need only Python and Bash; they use fake sysfs trees and a fake `srun`:

```bash
python3 -B -m unittest discover -s skills/frontier-frequency-power-capping/tests -v
```

## References

- [references/mechanisms.md](references/mechanisms.md): plugin rules, sysfs paths, read-only `rocm-smi`/`amd-smi` commands, pm_counters, CPU frequency
- [references/experiment-design.md](references/experiment-design.md): value grids, repetitions, statistics, reporting template
- [OLCF Frontier User Guide: OLCF Custom Slurm Plugins](https://docs.olcf.ornl.gov/systems/frontier_user_guide.html#olcf-custom-slurm-plugins)
