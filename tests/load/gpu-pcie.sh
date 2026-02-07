#!/usr/bin/env bash
# GPU PCIe load test — saturate PCIe with pinned memory + async CUDA streams
# Expected: ~150-400 Gbps on RTX 5090 (PCIe 5.0 x16)
#   Lower end when vLLM or other GPU workloads occupy VRAM
# Requires: vLLM docker image (has PyTorch with sm_120 support)
# Usage: bash tests/load/gpu-pcie.sh [duration_seconds] [docker_image]
set -euo pipefail

DURATION=${1:-15}
IMAGE=${2:-vllm/vllm-openai:latest}

echo "GPU PCIe: ${DURATION}s of pinned-memory async transfers across both GPUs"
docker run --rm --gpus all --ipc=host --entrypoint python3 "$IMAGE" -c "
import torch, time

n_gpus = torch.cuda.device_count()
print(f'GPUs: {n_gpus}')

# 25MB buffers x 8 — small enough to coexist with vLLM
bufs = [torch.randn(2500, 2500).pin_memory() for _ in range(8)]

streams = []
for gpu in range(n_gpus):
    streams.append(torch.cuda.Stream(device=gpu))

print('Saturating PCIe...')
end = time.time() + ${DURATION}
count = 0
while time.time() < end:
    for gpu in range(n_gpus):
        with torch.cuda.stream(streams[gpu]):
            for buf in bufs:
                d = buf.cuda(gpu, non_blocking=True)
                d.cpu()
    count += 1

for gpu in range(n_gpus):
    torch.cuda.synchronize(gpu)
print(f'Done ({count} iterations)')
"
echo "Done"
