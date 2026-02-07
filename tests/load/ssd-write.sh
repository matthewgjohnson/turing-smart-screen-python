#!/usr/bin/env bash
# SSD Write load test — 8 parallel dd streams from /dev/zero
# Expected: ~75+ Gbps on Samsung 9100 PRO NVMe
# Usage: bash tests/load/ssd-write.sh [count_MB_per_stream]
set -euo pipefail

COUNT=${1:-5000}  # MB per stream, default 5000 (5GB x 8 = 40GB total)

echo "SSD WRITE: 8 parallel streams, ${COUNT}MB each"
for i in $(seq 1 8); do
    dd if=/dev/zero of=/tmp/ssd_load_$i bs=4M count=$((COUNT / 4)) oflag=direct 2>/dev/null &
done
echo "Running... watch SSD Write on Display 1"
wait
rm -f /tmp/ssd_load_*
echo "Done"
