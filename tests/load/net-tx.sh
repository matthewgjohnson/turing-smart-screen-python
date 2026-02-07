#!/usr/bin/env bash
# Network TX load test — push data to a remote host via SSH
# Expected: ~0.3-0.7 Gbps on 1 Gbps link (SSH overhead)
# Usage: bash tests/load/net-tx.sh <dest_host>
# Example: bash tests/load/net-tx.sh mgj@192.168.128.1
set -euo pipefail

HOST=${1:?Usage: $0 <dest_host> (e.g. mgj@192.168.128.1)}
SIZE=${2:-2000}  # MB, default 2000

echo "NET TX: pushing ${SIZE}MB to $HOST"
dd if=/dev/zero bs=1M count=$SIZE 2>/dev/null | ssh "$HOST" "cat > /dev/null"
echo "Done"
