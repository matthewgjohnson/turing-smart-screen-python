#!/usr/bin/env bash
# GPU PCIe load test — transfer large tensors between CPU and GPU via PyTorch
# Expected: ~20-30 Gbps on RTX 5090 (PCIe 5.0 x16, Python overhead)
# Requires: vLLM docker image (has PyTorch with sm_120 support)
# Usage: bash tests/load/gpu-pcie.sh [iterations]
set -euo pipefail

ITERS=${1:-200}
IMAGE=${2:-vllm/vllm-openai:latest}

echo "GPU PCIe: $ITERS iterations of 400MB tensor transfers (CPU<->GPU x2)"
docker run --rm --gpus all --ipc=host --entrypoint python3 "$IMAGE" -c "
import torch
print(f'GPUs: {torch.cuda.device_count()}')
for i in range(${ITERS}):
    x = torch.randn(10000, 10000)
    x.cuda(0).cpu()
    x.cuda(1).cpu()
print('Done')
"
echo "Done"
