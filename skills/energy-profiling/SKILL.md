---
name: energy-profiling
description: Use when profiling GPU job energy or power consumption on AMD systems with Omnistat. Handles Omnistat/VictoriaMetrics setup, Slurm job instrumentation, report-card interpretation, and troubleshooting. Do NOT use for rocprofv3 kernel/API performance profiling.
---

# Energy profiling with Omnistat

`Omnistat` is a lightweight power/energy sampling daemon for AMD GPU systems. It periodically samples per-GPU (per-GCD) telemetry — utilization, memory use, temperature, power, and accumulated energy — stores it in a local [VictoriaMetrics](https://victoriametrics.com/) time-series database, and prints a per-job **report card** (energy in kWh) at the end of a Slurm job.

This is distinct from the `profiling` skill: `rocprofv3` profiles **kernel/API performance** (what runs fast), while Omnistat measures **energy and power over a job's lifetime** (what costs energy). Use `rocprofv3` to optimize kernels; use Omnistat to quantify and compare the energy cost of whole jobs and configurations.

See references for details on energy profiling in specific environments, e.g. LUMI.

## General setup

For general Omnistat setup follow the steps below.
For site-specific (e.g. LUMI) details, see the respective references.

Note that some systems may already have Omnistat installed and available.

### 1. Create the install directory

Use a directory that is permanent and shared across all nodes — e.g. `/scratch`.

```bash
mkdir -p /scratch/users/<USERNAME>/omnistat/
cd /scratch/users/<USERNAME>/omnistat/
```

### 2. Download and extract Omnistat and VictoriaMetrics

```bash
curl -OLJ https://github.com/ROCm/omnistat/archive/refs/tags/v1.12.0.tar.gz
tar xfz omnistat-1.12.0.tar.gz
```

```bash
VM_VERSION=v1.143.0
curl -OLJ https://github.com/VictoriaMetrics/VictoriaMetrics/releases/download/${VM_VERSION}/victoria-metrics-linux-amd64-${VM_VERSION}.tar.gz
tar xfz victoria-metrics-linux-amd64-${VM_VERSION}.tar.gz
# Binary is extracted as: victoria-metrics-prod
```

### 3. Install Omnistat Python dependencies

```bash
python3 -m venv omnistat-env
omnistat-env/bin/pip install -r requirements.txt -r requirements-query.txt -e omnistat-1.12.0/
```

### 4. Write Omnistat config

```ini
[omnistat.query]
prometheus_url = http://localhost:9090
system_name = AMD-HPC

[omnistat.collectors]
port = 8001
enable_rocm_smi = False
enable_amd_smi  = True # Only one SMI GPU collector may be active at a time; either rocm_smi or amd_smi.
enable_rms = True

[omnistat.collectors.rms]
job_detection_mode = file-based
job_detection_file = /tmp/omni_rmsjobinfo_user

[omnistat.usermode]
victoria_binary  = /scratch/users/<USERNAME>/omnistat/victoria-metrics-prod
victoria_datadir = data_prom
victoria_logfile = vic_server.log
```

## Using Omnistat in a Slurm job

```bash
export OMNISTAT_CONFIG=/scratch/users/<USERNAME>/omnistat/omnistat.config
export OMNISTAT_DIR=/scratch/users/<USERNAME>/omnistat/omnistat-env/bin

# At the start of the job — start VictoriaMetrics + local exporter.
# The samplerate flag --interval 10 samples every 10 seconds.
# Use --localjob if SSH between compute nodes is blocked, so the default SSH-based exporter launch silently fails.
$OMNISTAT_DIR/omnistat-usermode --start --interval 10 --localjob ${SLURM_JOB_ID}

# ... your srun / singularity commands here ...

# At the end of the job — stop exporters, print report card, stop VictoriaMetrics
$OMNISTAT_DIR/omnistat-usermode --stopexporters --localjob ${SLURM_JOB_ID}
$OMNISTAT_DIR/omnistat-query      --job ${SLURM_JOB_ID} --interval 10
$OMNISTAT_DIR/omnistat-usermode  --stopserver

# Archive the time-series DB so it isn't overwritten by the next job
mv data_prom data_prom_${SLURM_JOB_ID}
```

## Reading the report card

At job end Omnistat prints a per-GPU summary:

```
----------------------------------------------------------------------
Omnistat Report Card for Job Id: 18830188
----------------------------------------------------------------------

Job Overview (Num Nodes = 1, Machine = LUMI)
--> Start time = 2026-05-25 12:28:51
--> End   time = 2026-05-25 12:30:36
--> Duration  = 105 secs

GPU Statistics:

        | Utilization (%) |  Memory Use (%) | Temperature (C) |    Power (W)     | Energy (kWh) |
  GPU # |   Max    Mean   |   Max    Mean   |   Max    Mean   |   Max     Mean   |    Total     |
  ---------------------------------------------------------------------------------------------------
    0   | 100.00  49.75   |  3.10   1.80    | 57.00  48.70    | 393.00  273.60   |  5.10e-03    |
    ...
```

How to interpret:
- **One row per GCD** (8 rows on a full MI250X node). Sum the `Energy (kWh)` column across GPUs (and nodes) for total job energy.
- **Energy (kWh) Total** is the headline number for efficiency comparisons — normalize by useful work (e.g. samples or tokens) to get Wh/sample.
- **Utilization Mean** near 100% = compute-bound; low mean over a long job = idle/loading time wasting energy (a key optimization target — loading and idle often dominate total job energy).

## Exploring results locally

The archived VictoriaMetrics database is at `/<OUTPUTDIR>/data_prom_<JOBID>/`. User can visualize it on their local machine with the upstream "Exploring results locally" workflow (`docker compose up` using the `docker/` directory of the Omnistat source tree) using Grafana dashboards over the collected time series.

## Known limitations

| Symptom | Cause | Fix |
| ------- | ----- | --- |
| `KeyError: 'omnistat.query'` at query time | Missing config section | Add `[omnistat.query]` with `prometheus_url` and `system_name` |
| No data; shutdown takes ~0.01 s; no `exporter.log` | SSH-based exporter launch blocked on compute nodes | Use `--localjob ${SLURM_JOB_ID}` on `--start` and `--stopexporters` |
| Only one SMI collector allowed / collector error | Both `rocm_smi` and `amd_smi` enabled | Set `enable_amd_smi = False` |

## Resources

- [Omnistat (ROCm) — GitHub](https://github.com/ROCm/omnistat) — source, config reference, and the "Exploring results locally" Grafana workflow
- [VictoriaMetrics releases](https://github.com/VictoriaMetrics/VictoriaMetrics/releases) — pick a plain `vX.Y.Z` (community) build
- Complete Omnistat on LUMI step-by-step procedure: [references/omnistat-lumi.md](references/omnistat-lumi.md)
