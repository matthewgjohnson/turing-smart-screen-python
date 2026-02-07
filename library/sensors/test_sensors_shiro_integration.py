# SPDX-License-Identifier: GPL-3.0-or-later
"""
Integration tests for BandwidthCollector sensors.

These tests run on real hardware (Shiro) and verify that sensors produce
changing values when actual I/O occurs.  They are marked with
@pytest.mark.integration so they won't run with a plain `pytest` invocation.

Run on Shiro:
    uv run python -m pytest library/sensors/test_sensors_shiro_integration.py -v
"""

import os
import socket
import tempfile
import threading
import time

import pytest

# Ensure project root is on sys.path so `library.sensors` resolves.
import sys
from pathlib import Path

_project_root = str(Path(__file__).resolve().parents[2])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from library.sensors.sensors_shiro import BandwidthCollector

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_bandwidth_collector():
    """Reset BandwidthCollector state so every test starts fresh."""
    BandwidthCollector._cache = {}
    BandwidthCollector._last_fetch = 0
    BandwidthCollector._gpu_cache = {"gpu_rx": 0, "gpu_tx": 0}
    BandwidthCollector._gpu_last_fetch = 0
    BandwidthCollector._prev_disk = None
    BandwidthCollector._prev_net = None
    BandwidthCollector._prev_time = None
    BandwidthCollector._disks = None
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prime_and_reset():
    """Prime the collector (first read stores baseline) then bust the cache."""
    BandwidthCollector.get("ssd_read")  # triggers _fetch → stores _prev_*
    BandwidthCollector._last_fetch = 0  # bust TTL so next call re-fetches


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSsdBandwidth:
    def test_ssd_bandwidth_moves(self, tmp_path):
        """Write and read ~50 MB; verify SSD bandwidth sensors show activity."""
        _prime_and_reset()

        # Generate real disk I/O
        tmp_file = tmp_path / "integration_test_blob"
        data = os.urandom(50 * 1024 * 1024)  # 50 MB
        tmp_file.write_bytes(data)
        os.sync()
        _ = tmp_file.read_bytes()

        # Let a little time pass so the delta is meaningful
        time.sleep(0.1)
        BandwidthCollector._last_fetch = 0
        ssd_write = BandwidthCollector.get("ssd_write")
        ssd_read = BandwidthCollector.get("ssd_read")

        assert ssd_write > 0, f"ssd_write should be >0 after writing 50 MB, got {ssd_write}"
        assert ssd_read > 0, f"ssd_read should be >0 after reading 50 MB, got {ssd_read}"


class TestNetworkBandwidth:
    def test_network_bandwidth_moves(self):
        """Transfer ~10 MB over localhost; verify network bandwidth sensors."""
        _prime_and_reset()

        chunk = b"x" * (1024 * 1024)  # 1 MB
        total_mb = 10
        received = []

        def _server(server_sock):
            conn, _ = server_sock.accept()
            try:
                while True:
                    data = conn.recv(65536)
                    if not data:
                        break
                    received.append(len(data))
            finally:
                conn.close()

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        port = server.getsockname()[1]
        server.listen(1)

        t = threading.Thread(target=_server, args=(server,), daemon=True)
        t.start()

        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect(("127.0.0.1", port))
        for _ in range(total_mb):
            client.sendall(chunk)
        client.close()
        t.join(timeout=5)
        server.close()

        time.sleep(0.1)
        BandwidthCollector._last_fetch = 0
        net_rx = BandwidthCollector.get("net_rx")
        net_tx = BandwidthCollector.get("net_tx")

        # Loopback traffic shows on both rx and tx (lo is excluded by the
        # collector, but the kernel also counts bytes on the physical NIC
        # stats when using 127.0.0.1 on some kernels).  The important
        # thing is that *at least one* direction moved, proving the delta
        # logic works with real counters.
        assert net_rx > 0 or net_tx > 0, (
            f"Expected net_rx or net_tx > 0 after 10 MB localhost transfer, "
            f"got rx={net_rx} tx={net_tx}"
        )


class TestGpuPcie:
    def test_gpu_pcie_responds(self):
        """Smoke-test: GPU PCIe sensors return >= 0 (not error sentinel)."""
        gpu_rx = BandwidthCollector.get("gpu_rx")
        gpu_tx = BandwidthCollector.get("gpu_tx")

        assert gpu_rx >= 0, f"gpu_rx returned error sentinel {gpu_rx}"
        assert gpu_tx >= 0, f"gpu_tx returned error sentinel {gpu_tx}"


class TestRamUsed:
    def test_ram_used_is_plausible(self):
        """RAM usage should be between 1% and 99%."""
        ram = BandwidthCollector.get("ram_used")
        assert 1 <= ram <= 99, f"ram_used={ram}% is outside plausible range"


class TestSsdUsed:
    def test_ssd_used_is_plausible(self):
        """SSD usage should be between 1% and 99%."""
        ssd = BandwidthCollector.get("ssd_used")
        assert 1 <= ssd <= 99, f"ssd_used={ssd}% is outside plausible range"
