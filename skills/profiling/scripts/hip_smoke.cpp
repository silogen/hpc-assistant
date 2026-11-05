#include <hip/hip_runtime.h>
#include <cstdio>
#include <cstdlib>
#include <vector>

#define HIP_OK(call) do { hipError_t e = (call); if (e != hipSuccess) { \
    std::fprintf(stderr, "%s: %s\n", #call, hipGetErrorString(e)); \
    std::exit(2); } } while (0)

__global__ void add(const float* a, const float* b, float* c, int n) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) c[i] = a[i] + b[i];
}

int main() {
    int count = 0;
    HIP_OK(hipGetDeviceCount(&count));
    if (count != 1) {
        std::fprintf(stderr, "Expected one Slurm-visible GCD, got %d\n", count);
        return 3;
    }
    HIP_OK(hipSetDevice(0));
    char bus[32] = {};
    HIP_OK(hipDeviceGetPCIBusId(bus, sizeof(bus), 0));
    constexpr int n = 1 << 20;
    const size_t bytes = n * sizeof(float);
    std::vector<float> a(n, 1.0f), b(n, 2.0f), c(n);
    float *da = nullptr, *db = nullptr, *dc = nullptr;
    HIP_OK(hipMalloc(&da, bytes));
    HIP_OK(hipMalloc(&db, bytes));
    HIP_OK(hipMalloc(&dc, bytes));
    HIP_OK(hipMemcpy(da, a.data(), bytes, hipMemcpyHostToDevice));
    HIP_OK(hipMemcpy(db, b.data(), bytes, hipMemcpyHostToDevice));
    for (int repeat = 0; repeat < 10; ++repeat) {
        hipLaunchKernelGGL(add, dim3((n + 255) / 256), dim3(256), 0, 0, da, db, dc, n);
        HIP_OK(hipGetLastError());
    }
    HIP_OK(hipDeviceSynchronize());
    HIP_OK(hipMemcpy(c.data(), dc, bytes, hipMemcpyDeviceToHost));
    for (float value : c) if (value != 3.0f) return 4;
    HIP_OK(hipFree(da));
    HIP_OK(hipFree(db));
    HIP_OK(hipFree(dc));
    std::printf("PASS visible_gcds=%d logical_device=0 pci_bus=%s elements=%d\n", count, bus, n);
    return 0;
}
