#!/usr/bin/env bash
# SSD Read load test — 8 parallel read streams
# Expected: ~118+ Gbps on Samsung 9100 PRO NVMe
# Usage: bash tests/load/ssd-read.sh [duration_seconds]
set -euo pipefail

DURATION=${1:-15}

echo "Preparing 10GB test file..."
dd if=/dev/zero of=/tmp/ssd_load_read bs=4M count=2500 oflag=direct 2>/dev/null

echo "SSD READ: 8 parallel streams for ${DURATION}s"
for i in $(seq 1 8); do
    ( end=$((SECONDS + DURATION)); while [ $SECONDS -lt $end ]; do dd if=/tmp/ssd_load_read of=/dev/null bs=4M iflag=direct 2>/dev/null; done ) &
done
echo "Running... watch SSD Read on Display 1"
wait
rm -f /tmp/ssd_load_read
echo "Done"
