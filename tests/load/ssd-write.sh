#!/usr/bin/env bash
# SSD Write load test — 8 parallel dd streams from /dev/zero
# Expected: ~75+ Gbps on Samsung 9100 PRO NVMe
# Usage: bash tests/load/ssd-write.sh [duration_seconds]
set -euo pipefail

DURATION=${1:-15}

echo "SSD WRITE: 8 parallel streams for ${DURATION}s"
for i in $(seq 1 8); do
    ( timeout "$DURATION" dd if=/dev/zero of=/tmp/ssd_load_$i bs=4M oflag=direct 2>/dev/null || true ) &
done
echo "Running... watch SSD Write on Display 1"
wait
rm -f /tmp/ssd_load_*
echo "Done"
