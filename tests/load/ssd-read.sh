#!/usr/bin/env bash
# SSD Read load test — write a temp file then 8 parallel reads
# Expected: ~118+ Gbps on Samsung 9100 PRO NVMe
# Usage: bash tests/load/ssd-read.sh [size_MB]
set -euo pipefail

SIZE=${1:-10000}  # MB, default 10GB

echo "Preparing ${SIZE}MB test file..."
dd if=/dev/zero of=/tmp/ssd_load_read bs=4M count=$((SIZE / 4)) oflag=direct 2>/dev/null

echo "SSD READ: 8 parallel streams"
for i in $(seq 1 8); do
    dd if=/tmp/ssd_load_read of=/dev/null bs=4M iflag=direct 2>/dev/null &
done
echo "Running... watch SSD Read on Display 1"
wait
rm -f /tmp/ssd_load_read
echo "Done"
