# Capping mechanisms on Frontier

## 1. Documentation

The [Frontier User Guide](https://docs.olcf.ornl.gov/systems/frontier_user_guide.html#olcf-custom-slurm-plugins) (section *OLCF Custom Slurm Plugins*; downloaded to `.hpc-assistant/docs/frontier/` by `hpc-assistant-init`) lists three OLCF options for `sbatch`/`#SBATCH`:

| Option | Example | Guide description |
| --- | --- | --- |
| `--gpu-srange` | `#SBATCH --gpu-srange=500-1600` | Sets the GPU sclk (system clock) range in MHz (default: 500-1700) |
| `--gpu-power-cap` | `#SBATCH --gpu-power-cap=500` | Sets the GPU power cap in Watts (default: 560) |
| `--gpu-counters` | `#SBATCH --gpu-counters=0` | When 1 (default if > 500 nodes), a rocprofiler-based daemon samples GPU hardware counters on a subset of nodes; data available on request from help@olcf.ornl.gov |

`sbatch --help`, `salloc --help` and `srun --help` all list the options (Slurm 25.11 on Frontier, checked 2026-11-05). Only the job-level value counts (see below).

### Setting them in the job header

```bash
#!/bin/bash
#SBATCH -A abc123
#SBATCH -J app-300W
#SBATCH -N 1
#SBATCH -t 00:30:00
#SBATCH --gpu-power-cap=300
#SBATCH --gpu-srange=500-1700
#SBATCH --gpu-counters=0

srun -N1 -n8 -c7 --gpus-per-task=1 --gpu-bind=closest ./app
```

- One job per power cap and per frequency range. A sweep over 4 caps means 4 jobs (4 scripts, or one script submitted 4 times with `sbatch --gpu-power-cap=W`).
- Precedence: `sbatch` command line > `#SBATCH` header > plugin default.
- `#SBATCH` lines must come before the first command. `##SBATCH` is a comment.
- `salloc --gpu-power-cap=300 ...` works the same way for interactive allocations.
- Invalid values are rejected at submission. Checking with `sbatch --test-only` submits nothing and costs nothing (verified 2026-11-05):

```text
sbatch: error: gpu-power-cap: requested GPU power cap value '50' is below minumum '100'
sbatch: error: Invalid --gpu-power-cap argument: 50
sbatch: error: gpu-srange: requested GPU min sclk value '1300' is greater than requested max '1300'
sbatch: error: Invalid --gpu-srange argument: 1300-1300
```

```bash
sbatch --test-only -A "$PROJECT" job.sbatch     # validates the #SBATCH --gpu-* header
```

### Where the accepted values are recorded

At submission the plugins export `GPU_SRANGE`, `GPU_SRANGE_SRC`, `GPU_POWER_CAP`, `GPU_POWER_CAP_SRC`, `GPU_COUNTERS` and `GPU_COUNTERS_SRC` to the job control environment. The job_submit plugin stores them in the job's **AdminComment**, which users can read:

```bash
scontrol show job "$SLURM_JOB_ID" | grep -o 'AdminComment=[^ ]*'
sacct -j 1234567 -X -o JobID,JobName,AdminComment%160
sacct -u "$USER" -S 2026-10-01 -X -o JobID,JobName%30,AdminComment%160   # audit a whole sweep
```

Example: `AdminComment=GPU_SRANGE=500-1700,GPU_SRANGE_SRC=default,GPU_POWER_CAP=200,GPU_POWER_CAP_SRC=user,GPU_COUNTERS=0,GPU_COUNTERS_SRC=default`. `_SRC=user` means the option was set in the header or on the command line. These are the *requested* values; the sysfs read-back below shows what the prolog actually applied on each node. The variables are not exported into the job's own environment.

## 2. What the plugins actually do

The options are Lua SPANK plugins on the login and compute nodes:

```text
/etc/slurm/plugstack.conf.d/lua.conf   ->  required lua.so /etc/slurm/lua.d/*.lua
/etc/slurm/lua.d/gpu-srange.lua
/etc/slurm/lua.d/gpu-power-cap.lua
/etc/slurm/lua.d/gpu-counter-opt.lua
```

Both cap plugins say: *"Only settable at the job level, not for each srun because GPUs can be shared among steps."* Behavior as read on 2026-11-05:

### `--gpu-srange` (gpu-srange.lua)

| Item | Value |
| --- | --- |
| Constants | `range_min = 500`, `range_default = 1700`, `range_max = 1700` |
| Parsing | split on `-`; two numbers mean MIN-MAX, one number means MAX with MIN = 500 |
| Rejected (job submission fails) | non-numeric values, MIN ≥ MAX, MIN < 500 |
| Accepted with no check | MAX > 1700 (comment: "Allow margin testing to go above max") |
| Applied | in `slurm_spank_job_prolog`: `/opt/rocm-default/bin/rocm-smi --setsrange MIN MAX --autorespond y` (all GPUs on each node) |
| Reset | in `slurm_spank_job_epilog`: `--setsrange 500 1700` |
| Recorded | `GPU_SRANGE=MIN-MAX`, `GPU_SRANGE_SRC=user|default` in the job's AdminComment |

Consequences:

- `--gpu-srange=1300` is the same as `500-1300`.
- A fixed clock (`1300-1300`) is impossible. Use a 1 MHz band (`1299-1300`), which keeps the GPU at about 1300 MHz even when idle.
- The DPM governor stays active inside the range. MIN is a floor that applies even when the GPU is idle, and MAX is a ceiling.
- Only sclk changes. The HBM clock (mclk, 1600 MHz on MI250X) does not.

### `--gpu-power-cap` (gpu-power-cap.lua)

| Item | Value |
| --- | --- |
| Constants | `power_min = 100`, `power_default = 560`, `power_max = 600` |
| Rejected | non-numeric, < 100, > 600 |
| Applied | in the job prolog, writes `W * 1000000` (microwatts) to `/sys/class/drm/cardN/device/hwmon/hwmonN/power1_cap` for **N = 0, 2, 4, 6** |
| Reset | in the epilog, back to 560 W on the same four cards |
| Recorded | `GPU_POWER_CAP`, `GPU_POWER_CAP_SRC` in the job's AdminComment |

Consequences:

- One cap per MI250X package (OAM). Its two GCDs share it, so the node GPU budget is 4 × W.
- Writing to sysfs fails silently if the path is different (the plugin ignores a failed `io.open`). This is why `verify_caps.py` reads the value back.
- Values between 561 and 600 W are accepted, but whether the firmware honors them above the board limit is not documented. Treat 560 W as the effective maximum.

### `--gpu-counters` (gpu-counter-opt.lua + prolog/epilog `rocm-counter-*.py`)

This is not a cap. When enabled, a rocprofiler counter daemon runs during the job and writes to `/lustre/orion/sysinfo`. It adds a small background load and occupies the GPU profiling interface, so set `--gpu-counters=0` consistently when measuring energy or using rocprofv3.

### Job-level scope

Because the caps are set in the job prolog, they:

- apply to **all nodes** and all GPUs of the allocation, whatever GPUs each step uses
- cannot change between steps (putting different configurations in one job is impossible, so use one job per configuration)
- persist through the whole allocation, including idle time between steps
- are reset by the epilog, so the next job on the node does not inherit them

## 3. Runtime max frequency: `set_gpu_max_sclk`

`/usr/bin/set_gpu_max_sclk` is a utility installed on Frontier **compute nodes** (not on login nodes). It sets the GPU sclk **maximum** at runtime, so one job can measure several frequencies. Run it from the batch script, once per node, before each application step:

```bash
#SBATCH --gpu-power-cap=560        # power cap stays fixed for the whole job
for maxfreq in 1700 1500 1300 1100 900 700 501; do
    srun -N "$SLURM_JOB_NUM_NODES" --ntasks-per-node=1 --exclusive /usr/bin/set_gpu_max_sclk "$maxfreq"
    srun -N "$SLURM_JOB_NUM_NODES" -n $((8 * SLURM_JOB_NUM_NODES)) -c7 --gpus-per-task=1 --gpu-bind=closest ./app > "out-${maxfreq}.txt"
done
srun -N "$SLURM_JOB_NUM_NODES" --ntasks-per-node=1 --exclusive /usr/bin/set_gpu_max_sclk 1700
```

What is known (from job scripts used on Frontier):

| Item | Behavior |
| --- | --- |
| Argument | One integer: the max sclk in MHz |
| Scope | All GPUs of the node it runs on; hence one task per node with `-N $SLURM_JOB_NUM_NODES --ntasks-per-node=1` |
| Lowest value used | 501 MHz (the min stays at 500, and the range needs min < max) |
| Persistence | Until changed again; the `--gpu-srange` epilog resets the range to 500-1700 at job end. Restore 1700 yourself if later steps must run at full clock |
| Mechanism | Writes the amdgpu OverDrive sclk range (`pp_od_clk_voltage`), so `verify_caps.py --expect-srange 500-<MHz>` reads it back |
| Min frequency | Not changed; use `#SBATCH --gpu-srange=MIN-MAX` for the floor |

Not yet confirmed: whether it accepts a value above the header's `--gpu-srange` max, and its exact error behavior. Run `/usr/bin/set_gpu_max_sclk` with no arguments inside a job (`srun -N1 -n1 /usr/bin/set_gpu_max_sclk`) to see its usage, then record the answers here.

Runtime vs. header frequency control:

| | `set_gpu_max_sclk` (runtime) | `#SBATCH --gpu-srange` (header) |
| --- | --- | --- |
| Frequencies per job | many | one |
| Sets min | no | yes |
| Same nodes for every point | yes (less node-to-node noise) | no (unless `--nodelist`) |
| Carry-over between points | thermal state and caches of the previous point; use an idle gap and shuffle the order | none |
| Queue cost | one job wait for the whole sweep | one wait per point |

Power capping has no equivalent runtime tool for users: use one job per power cap with `#SBATCH --gpu-power-cap`. Some scripts write `hwmon/power1_cap` directly when the file is writable on a node. That bypasses the Slurm plugin (nothing is recorded in AdminComment, and the epilog resets the value anyway), so it is not used by this skill.

## 4. Reading the state back (no root needed)

Sysfs files read by [verify_caps.py](../scripts/verify_caps.py) and [gpu_sampler.py](../scripts/gpu_sampler.py):

| Path under `/sys/class/drm/cardN/device/` | Content |
| --- | --- |
| `pp_od_clk_voltage` | `OD_SCLK:` section, `0: <min>Mhz` and `1: <max>Mhz`: the active sclk range. `OD_RANGE:` gives the hardware limits |
| `pp_dpm_sclk` | Available DPM levels; `*` marks the current one |
| `hwmon/hwmon*/power1_cap` | Power cap in µW (cards 0, 2, 4, 6 on Frontier) |
| `hwmon/hwmon*/power1_average` or `power1_input` | Package power in µW (one GCD per package reports it) |
| `hwmon/hwmon*/freq1_input`, `freq2_input` | Current sclk and mclk in Hz |
| `hwmon/hwmon*/temp{1,2,3}_input` | Edge, junction, and memory temperature in m°C |
| `power_dpm_force_performance_level` | DPM mode (`auto`, `manual`, ...) |

`/sys/class/drm/cardN` numbering does not have to match HIP device numbering or `ROCR_VISIBLE_DEVICES`. Use it only for node-level checks.

Read-only commands (load a `rocm` module, or use `/opt/rocm-<ver>/bin`):

```bash
rocm-smi --showpower --showmaxpower --showclocks          # power, power cap, current clocks
rocm-smi --showsclkrange --showmclkrange                  # sclk/mclk OD ranges
rocm-smi -s                                               # supported clock levels
rocm-smi --showenergycounter                              # accumulated energy
rocm-smi --showpower --showclocks --json                  # machine-readable
amd-smi static --limit                                    # power and thermal limits
amd-smi metric --power --clock --energy --json            # power, clocks, energy
amd-smi metric --power --clock -w 1 -i 10                 # 10 samples at 1 s
```

`rocm-smi --setsrange`, `--setpoweroverdrive`, `--setperflevel`, and `amd-smi set` need root, and users get permission errors. Use the Slurm header options, and `set_gpu_max_sclk` for the max frequency.

Run these commands inside a job (for example `srun --overlap -N1 -n1 rocm-smi ...`). The login nodes have no GPUs.

## 5. HPE Cray pm_counters (energy)

Compute nodes expose `/sys/cray/pm_counters/`, which users can read. Each file has the form `<value> <unit> <timestamp> us`, for example `123456 J 1700000000123456 us`. The values are updated at about 10 Hz.

| File | Meaning |
| --- | --- |
| `energy`, `power` | Whole node (cumulative J, instantaneous W) |
| `cpu_energy`, `cpu_power` | CPU socket |
| `memory_energy`, `memory_power` | DDR memory |
| `accel0..3_energy`, `accel0..3_power` | One per MI250X package |
| `power_cap`, `accelN_power_cap` (if present) | Firmware caps, which may read 0 when unset |
| `freshness`, `generation`, `startup`, `version` | Metadata; `generation` changes when counters reset |

Energy is the difference between a counter at the end and at the start of the application window (`analyze_caps.py` interpolates at the exact start and end times). A decreasing counter means a reset, and the run is excluded. The `accelN` index may not match ROCm card order. That is fine for totals; to attribute energy to a GPU, match them through serial or PCI IDs.

## 6. CPU frequency (optional, separate knob)

Slurm on Frontier lists `CpuFreqGovernors = OnDemand,Performance,UserSpace` (`scontrol show config`). `--cpu-freq` is a per-**step** option for `srun`/`sbatch`:

```bash
srun --cpu-freq=Performance ...            # governor only
srun --cpu-freq=1500000-2000000:UserSpace ...   # min-max in kHz with governor
srun --cpu-freq=1800000 ...               # a single frequency in kHz
```

It was not checked on compute nodes whether the request takes effect. Check it inside the step:

```bash
srun -N1 -n1 --cpu-freq=1500000 bash -c 'cat /sys/devices/system/cpu/cpu1/cpufreq/scaling_{governor,min_freq,max_freq,cur_freq}'
```

If the values do not change, report that CPU frequency control is unavailable rather than assuming it worked. Keep CPU frequency fixed (default) while sweeping GPU caps, unless the CPU is part of the study.

## 7. Hardware reference (MI250X on Frontier)

| Item | Value |
| --- | --- |
| Packages per node | 4 MI250X OAMs, 2 GCDs each (8 logical GPUs) |
| Engine clock (sclk) | up to 1700 MHz; plugin floor 500 MHz |
| HBM2E clock (mclk) | 1600 MHz; not changed by `--gpu-srange` |
| Package power | 560 W default cap on Frontier (per OAM) |
| Power cap range | 100–600 W (plugin limits) |
| L2 cache | 8 MB per GCD |
