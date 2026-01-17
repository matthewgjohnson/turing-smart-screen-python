# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for Shiro custom sensors."""

import sys
import unittest
from unittest.mock import patch, mock_open, MagicMock
import time

# Mock the library.sensors.sensors_base before importing sensors_shiro
mock_base = MagicMock()
mock_base.CustomDataSource = object
sys.modules['library'] = MagicMock()
sys.modules['library.sensors'] = MagicMock()
sys.modules['library.sensors.sensors_base'] = mock_base

import sensors_shiro


class TestGpuCollector(unittest.TestCase):
    """Tests for GpuCollector."""

    def setUp(self):
        sensors_shiro.GpuCollector._cache = {}
        sensors_shiro.GpuCollector._last_fetch = 0

    @patch('subprocess.run')
    def test_fetch_parses_nvidia_smi_output(self, mock_run):
        """Test that nvidia-smi output is correctly parsed."""
        mock_run.return_value = MagicMock(
            stdout="65, 18, 45, 98, 250.5, 350.0, 8192, 24576, P0, Active\n"
                   "42, 10, 30, 50, 8.5, 75.0, 1024, 8192, P8, Not Active\n"
        )

        sensors_shiro.GpuCollector._fetch()

        # GPU 0
        self.assertEqual(sensors_shiro.GpuCollector._cache[0]['temp'], 65)
        self.assertEqual(sensors_shiro.GpuCollector._cache[0]['temp_headroom'], 18)
        self.assertEqual(sensors_shiro.GpuCollector._cache[0]['fan'], 45)
        self.assertEqual(sensors_shiro.GpuCollector._cache[0]['load'], 98)
        self.assertAlmostEqual(sensors_shiro.GpuCollector._cache[0]['power'], 250.5)
        self.assertAlmostEqual(sensors_shiro.GpuCollector._cache[0]['power_limit'], 350.0)
        self.assertEqual(sensors_shiro.GpuCollector._cache[0]['vram_used'], 8192)
        self.assertEqual(sensors_shiro.GpuCollector._cache[0]['vram_total'], 24576)
        self.assertEqual(sensors_shiro.GpuCollector._cache[0]['pstate'], 'P0')

        # GPU 1
        self.assertEqual(sensors_shiro.GpuCollector._cache[1]['temp'], 42)
        self.assertEqual(sensors_shiro.GpuCollector._cache[1]['power'], 8.5)

    @patch('subprocess.run')
    def test_cache_ttl_prevents_refetch(self, mock_run):
        """Test that cache TTL prevents redundant fetches."""
        mock_run.return_value = MagicMock(
            stdout="65, 18, 45, 98, 250.5, 350.0, 8192, 24576, P0, Active\n"
        )

        sensors_shiro.GpuCollector._fetch()
        sensors_shiro.GpuCollector._fetch()
        sensors_shiro.GpuCollector._fetch()

        # Should only call subprocess once due to cache
        self.assertEqual(mock_run.call_count, 1)

    @patch('subprocess.run')
    def test_handles_na_values(self, mock_run):
        """Test that [N/A] values are handled correctly."""
        mock_run.return_value = MagicMock(
            stdout="65, [N/A], [Not Supported], 98, 250.5, 350.0, 8192, 24576, P0, [N/A]\n"
        )

        sensors_shiro.GpuCollector._fetch()

        self.assertEqual(sensors_shiro.GpuCollector._cache[0]['temp'], 65)
        self.assertEqual(sensors_shiro.GpuCollector._cache[0]['temp_headroom'], 0)
        self.assertEqual(sensors_shiro.GpuCollector._cache[0]['fan'], -1)

    @patch('subprocess.run')
    def test_get_returns_metric(self, mock_run):
        """Test get() returns correct metric value."""
        mock_run.return_value = MagicMock(
            stdout="65, 18, 45, 98, 250.5, 350.0, 8192, 24576, P0, Active\n"
        )

        result = sensors_shiro.GpuCollector.get(0, 'temp')
        self.assertEqual(result, 65)

    @patch('subprocess.run')
    def test_get_missing_gpu_returns_minus_one(self, mock_run):
        """Test get() returns -1 for missing GPU."""
        mock_run.return_value = MagicMock(stdout="65, 18, 45, 98, 250.5, 350.0, 8192, 24576, P0, Active\n")

        result = sensors_shiro.GpuCollector.get(5, 'temp')
        self.assertEqual(result, -1)


class TestCpuCollector(unittest.TestCase):
    """Tests for CpuCollector."""

    def setUp(self):
        sensors_shiro.CpuCollector._cache = {}
        sensors_shiro.CpuCollector._last_fetch = 0
        sensors_shiro.CpuCollector._k10temp_path = '/sys/class/hwmon/hwmon0'
        sensors_shiro.CpuCollector._nct6798_path = '/sys/class/hwmon/hwmon1'
        sensors_shiro.CpuCollector._prev_energy = None
        sensors_shiro.CpuCollector._prev_time = None

    def test_cpu_temp_parsing(self):
        """Test CPU temperature is correctly parsed from hwmon."""
        mock_files = {
            '/sys/class/hwmon/hwmon0/temp1_input': '65000',
            '/sys/class/hwmon/hwmon1/fan7_input': '2500',
            '/sys/class/hwmon/hwmon1/fan2_input': '1200',
            '/sys/class/hwmon/hwmon1/fan6_input': '800',
            '/sys/class/hwmon/hwmon1/fan5_input': '600',
            '/sys/class/powercap/intel-rapl:0/energy_uj': '1000000000',
        }

        def mock_open_fn(path, *args, **kwargs):
            if path in mock_files:
                return mock_open(read_data=mock_files[path])()
            raise FileNotFoundError(path)

        with patch('builtins.open', mock_open_fn):
            sensors_shiro.CpuCollector._fetch()

        self.assertEqual(sensors_shiro.CpuCollector._cache['temp'], 65)
        self.assertEqual(sensors_shiro.CpuCollector._cache['pump'], 2500)
        self.assertEqual(sensors_shiro.CpuCollector._cache['rad'], 1200)

    def test_cpu_power_delta_calculation(self):
        """Test CPU power is calculated from RAPL delta."""
        sensors_shiro.CpuCollector._prev_energy = 1000000000  # 1J
        sensors_shiro.CpuCollector._prev_time = time.time() - 1  # 1 second ago

        mock_files = {
            '/sys/class/hwmon/hwmon0/temp1_input': '65000',
            '/sys/class/hwmon/hwmon1/fan7_input': '2500',
            '/sys/class/hwmon/hwmon1/fan2_input': '1200',
            '/sys/class/hwmon/hwmon1/fan6_input': '800',
            '/sys/class/hwmon/hwmon1/fan5_input': '600',
            '/sys/class/powercap/intel-rapl:0/energy_uj': '1100000000',  # 1.1J (100W for 1s)
        }

        def mock_open_fn(path, *args, **kwargs):
            if path in mock_files:
                return mock_open(read_data=mock_files[path])()
            raise FileNotFoundError(path)

        with patch('builtins.open', mock_open_fn):
            sensors_shiro.CpuCollector._fetch()

        # Should be approximately 100W (100M uJ over 1 second)
        self.assertGreater(sensors_shiro.CpuCollector._cache['power'], 90)
        self.assertLess(sensors_shiro.CpuCollector._cache['power'], 110)


class TestBandwidthCollector(unittest.TestCase):
    """Tests for BandwidthCollector."""

    def setUp(self):
        sensors_shiro.BandwidthCollector._cache = {}
        sensors_shiro.BandwidthCollector._gpu_cache = {'gpu_rx': 0, 'gpu_tx': 0}
        sensors_shiro.BandwidthCollector._last_fetch = 0
        sensors_shiro.BandwidthCollector._gpu_last_fetch = 0
        sensors_shiro.BandwidthCollector._prev_disk = None
        sensors_shiro.BandwidthCollector._prev_net = None
        sensors_shiro.BandwidthCollector._prev_time = None
        sensors_shiro.BandwidthCollector._disks = None

    @patch('os.listdir')
    def test_nvme_disk_detection(self, mock_listdir):
        """Test NVMe disks are auto-detected."""
        mock_listdir.return_value = ['nvme0n1', 'nvme1n1', 'sda', 'loop0']

        sensors_shiro.BandwidthCollector._detect_disks()

        self.assertIn('nvme0n1', sensors_shiro.BandwidthCollector._disks)
        self.assertIn('nvme1n1', sensors_shiro.BandwidthCollector._disks)
        self.assertNotIn('sda', sensors_shiro.BandwidthCollector._disks)
        self.assertNotIn('loop0', sensors_shiro.BandwidthCollector._disks)

    @patch('sensors_shiro.subprocess.run')
    def test_gpu_pcie_uses_separate_cache(self, mock_run):
        """Test GPU PCIe bandwidth uses 5s TTL cache."""
        mock_run.return_value = MagicMock(
            stdout="# gpu  rxpci  txpci\n0  1000  500\n1  800  400\n"
        )

        # First fetch
        sensors_shiro.BandwidthCollector._fetch_gpu_pcie()

        # Verify it was called
        self.assertEqual(mock_run.call_count, 1)

        # Second fetch within TTL should not call subprocess
        sensors_shiro.BandwidthCollector._fetch_gpu_pcie()
        self.assertEqual(mock_run.call_count, 1)

        # Check values are calculated (MB/s * 8 / 1000 = Gbps)
        self.assertAlmostEqual(
            sensors_shiro.BandwidthCollector._gpu_cache['gpu_rx'],
            (1000 + 800) * 8 / 1000
        )

    def test_get_routes_gpu_metrics_correctly(self):
        """Test get() uses correct cache for GPU metrics."""
        sensors_shiro.BandwidthCollector._gpu_cache = {'gpu_rx': 5.0, 'gpu_tx': 3.0}
        sensors_shiro.BandwidthCollector._cache = {'ssd_read': 1.0}
        sensors_shiro.BandwidthCollector._gpu_last_fetch = time.time()  # Prevent refetch
        sensors_shiro.BandwidthCollector._last_fetch = time.time()

        # GPU metric should return from _gpu_cache
        result = sensors_shiro.BandwidthCollector.get('gpu_rx')
        self.assertEqual(result, 5.0)

        # Non-GPU metric should return from _cache
        result = sensors_shiro.BandwidthCollector.get('ssd_read')
        self.assertEqual(result, 1.0)

    def test_gpu_pcie_ttl_is_5_seconds(self):
        """Test GPU PCIe cache TTL is 5 seconds."""
        self.assertEqual(sensors_shiro.BandwidthCollector._gpu_cache_ttl, 5.0)


class TestSensorFormatting(unittest.TestCase):
    """Tests for sensor string formatting."""

    def test_gpu_temp_fixed_width(self):
        """Test GPU temp uses fixed-width formatting."""
        with patch.object(sensors_shiro.GpuCollector, 'get', return_value=65):
            sensor = sensors_shiro.Gpu0_Temp()
            result = sensor.as_string()

            self.assertEqual(len(result), 4)  # " 65c"
            self.assertTrue(result.endswith('c'))
            self.assertEqual(result, " 65c")

    def test_gpu_temp_negative_shows_dashes(self):
        """Test GPU temp shows dashes when unavailable."""
        with patch.object(sensors_shiro.GpuCollector, 'get', return_value=-1):
            sensor = sensors_shiro.Gpu0_Temp()
            result = sensor.as_string()

            self.assertEqual(result, "-- ")

    def test_gpu_power_fixed_width(self):
        """Test GPU power uses fixed-width formatting."""
        with patch.object(sensors_shiro.GpuCollector, 'get', return_value=8):
            sensor = sensors_shiro.Gpu1_Power()
            result = sensor.as_string()

            self.assertEqual(len(result), 4)  # "  8w"
            self.assertTrue(result.endswith('w'))
            self.assertEqual(result, "  8w")

    def test_gpu_power_three_digits(self):
        """Test GPU power with 3 digits."""
        with patch.object(sensors_shiro.GpuCollector, 'get', return_value=250):
            sensor = sensors_shiro.Gpu0_Power()
            result = sensor.as_string()

            self.assertEqual(len(result), 4)  # "250w"
            self.assertEqual(result, "250w")

    def test_fan_fixed_width(self):
        """Test fan RPM uses fixed-width formatting."""
        with patch.object(sensors_shiro.CpuCollector, 'get', return_value=850):
            sensor = sensors_shiro.Cpu_Rear()
            result = sensor.as_string()

            self.assertEqual(len(result), 4)  # " 850"
            self.assertEqual(result, " 850")

    def test_fan_four_digits(self):
        """Test fan RPM with 4 digits."""
        with patch.object(sensors_shiro.CpuCollector, 'get', return_value=2500):
            sensor = sensors_shiro.Cpu_Pump()
            result = sensor.as_string()

            self.assertEqual(len(result), 4)  # "2500"
            self.assertEqual(result, "2500")

    def test_vram_formatting(self):
        """Test VRAM uses proper GB formatting."""
        with patch.object(sensors_shiro.GpuCollector, 'get', return_value=8192):
            sensor = sensors_shiro.Gpu0_Vram()
            result = sensor.as_string()

            self.assertEqual(len(result), 7)  # "  8.0gb"
            self.assertTrue(result.endswith('gb'))

    def test_cpu_power_fixed_width(self):
        """Test CPU power uses fixed-width formatting."""
        with patch.object(sensors_shiro.CpuCollector, 'get', return_value=129):
            sensor = sensors_shiro.Cpu_Power()
            result = sensor.as_string()

            self.assertEqual(len(result), 4)  # "129w"
            self.assertTrue(result.endswith('w'))

    def test_percentage_formatting(self):
        """Test percentage values use fixed-width formatting."""
        with patch.object(sensors_shiro.BandwidthCollector, 'get', return_value=45):
            sensors_shiro.BandwidthCollector._last_fetch = time.time()
            sensor = sensors_shiro.Bw_RamUsed()
            result = sensor.as_string()

            self.assertEqual(len(result), 4)  # " 45%"
            self.assertTrue(result.endswith('%'))


class TestSensorNumericValues(unittest.TestCase):
    """Tests for sensor numeric values."""

    def test_gpu_vram_converts_to_gb(self):
        """Test VRAM is converted from MB to GB."""
        with patch.object(sensors_shiro.GpuCollector, 'get', return_value=24576):
            sensor = sensors_shiro.Gpu0_Vram()
            result = sensor.as_numeric()

            self.assertEqual(result, 24.0)  # 24576 MB = 24 GB

    def test_gpu_temp_max_adds_headroom(self):
        """Test temp max adds headroom to current temp."""
        def mock_get(gpu_id, metric):
            if metric == 'temp':
                return 65
            elif metric == 'temp_headroom':
                return 18
            return -1

        with patch.object(sensors_shiro.GpuCollector, 'get', side_effect=mock_get):
            sensor = sensors_shiro.Gpu0_TempMax()
            result = sensor.as_numeric()

            self.assertEqual(result, 83)  # 65 + 18


if __name__ == '__main__':
    unittest.main()
