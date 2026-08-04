#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
#
# SPDX-License-Identifier: MIT

"""
Unoptimized Triton Matrix Multiplication Kernel -- Target for GEAK Optimization

This is a deliberately naive GEMM implementation with many optimization
opportunities for the GEAK agent to discover:
  - Fixed block sizes (no autotuning)
  - No software pipelining (num_stages=1)
  - No warp tuning
  - No shared memory / L2 cache hints
  - Basic tiling without swizzle
  - No split-K or grouped GEMM strategies

Usage:
    geak --kernel-url matmul_kernel.py --gpu-ids 0 --yolo -m claude-sonnet-4-6 --model-class anthropic_model
"""

import torch
import triton
import triton.language as tl


@triton.jit
def matmul_kernel(
    a_ptr, b_ptr, c_ptr,
    M, N, K,
    stride_am, stride_ak,
    stride_bk, stride_bn,
    stride_cm, stride_cn,
    BLOCK_M: tl.constexpr,
    BLOCK_N: tl.constexpr,
    BLOCK_K: tl.constexpr,
):
    """
    Naive blocked matrix multiplication C = A @ B.

    Each program instance computes a BLOCK_M x BLOCK_N tile of C by
    iterating over K in steps of BLOCK_K.
    """
    pid = tl.program_id(0)
    num_n_blocks = tl.cdiv(N, BLOCK_N)
    pid_m = pid // num_n_blocks
    pid_n = pid % num_n_blocks

    offs_m = pid_m * BLOCK_M + tl.arange(0, BLOCK_M)
    offs_n = pid_n * BLOCK_N + tl.arange(0, BLOCK_N)

    acc = tl.zeros((BLOCK_M, BLOCK_N), dtype=tl.float32)

    for k_start in range(0, K, BLOCK_K):
        offs_k = k_start + tl.arange(0, BLOCK_K)

        a_ptrs = a_ptr + offs_m[:, None] * stride_am + offs_k[None, :] * stride_ak
        b_ptrs = b_ptr + offs_k[:, None] * stride_bk + offs_n[None, :] * stride_bn

        mask_a = (offs_m[:, None] < M) & (offs_k[None, :] < K)
        mask_b = (offs_k[:, None] < K) & (offs_n[None, :] < N)

        a_tile = tl.load(a_ptrs, mask=mask_a, other=0.0)
        b_tile = tl.load(b_ptrs, mask=mask_b, other=0.0)

        acc += tl.dot(a_tile, b_tile)

    c_tile = acc.to(tl.float16)
    c_ptrs = c_ptr + offs_m[:, None] * stride_cm + offs_n[None, :] * stride_cn
    mask_c = (offs_m[:, None] < M) & (offs_n[None, :] < N)
    tl.store(c_ptrs, c_tile, mask=mask_c)


def triton_matmul(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    """
    Matrix multiply a (M, K) @ b (K, N) -> c (M, N) using the Triton kernel.
    """
    assert a.is_cuda and b.is_cuda
    assert a.shape[1] == b.shape[0], f"Inner dims must match: {a.shape[1]} vs {b.shape[0]}"
    assert a.dtype == torch.float16 and b.dtype == torch.float16

    M, K = a.shape
    K2, N = b.shape

    c = torch.empty((M, N), device=a.device, dtype=torch.float16)

    BLOCK_M, BLOCK_N, BLOCK_K = 64, 64, 32

    grid = (triton.cdiv(M, BLOCK_M) * triton.cdiv(N, BLOCK_N),)

    matmul_kernel[grid](
        a, b, c,
        M, N, K,
        a.stride(0), a.stride(1),
        b.stride(0), b.stride(1),
        c.stride(0), c.stride(1),
        BLOCK_M=BLOCK_M,
        BLOCK_N=BLOCK_N,
        BLOCK_K=BLOCK_K,
    )
    return c


def torch_matmul(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    """Reference PyTorch implementation for correctness checking."""
    return torch.mm(a, b)


triton_op = triton_matmul
torch_op = torch_matmul


if __name__ == "__main__":
    M, N, K = 1024, 1024, 1024
    a = torch.randn(M, K, device="cuda", dtype=torch.float16)
    b = torch.randn(K, N, device="cuda", dtype=torch.float16)

    out = triton_matmul(a, b)
    torch.cuda.synchronize()

    ref = torch_matmul(a, b)
    assert torch.allclose(out, ref, rtol=1e-2, atol=1e-2), "Correctness check failed!"
    print(f"matmul_kernel: {M}x{K} @ {K}x{N} = {M}x{N}, output[0,0]={out[0, 0].item():.4f}")
