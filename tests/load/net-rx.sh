#!/usr/bin/env bash
# Network RX load test — pull data from a remote host via SSH
# Expected: ~0.3-0.7 Gbps on 1 Gbps link (SSH overhead)
# Usage: bash tests/load/net-rx.sh <source_host>
# Example: bash tests/load/net-rx.sh mgj@192.168.128.1
set -euo pipefail

HOST=${1:?Usage: $0 <source_host> (e.g. mgj@192.168.128.1)}
SIZE=${2:-2000}  # MB, default 2000

echo "NET RX: pulling ${SIZE}MB from $HOST"
ssh "$HOST" "dd if=/dev/zero bs=1M count=$SIZE 2>/dev/null" > /dev/null
echo "Done"
