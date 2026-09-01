# Energy Profiling with Omnistat on LUMI

This is the complete, step-by-step procedure for setting up and using Omnistat on LUMI.

In a shared project, steps 1–5 may already have been done by a teammate — check
`/projappl/$PROJECT/omnistat` before repeating them.

Throughout, `$PROJECT` is your LUMI project with a GPU allocation, e.g. `project_46XXXXXXX`.
Prerequisites: access to `/projappl/$PROJECT` and `/scratch/$PROJECT`.

## One-time setup (per project)

In a shared project, the setup may already be done — check `/projappl/$PROJECT/omnistat` first.

### 1. Create the install directory

Use `/projappl` — it is permanent, shared across all nodes, and well-suited for software.

```bash
export PROJECT=project_46XXXXXXX        # replace with your project number
mkdir -p /projappl/$PROJECT/omnistat
cd /projappl/$PROJECT/omnistat
```

### 2. Download and extract Omnistat

```bash
curl -OLJ https://github.com/ROCm/omnistat/archive/refs/tags/v1.12.0.tar.gz
tar xfz omnistat-1.12.0.tar.gz
```

### 3. Install Python dependencies with the LUMI container wrapper

Do **not** use `pip install --user` or a plain venv directly on Lustre — it creates
thousands of small files and will exhaust your inode quota. The LUMI container wrapper
(tykky) bakes the pip environment into a SquashFS image so only a handful of files land on
the filesystem.

```bash
module load CrayEnv
module load lumi-container-wrapper/0.4.2-cray-python-default
```

Create a combined requirements file. The `omnistat @ file://...` line installs omnistat
itself from the local source tree (it is not on PyPI):

```bash
cat omnistat-1.12.0/requirements.txt \
    omnistat-1.12.0/requirements-query.txt \
    > /tmp/omnistat-reqs.txt

echo "omnistat @ file:///projappl/$PROJECT/omnistat/omnistat-1.12.0" \
    >> /tmp/omnistat-reqs.txt
```

Run the wrapper (takes ~2 minutes):

```bash
pip-containerize new \
    --prefix /projappl/$PROJECT/omnistat/omnistat-env \
    /tmp/omnistat-reqs.txt
```

Verify the entry-point scripts were created:

```bash
ls /projappl/$PROJECT/omnistat/omnistat-env/bin/omnistat-*
# Expected: omnistat-annotate  omnistat-monitor  omnistat-query
#           omnistat-rms-env    omnistat-standalone  omnistat-usermode
```

### 4. Download and extract VictoriaMetrics

A single binary — no small-files issue. Use the community single-node `linux-amd64` build
(not the `-enterprise` or `-cluster` variants). Releases named `vX.Y.Z` without a suffix are
the community edition.

```bash
VM_VERSION=v1.143.0
curl -OLJ https://github.com/VictoriaMetrics/VictoriaMetrics/releases/download/${VM_VERSION}/victoria-metrics-linux-amd64-${VM_VERSION}.tar.gz
tar xfz victoria-metrics-linux-amd64-${VM_VERSION}.tar.gz
# Binary is extracted as: victoria-metrics-prod
```

### 5. Write the runtime config

Save as `/projappl/$PROJECT/omnistat/omnistat.config`.

Replace `$PROJECT`in the config file with the literal project ID (e.g. `project_462001170`).

```ini
[omnistat.query]
prometheus_url = http://localhost:9090
system_name = LUMI

[omnistat.collectors]
port = 8001
enable_rocm_smi = False
enable_amd_smi  = True    # required for real power readings on LUMI (see note below)
enable_rms = True

[omnistat.collectors.rms]
job_detection_mode = file-based
job_detection_file = /tmp/omni_rmsjobinfo_user

[omnistat.usermode]
victoria_binary  = /projappl/$PROJECT/omnistat/victoria-metrics-prod
victoria_datadir = data_prom
victoria_logfile = vic_server.log
```

## Using Omnistat in a Slurm job

Add the start/stop lines around your workload in any GPU job script.

```bash
export OMNISTAT_CONFIG=/projappl/$PROJECT/omnistat/omnistat.config
export OMNISTAT_DIR=/projappl/$PROJECT/omnistat/omnistat-env/bin

# At the start of the job — start VictoriaMetrics + local exporter.
# The samplerate flag --interval 10 samples every 10 seconds.
# --localjob is REQUIRED: SSH between compute nodes is blocked on LUMI, so the default SSH-based exporter launch silently fails.
$OMNISTAT_DIR/omnistat-usermode --start --interval 10 --localjob ${SLURM_JOB_ID}

# ... your srun / singularity commands here ...

# At the end of the job — stop exporters, print report card, stop VictoriaMetrics
$OMNISTAT_DIR/omnistat-usermode --stopexporters --localjob ${SLURM_JOB_ID}
$OMNISTAT_DIR/omnistat-query      --job ${SLURM_JOB_ID} --interval 10
$OMNISTAT_DIR/omnistat-usermode  --stopserver
mv data_prom data_prom_${SLURM_JOB_ID}
```

Point `#SBATCH --chdir` to `/scratch/$PROJECT/runs/%j` (or `cd` there at the top of the
script) so `data_prom` and `vic_server.log` land on scratch rather than home or projappl.

Set `--account` and a GPU partition (e.g. `small-g`) as in the `lumi-slurm-job-management` skill.

## Known limitations

| Symptom | Cause | Fix |
| ------- | ------- | --- |
| Inode quota exceeded | Raw pip/venv install on Lustre | Use `pip-containerize` (lumi-container-wrapper), which bakes the environment into a SquashFS image (a handful of files) |
| `KeyError: 'omnistat.query'` at query time | `[omnistat.query]` section missing from config | Add the section with `prometheus_url` and `system_name` |
| `Power (W) = 0.00` on all nodes — rocm-smi C API returns `RSMI_STATUS_NOT_YET_IMPLEMENTED` in user mode on LUMI | `enable_rocm_smi = True` (default) | Set `enable_amd_smi = True`, `enable_rocm_smi = False` in config; amdsmi loads from `/opt/rocm/share/amd_smi/` automatically |
| Only one SMI GPU data collector may be configured; collector startup error | Both SMI collectors enabled simultaneously | Set exactly one to `True` — use `enable_amd_smi = True`, `enable_rocm_smi = False` |
| `exporter.log` absent, shutdown takes 0.01 s, no data collected | SSH-based exporter launch blocked on compute nodes | Use `--localjob ${SLURM_JOB_ID}` on `--start` and `--stopexporters` |
| Fills storage quota | `data_prom` written to home/projappl | Set job working directory to `/scratch/$PROJECT/...` |
| Omnistat's default SSH-based exporter launch silently fails (no data collected) | SSH between compute nodes is blocked on LUMI | Always pass `--localjob ${SLURM_JOB_ID}` to `--start` and `--stopexporters` |

## Resources

- [Omnistat (ROCm) — GitHub](https://github.com/ROCm/omnistat) — source, config reference, and the "Exploring results locally" Grafana workflow
- [VictoriaMetrics releases](https://github.com/VictoriaMetrics/VictoriaMetrics/releases) — pick a plain `vX.Y.Z` (community) build
- [LUMI container wrapper (tykky)](https://docs.lumi-supercomputer.eu/software/installing/container-wrapper/) — `pip-containerize` usage