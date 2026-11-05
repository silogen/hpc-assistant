#!/bin/bash
#SBATCH --job-name=frontier-gpu-smoke
#SBATCH --partition=batch
#SBATCH --nodes=1
#SBATCH --ntasks=8
#SBATCH --ntasks-per-node=8
#SBATCH --cpus-per-task=7
#SBATCH --threads-per-core=1
#SBATCH --time=00:10:00
#SBATCH --output=%x-%j.out
#SBATCH --error=%x-%j.err
# Submit with: sbatch --account="$PROJECT" gpu-smoke.sbatch
# Requires PYTHON_ENV (an existing, already-validated ROCm PyTorch conda/venv
# environment) exported in the calling shell before sbatch.
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:?}"

: "${PYTHON_ENV:?Set PYTHON_ENV to an existing ROCm PyTorch environment before submitting}"

module load miniforge3
conda activate "$PYTHON_ENV"
command -v python3
python3 -c "import torch; print(torch.__version__, torch.version.hip)"

export SMOKE_OUT_DIR="$SLURM_SUBMIT_DIR"
export OMP_NUM_THREADS=7

srun -N1 -n8 -c7 --gpus-per-task=1 --gpu-bind=closest \
  python3 "$SLURM_SUBMIT_DIR/gpu_smoke.py"

# Verification (run after the job completes, not inside it):
#   ls gpu_smoke_rank*.json | wc -l   # expect 8
#   python3 -c "import json,glob;
#   rs=[json.load(open(f)) for f in glob.glob('gpu_smoke_rank*.json')];
#   assert len(rs)==8 and all(r['ok'] and r['device_count']==1 for r in rs)"
