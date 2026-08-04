# LUMI Slurm Job Examples

## Minimal batch script

```bash
#!/bin/bash
#SBATCH --job-name=exampleJob
#SBATCH --account=project_<<id>>
#SBATCH --time=02:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --partition=small

module load MyApp/1.2.3
srun myapp -i input -o output
```

## LUMI-G GPU (MPI, one rank per GPU)

Full-node: 2 nodes, 8 GPUs per node, GPU and CPU binding.

```bash
#!/bin/bash -l
#SBATCH --job-name=examplejob
#SBATCH --output=examplejob.o%j
#SBATCH --error=examplejob.e%j
#SBATCH --partition=standard-g
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=8
#SBATCH --gpus-per-node=8
#SBATCH --time=1-12:00:00
#SBATCH --account=project_<<id>>

cat << 'EOF' > select_gpu
#!/bin/bash
export ROCR_VISIBLE_DEVICES=$SLURM_LOCALID
exec "$@"
EOF
chmod +x ./select_gpu

CPU_BIND="map_cpu:49,57,17,25,1,9,33,41"
export MPICH_GPU_SUPPORT_ENABLED=1

srun --cpu-bind=${CPU_BIND} ./select_gpu <executable> <args>
rm -f ./select_gpu
```

## Small GPU job (sub-node)

```bash
#!/bin/bash
#SBATCH --job-name=small-gpu-job
#SBATCH --account=project_<<id>>
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=02:00:00
#SBATCH --output=job_%j.out
#SBATCH --error=job_%j.err

srun my_gpu_app
```

## Container job with storage binding

```bash
srun --partition=small --account=project_xxx \
  singularity exec -B /scratch/project_xxx:/scratch/project_xxx \
  -B /project/project_xxx:/project/project_xxx \
  myimage.sif mycommand
```

## Python jobs

### Cray Python serial job

```bash
#!/bin/bash -l
#SBATCH --job-name=python-serial
#SBATCH --output=python-serial.o%j
#SBATCH --error=python-serial.e%j
#SBATCH --partition=small
#SBATCH --ntasks=1
#SBATCH --time=00:15:00
#SBATCH --account=project_<<id>>

module load cray-python
# source your-venv/bin/activate  # optional
python3 your_application.py
```

### Container Python MPI job

Use `srun` instead of mpirun/mpiexec. Container path may vary; check LUMI docs.

```bash
#!/bin/bash -l
#SBATCH --job-name=python-mpi
#SBATCH --output=python-mpi.o%j
#SBATCH --error=python-mpi.e%j
#SBATCH --partition=standard
#SBATCH --nodes=2
#SBATCH --ntasks=256
#SBATCH --mem=224G
#SBATCH --time=1-12:00:00
#SBATCH --account=project_<<id>>

export CONTAINER=/appl/local/containers/sif-images/lumi-mpi4py-rocm-6.2.0-python-3.12-mpi4py-3.1.6.sif
srun singularity exec $CONTAINER python3 your_application.py
```

### Python array job

Array of jobs; each task processes one file (e.g. `data*.json`). Match `--array=1-N` to number of files.

```bash
#!/bin/bash -l
#SBATCH --job-name=python-array
#SBATCH --partition=small
#SBATCH --ntasks=1
#SBATCH --time=00:15:00
#SBATCH --account=project_<<id>>
#SBATCH --array=1-10

module load cray-python

DATA=$(find "$PWD"/data -name "data*.json" | awk -v var=$SLURM_ARRAY_TASK_ID 'NR==var {print $1}')
FOLDER=$(dirname "$DATA")

srun --output="$FOLDER"/slurm-%j_$SLURM_ARRAY_TASK_ID.out \
     --error="$FOLDER"/slurm-%j_$SLURM_ARRAY_TASK_ID.err \
     python3 your_application.py $DATA
```

### PyTorch GPU job (LUMI AI Factory container)

One GPU, single node, using the LUMI AI Factory containerised PyTorch stack. Replace `myprog.py` and `<options>` with your script and arguments. The SIF path may change with new releases; list `/appl/local/laifs/containers/` for available images.

```bash
#!/bin/bash
#SBATCH --account=project_<<id>>
#SBATCH --partition=small-g
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=7
#SBATCH --gpus-per-node=1
#SBATCH --mem=60G
#SBATCH --time=1:00:00

module purge
module use /appl/local/laifs/modules
module load lumi-aif-singularity-bindings
export SIF=/appl/local/laifs/containers/lumi-multitorch-u24r64f21m43t29-20260124_092648/lumi-multitorch-full-u24r64f21m43t29-20260124_092648.sif
srun singularity run $SIF python3 myprog.py <options>
```

*Source: [LUMI AI Factory – AI Software Environment](https://docs.lumi-supercomputer.eu/laif/software/ai-environment/).*

## Key SBATCH parameters

| Parameter | Description | Example |
| --------- | ----------- | ------- |
| `--account` | Project for billing (required) | `project_xxx` |
| `--job-name` | Job name (e.g. in squeue) | `my-job` |
| `--partition` | Partition | `small`, `small-g`, `standard-g` |
| `--time` | Max run time | `02:00:00`, `1-12:00:00` |
| `--nodes` | Number of nodes | `1`, `2` |
| `--ntasks` | Total tasks (e.g. MPI ranks) | `16` |
| `--ntasks-per-node` | Tasks per node | `8` |
| `--cpus-per-task` | CPUs per task | `8` |
| `--gpus` | Total GPUs for job | `1`, `8` |
| `--gpus-per-node` | GPUs per node | `8` |
| `--mem` | Memory per node | `32G` |
| `--output` | stdout file | `logs/%x_%j.out` |
| `--error` | stderr file | `logs/%x_%j.err` |
| `--open-mode=append` | Append output after requeue | — |

**Log placeholders:** `%x` job name, `%j` job ID, `%s` step ID, `%t` task ID, `%N` node name.
