# Energy Profiling with Omnistat on Frontier

Step-by-step procedure for using Omnistat on ORNL's Frontier supercomputer. Omnistat is preinstalled on Frontier by AMD Research and provided through the `omnistat-wrapper` module, so a custom install is normally not needed.

Throughout, `<project_id>` is your Frontier project (e.g. `CSC000`); replace it literally.
Prerequisites: an active Frontier allocation and familiarity with Slurm job submission.

## 1. Load the module

Module discovery is fine on the login node:

```bash
module use /sw/frontier/amdsw/modulefiles
module spider omnistat-wrapper
module load omnistat-wrapper
echo "$OMNISTAT_WRAPPER"
"$OMNISTAT_WRAPPER" usermode --help
"$OMNISTAT_WRAPPER" query --help
```

Start the monitor, the application and the queries from a batch script (or an `srun` onto the allocated node), not from an `salloc` shell on the login node.

## 2. Minimal Slurm job

Start Omnistat once per allocation from the batch script:

```bash
#!/bin/bash
#SBATCH -A <project_id>
#SBATCH -J omnistat
#SBATCH -N 2
#SBATCH -t 01:00:00
#SBATCH -S 0

module unload darshan-runtime        # recommended in the OLCF Omnistat example
module use /sw/frontier/amdsw/modulefiles
module load omnistat-wrapper
${OMNISTAT_WRAPPER} usermode --start --interval 1.0

srun ./your_application

${OMNISTAT_WRAPPER} usermode --stopexporters
${OMNISTAT_WRAPPER} query --interval 1.0 --job ${SLURM_JOB_ID} --pdf omnistat.${SLURM_JOB_ID}.pdf
${OMNISTAT_WRAPPER} usermode --stopserver
```

`-S 0` disables Frontier's default core-specialization reservation for the job, as in the Omnistat Frontier documentation. The Frontier recipe does not use `--localjob` (on LUMI it works around blocked SSH between compute nodes). Check that all nodes report data (see Troubleshooting) rather than adding it.

Use `query ... --export` to also write CSV for offline analysis.

### Proxy variables

An HTTP(S) proxy in the job environment interferes with Omnistat's node-to-node communication. Unset it before starting:

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY proxy no_proxy all_proxy ftp_proxy
```

### Storage

By default the database is stored under
`/lustre/orion/${SLURM_JOB_ACCOUNT}/scratch/${USER}/omnistat/${SLURM_JOB_ID}`. Override it with `OMNISTAT_VICTORIA_DATADIR`, e.g. to use node-local `/tmp` and copy the data out at the end of the job:

```bash
export OMNISTAT_VICTORIA_DATADIR=/tmp/omnistat/${SLURM_JOB_ID}
${OMNISTAT_WRAPPER} usermode --start --interval 1.0
srun ./your_application
${OMNISTAT_WRAPPER} usermode --stopexporters
${OMNISTAT_WRAPPER} query --interval 1.0 --job ${SLURM_JOB_ID} --pdf omnistat.${SLURM_JOB_ID}.pdf
${OMNISTAT_WRAPPER} usermode --stopserver
mv /tmp/omnistat/${SLURM_JOB_ID} data_omnistat.${SLURM_JOB_ID}
```

The database needs `flock`, which Lustre and `/tmp` support but Frontier's `$HOME` does not; data can be moved to `$HOME` afterwards. A `/tmp` database lives only on the node that hosts the server and is lost when the allocation ends unless copied out. Orion scratch is purged, so archive results you want to keep.

## 3. Wrapper script with cleanup

[scripts/with_omnistat.sh](../scripts/with_omnistat.sh) wraps the sequence above for one command and keeps teardown working when the application fails:

```bash
module use /sw/frontier/amdsw/modulefiles
module load omnistat-wrapper
export RUN_DIR=/lustre/orion/<project_id>/scratch/${USER}/energy/${SLURM_JOB_ID}
bash /path/to/with_omnistat.sh srun -N 2 --ntasks-per-node=8 --gpus-per-task=1 --gpu-bind=closest ./your_application
```

It must be called once from the batch shell (not per rank), and `RUN_DIR` must be an absolute path that does not exist yet. It sets `OMNISTAT_VICTORIA_DATADIR=$RUN_DIR/data_prom`, saves the wrapper help and run metadata, writes the application's stdout/stderr to `application.out`/`application.err` while running it from the caller's directory, and on exit (including failure, `SIGTERM` or `SIGINT`) stops the exporters, writes `report.txt` from `query --export` and stops the server. `status.txt` records the application, wrapper and telemetry exit codes separately. If the application succeeds but teardown fails, the script exits 90. `OMNISTAT_INTERVAL` sets the sampling interval (default 1.0 s).

A walltime kill or node failure can still skip cleanup, so leave a few minutes of margin and treat such runs as partial.

## 4. Reading the report card

On Frontier the report card has one row per GCD (8 per node). Column meanings:

| Field | Interpretation |
| --- | --- |
| Utilization Max/Mean (%) | Sampled GPU activity, not a measure of compute saturation |
| Memory Use Max/Mean (%) | HBM capacity occupied, not HBM bandwidth |
| Temperature Max/Mean (C) | Sampled sensor temperature |
| Power Max/Mean (W) | Sensor-reported power; check whether it is per GCD or per package |
| Energy Total (kWh) | Derived from the energy counter or integrated power |

### MI250X energy accounting: package versus GCD

A Frontier node has 4 MI250X packages (OAMs) with 2 GCDs each. Utilization and memory are reported per GCD, but power and energy may be measured per package. Before summing the Energy column:

1. Save the collector version, raw metric names and units, node labels, and device serial/PCI IDs, and establish which GCD rows share a package sensor. Do not assume adjacent or even indices.
2. Count one valid sensor per package, four per node. Depending on driver and collector, the second GCD of a package may show zero, no value, or a duplicate of the first. Zero on that row does not mean the GCD was idle, and a missing sensor is not zero energy.
3. Do not double a package reading or sum duplicates from both GCDs. A single-GCD workload still draws shared package power; do not halve the package value to attribute it.
4. `rocm_energy_joules` is cumulative since driver load: use end minus start over the job window and check for resets. For sampled power, integrate over timestamps (E = ∫P dt), not by sample count. 1 kWh = 3,600,000 J.
5. GPU energy is not node energy. Use the vendor counters below for node, CPU and memory, and do not add the node total to its components.
6. Report energy per unit of useful work (J/sample, Wh/token) together with elapsed time, and compare runs with matched inputs, ranks, precision and warm-up.

[scripts/integrate_power.py](../scripts/integrate_power.py) integrates already exported power samples per package. It takes a long-form CSV (`timestamp_s,node,sensor,power_watts`) and a JSON map that assigns exactly one distinct sensor to each of the four packages per node, and integrates over a common window using linear interpolation:

```bash
python3 integrate_power.py power.csv sensor-map.json --start 1700000000 --end 1700000600 --max-gap 5
```

```json
{"frontier00001": {"oam0": "<sensor>", "oam1": "<sensor>", "oam2": "<sensor>", "oam3": "<sensor>"}}
```

It rejects incomplete coverage of the window, gaps larger than `--max-gap`, duplicate timestamps, negative or non-finite values, and duplicate sensors; unmapped sensors are ignored. The sensor labels are whatever the export contains; the script does not parse Omnistat's export format or infer the mapping.

## 5. Vendor counters

Frontier provides an extra collector (`enable_vendor_counters`) that reads the HPE Cray `pm_counters` interface for node-level components. The `accel` label may not follow ROCm's GPU ordering, so reconcile indices before combining with ROCm metrics.

| Node Metric | Description |
| --- | --- |
| `omnistat_vendor_energy_joules` | Total node energy (J). Labels: `vendor`. |
| `omnistat_vendor_power_watts` | Instantaneous total node power (W). Labels: `vendor`. |
| `omnistat_vendor_cpu_energy_joules` | Cumulative CPU energy (J). Labels: `vendor`. |
| `omnistat_vendor_cpu_power_watts` | Instantaneous CPU power (W). Labels: `vendor`. |
| `omnistat_vendor_memory_energy_joules` | Cumulative system memory energy (J). Labels: `vendor`. |
| `omnistat_vendor_memory_power_watts` | Instantaneous system memory power (W). Labels: `vendor`. |

| GPU Metric | Description |
| --- | --- |
| `omnistat_vendor_accel_energy_joules` | Cumulative accelerator energy (J) per device. Labels: `vendor`, `accel`. |
| `omnistat_vendor_accel_power_watts` | Instantaneous accelerator power (W) per device. Labels: `vendor`, `accel`. |

## 6. Kernel tracing

Starting with Omnistat 1.12.0, a tracing library (`libomnistat_trace.so`) loaded into the application records GPU kernel dispatches. Pre-built libraries are provided for ROCm 6.4.0 through 7.2.0; use the one matching the ROCm the application was built with.

```bash
#!/bin/bash
#SBATCH -A <project_id>
#SBATCH -J omnistat
#SBATCH -N 2
#SBATCH -t 01:00:00
#SBATCH -S 0

module load rocm/7.2.0               # must match the application's build

module use /sw/frontier/amdsw/modulefiles
module load omnistat-wrapper
export OMNISTAT_CONFIG=/path/to/omnistat.config   # enable_kernel_trace = True
${OMNISTAT_WRAPPER} usermode --start --interval 1.0

export ROCP_TOOL_LIBRARIES=${OMNISTAT_DIR}/build-trace-rocm-${CRAY_ROCM_VERSION}/libomnistat_trace.so

srun ./your_application

${OMNISTAT_WRAPPER} usermode --stopexporters
${OMNISTAT_WRAPPER} query --job ${SLURM_JOB_ID} --interval 1.0
${OMNISTAT_WRAPPER} usermode --stopserver
```

Base a custom config on the Frontier default, `omnistat/config/omnistat.ornl` in the Omnistat source tree: `enable_rocm_smi = True`, `enable_amd_smi = False`, `enable_rms = True`, `enable_vendor_counters = True`, and an explicit `rocm_path`. Only set `OMNISTAT_CONFIG` when you need to change the module's default.

## 7. Custom install (only if the module cannot be used)

Use this only when a specific Omnistat or VictoriaMetrics version is required. Download on the login node, not in a job.

```bash
export OMNI_PREFIX="/lustre/orion/<project_id>/proj-shared/omnistat"
mkdir -p "$OMNI_PREFIX" && cd "$OMNI_PREFIX"

export OMNISTAT_VERSION=v1.14.0
curl -OLJ "https://github.com/ROCm/omnistat/archive/refs/tags/${OMNISTAT_VERSION}.tar.gz"
tar xfz "omnistat-${OMNISTAT_VERSION#v}.tar.gz"

export VM_VERSION=v1.109.0
curl -OLJ "https://github.com/VictoriaMetrics/VictoriaMetrics/releases/download/${VM_VERSION}/victoria-metrics-linux-amd64-${VM_VERSION}.tar.gz"
tar xfz "victoria-metrics-linux-amd64-${VM_VERSION}.tar.gz"   # extracts victoria-metrics-prod

python3 -m venv "${OMNI_PREFIX}/omnistat-env"
"${OMNI_PREFIX}/omnistat-env/bin/pip" install \
  -r "omnistat-${OMNISTAT_VERSION#v}/requirements.txt" \
  -r "omnistat-${OMNISTAT_VERSION#v}/requirements-query.txt" \
  -e "omnistat-${OMNISTAT_VERSION#v}/"
```

Check the requirement file names against the chosen release, and the inode usage of the venv on Lustre. Copy `omnistat.ornl` from the same release and change only `rocm_path`, `victoria_binary` and `victoria_datadir` (literal absolute paths; INI does not expand variables). Keep ROCm SMI enabled and AMD SMI disabled unless you have checked that AMD SMI reports valid power on this driver; only one SMI collector may be active.

In the job, call the installed commands instead of the wrapper:

```bash
export OMNISTAT_CONFIG=${OMNI_PREFIX}/omnistat.config
export OMNISTAT_DIR=${OMNI_PREFIX}/omnistat-env/bin
${OMNISTAT_DIR}/omnistat-usermode --start --interval 1.0
# ...
${OMNISTAT_DIR}/omnistat-usermode --stopexporters
${OMNISTAT_DIR}/omnistat-query --job ${SLURM_JOB_ID} --interval 1.0
${OMNISTAT_DIR}/omnistat-usermode --stopserver
```

## 8. Data analysis

Keep the database, report, CSV exports, config/module list, sensor map and exit statuses together. After the job, copy the database to a workstation and use the Docker-based "Exploring results locally" workflow from the Omnistat user-mode documentation (Grafana over the collected VictoriaMetrics series). Do not run Docker on Frontier compute nodes, and do not open a database that a live server is still using.

## Troubleshooting

| Symptom | Cause | Fix |
| ------- | ----- | --- |
| `KeyError: 'omnistat.query'` at query time | Missing `[omnistat.query]` section | Use the module's default config, or add `prometheus_url` and `system_name` |
| No data from some nodes; proxy-related errors | `http_proxy`/`https_proxy` set in the job environment | Unset the proxy variables before `usermode --start` |
| Both SMI collectors configured | `enable_rocm_smi` and `enable_amd_smi` both `True` | Enable exactly one; the Frontier default is ROCm SMI |
| Every other GPU row shows zero or identical power | Package-level sensor reported on one or both GCDs | Map GCDs to packages and count one sensor per package |
| All-zero power | Unsupported or misconfigured telemetry | Compare raw sensors and vendor counters; report energy as missing |
| Accelerator indices don't match ROCm numbering | Vendor collector `accel` label uses its own index | Reconcile via device serial/PCI ID |
| Database lock errors | Database on `$HOME` or reused from a previous run | Use a new Orion or `/tmp` directory |
| Too few samples | Short run or long interval | Lengthen the measured work or reduce `--interval` |
| Report produced but application failed | Telemetry is independent of the application | Check the application exit code (`status.txt` with the wrapper script) |
| Kernel tracing library missing or mismatched | `ROCP_TOOL_LIBRARIES` built for a different ROCm | Match `build-trace-rocm-<version>` to `CRAY_ROCM_VERSION` |

## Testing the helper scripts

Offline tests need only Python and Bash (no GPU, Omnistat or Slurm):

```bash
python3 -B -m unittest discover -s skills/energy-profiling/tests -v
bash -n skills/energy-profiling/scripts/with_omnistat.sh
```

On Frontier, validate with a short one-node job first (correct application output, data for all 8 GCDs, four package energy channels, report and database present, clean teardown), then a two-node job to confirm all nodes are sampled.

## Resources

- [Omnistat Frontier instructions](https://rocm.github.io/omnistat/site.frontier.html) — module, `-S 0`, storage, vendor counters, kernel tracing.
- [OLCF Frontier user guide](https://docs.olcf.ornl.gov/systems/frontier_user_guide.html) — hardware, binding, Omnistat example with proxy and Darshan notes.
- [Omnistat metrics reference](https://rocm.github.io/omnistat/metrics.html) — metric scope, units and energy counters.
- [Omnistat user-mode installation](https://rocm.github.io/omnistat/user-mode/user-install.html), [job instrumentation](https://rocm.github.io/omnistat/user-mode/jobs.html) and [local analysis](https://rocm.github.io/omnistat/user-mode/analysis.html).
- [Omnistat (ROCm) GitHub](https://github.com/ROCm/omnistat) — source and default Frontier config (`omnistat/config/omnistat.ornl`).
- [VictoriaMetrics releases](https://github.com/VictoriaMetrics/VictoriaMetrics/releases) — pick a plain `vX.Y.Z` (community) build.
