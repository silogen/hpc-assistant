# Designing a capping experiment

## 1. Pick the question, then the knob

| Question | Configurations | Mode |
| --- | --- | --- |
| How much energy can I save, and at what slowdown? | lower **max** sclk: `default, 1500, 1300, 1100, 900, 700` | `separate` |
| How does the code behave under a power budget? | power caps: `560, 450, 350, 250, 150` (add 100 for an extreme point) | `separate` |
| Is the code sensitive to DVFS ramp-up (bursty, latency-bound, many small kernels or MPI waits)? | raise **min** sclk: `500-1700, 1000-1700, 1400-1700`, plus `1699-1700` (≈ pinned at max) | `grid` with 560 W |
| Per-frequency characterization (roofline, models) | narrow bands: `699-700, 899-900, ..., 1699-1700` | `grid` with 560 W |
| Which knob is better for this code? | 3–4 frequency caps and 3–4 power caps matched to similar average power | `separate`, then compare at equal energy or equal slowdown |
| Interaction | small grid: max ∈ {1700, 1300, 900} × cap ∈ {560, 350, 200} | `grid` |

Start coarse (about 200 MHz or 100 W steps) and refine around the knee of the energy curve.

How the sweep maps to jobs:

- **Power caps:** one job per value (`#SBATCH --gpu-power-cap=W`).
- **Max frequency:** prefer `set_gpu_max_sclk` inside the job (`CAP_FREQS` in the template, `--runtime-freqs` in `cap_sweep.py`). One job covers all frequencies at one power cap, on the same nodes. Include 1700 in every job as the in-job baseline (`cap_sweep.py` adds it), shuffle the order across repetitions, and keep `IDLE_SECONDS` between points.
- **Min frequency or narrow bands:** one job per range (`#SBATCH --gpu-srange=MIN-MAX`).
- **Frequency × power grid:** one job per power cap, each sweeping the frequencies at runtime: `--power-cap ... --mode grid --runtime-freqs ...`.

Tips for choosing values:

- First run the baseline with the sampler. Its `pkg_power_p95_W` shows where power caps can bind: caps above the package's p95 power do almost nothing.
- Memory-bound codes often keep their runtime down to about 1000–1100 MHz. Compute-bound codes slow down roughly in proportion to sclk.
- Very low power caps (≤ 200 W per package) usually give large slowdowns and can **increase** energy, because static power is paid for longer.

## 2. Control everything else

- Use the same binary, module stack (`module list` saved in `run.env`), input, problem size, number of steps, precision, ranks, binding, and node count.
- Write both cap lines explicitly in every job header, even for the baseline (`#SBATCH --gpu-power-cap=560`, `#SBATCH --gpu-srange=500-1700`); `cap_sweep.py` does this.
- Use the same `--gpu-counters` value in every configuration.
- Avoid I/O-dominated phases in the measured window, or measure them separately.
- Warm-up: if the application has JIT, autotuning, or data loading, exclude it from the FOM. Either measure an inner timed region (and parse it with `--fom-regex`) or make the steady state dominate the window.
- Frontier allocates whole nodes, so there is no noise from other jobs on the GPUs. Network and filesystem noise remain for multi-node runs.

## 3. Repetitions, ordering, nodes

- ≥ 3 repetitions per configuration, and 5 if the expected effect is under 5 %.
- Randomize the order (`--shuffle --seed N`) so drift over time (system load, filesystem, thermal) does not line up with configurations.
- Node-to-node variation in power and clock under a cap is real. Either:
  - pin all jobs to one node with `--nodelist` (cleanest comparison, possibly long queue waits), or
  - use the default placement and enough repetitions, recording `nodelist` (in `runs.csv`) to look for outlier nodes.
- Multi-node runs: the caps apply to all nodes. Report total energy over all nodes, and check that every node produced a samples file.

## 4. Statistics

`analyze_caps.py` reports, per configuration, the mean and the coefficient of variation (CV = std/mean) of time and energy.

- When the difference between two configurations is less than about 2 × the larger CV, call it "no measurable difference".
- For a formal test, export `runs.csv` and run Welch's t-test or a bootstrap confidence interval on the per-run values. Do not test on ratios of means.
- Report n for every configuration, along with excluded runs and the reasons.

## 5. Metrics and how to read them

| Metric | Use |
| --- | --- |
| Runtime ratio t/t₀ | Performance cost |
| Energy ratio E/E₀ | Energy saving; GPU energy and node energy separately |
| EDP = E·t | Balanced energy/performance trade-off; minimize |
| ED²P = E·t² | Weights performance more; minimize (common for HPC) |
| Energy per unit of work | J/sample, J/timestep, Wh/token (`--work-units`, or FOM-based) |
| Average power | Power-budget scenarios (facility caps) |
| `cap_bound_frac` | Did the power cap act? ~0 means no effect expected |
| Observed sclk | Did the frequency cap act? Is the clock below MAX because of power or thermal throttling? |

Look at the **Pareto front** (the `pareto` column) rather than a single "best" configuration. The right choice depends on whether the user is limited by allocation (node-hours, favor runtime) or by energy or power budget.

Energy saving in GPU energy is not the same as node energy saving: CPU and memory power keep running during the longer runtime. Report both when pm_counters are available.

## 6. Reporting template

```text
Application / version / input:            ...
Nodes x GCDs, binding:                     1 x 8, -c7 --gpu-bind=closest
Software stack:                            rocm/x.y, cpe/..., (from run.env)
Baseline:                                  --gpu-srange=500-1700 --gpu-power-cap=560
Configurations, repetitions, order:        ..., n=3 each, shuffled (seed 1)
Measured window:                           application start-end (idle excluded)
Energy source:                             pm_counters accelN_energy (GPU), energy (node)
Excluded runs:                             ...

| config | n | time s (CV) | GPU kJ (CV) | t/t0 | E/E0 | EDP/EDP0 | Pareto |
| ...

Conclusion: e.g. "500-1300 saves X % GPU energy for Y % slowdown; power caps
above Z W did not bind (cap_bound_frac < 0.01)."
```

## 7. Cost estimate

Node-hours = Σ jobs × nodes × walltime actually used (Frontier charges whole nodes). `cap_sweep.py` prints the worst case from the requested walltime. A 1-node sweep of 10 configurations × 3 repetitions × 20 min is at most 10 node-hours. Confirm with the user before submitting large or multi-node sweeps.
