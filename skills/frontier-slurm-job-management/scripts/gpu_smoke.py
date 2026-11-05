#!/usr/bin/env python3
"""One-node, one-GCD-per-rank GPU smoke test for Frontier (ROCm PyTorch).

Launched as one srun task per rank with --gpus-per-task=1 --gpu-bind=closest.
Under that binding each rank sees exactly one GCD, always at local index 0 --
this script asserts that rather than assuming SLURM_LOCALID selects the device.

Writes a per-rank JSON result file so a caller can verify that all expected
ranks completed, each with a finite result and exactly one visible device.
Not a training, checkpoint, or multi-node network test.
"""
import json
import os
import sys


def main() -> int:
    rank = os.environ.get("SLURM_PROCID", "0")
    out_dir = os.environ.get("SMOKE_OUT_DIR", ".")
    out_path = os.path.join(out_dir, f"gpu_smoke_rank{rank}.json")

    result = {
        "rank": rank,
        "hostname": os.uname().nodename,
        "ok": False,
    }
    try:
        import torch

        device_count = torch.cuda.device_count()
        if device_count != 1:
            raise RuntimeError(
                f"expected exactly 1 visible GCD under --gpus-per-task=1, got {device_count}"
            )
        device = torch.device("cuda:0")
        a = torch.rand((4096, 4096), device=device)
        b = torch.rand((4096, 4096), device=device)
        c = (a @ b).sum().item()
        if not (c == c):  # NaN check without importing math
            raise RuntimeError("matmul result is NaN")

        result.update(
            {
                "ok": True,
                "device_count": device_count,
                "hip_version": getattr(torch.version, "hip", None),
                "torch_version": torch.__version__,
                "result_finite": True,
                "result_sample": c,
            }
        )
    except Exception as exc:  # record the error in the JSON result
        result["error"] = repr(exc)

    with open(out_path, "w") as fh:
        json.dump(result, fh, indent=2)

    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
