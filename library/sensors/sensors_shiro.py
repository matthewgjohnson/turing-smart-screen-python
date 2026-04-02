"""
Shiro custom sensors for Project Shiro system monitoring.

Provides 34 sensor classes organized by collector:
- GpuCollector: GPU0/GPU1 metrics via nvidia-smi
- CpuCollector: CPU temp, power, fans via hwmon/RAPL
- BandwidthCollector: PCIe, disk, network, RAM/SSD usage
"""

import glob
import logging
import os
import subprocess
import time
from typing import Dict, List, Optional, Tuple, Union, cast

from library.sensors.sensors_base import CustomDataSource

logger = logging.getLogger(__name__)


# ============================================================================
# COLLECTORS
# ============================================================================

class GpuCollector:
    """Fetches all GPU metrics efficiently via single nvidia-smi call."""

    _cache: Dict[int, Dict[str, Union[int, float, str]]] = {}
    _last_fetch: float = 0
    _cache_ttl: float = 1.0  # seconds

    _query_fields = [
        'temperature.gpu',
        'temperature.gpu.tlimit',
        'fan.speed',
        'utilization.gpu',
        'power.draw',
        'power.limit',
        'memory.used',
        'memory.total',
        'pstate',
        'clocks_throttle_reasons.sw_power_cap'
    ]

    @classmethod
    def _fetch(cls):
        """Fetch metrics for all GPUs."""
        now = time.time()
        if now - cls._last_fetch < cls._cache_ttl and cls._cache:
            return

        try:
            query = ','.join(cls._query_fields)
            cmd = ['nvidia-smi', f'--query-gpu={query}', '--format=csv,noheader,nounits']
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5
            )

            cls._cache = {}
            for gpu_id, line in enumerate(result.stdout.strip().split('\n')):
                values = [v.strip() for v in line.split(',')]
                if len(values) >= 10:
                    cls._cache[gpu_id] = {
                        'temp': int(values[0]) if values[0] not in ['[N/A]', '[Not Supported]'] else -1,
                        'temp_headroom': int(values[1]) if values[1] not in ['[N/A]', '[Not Supported]'] else 0,
                        'fan': int(values[2]) if values[2] not in ['[N/A]', '[Not Supported]'] else -1,
                        'load': int(values[3]) if values[3] not in ['[N/A]', '[Not Supported]'] else -1,
                        'power': float(values[4]) if values[4] not in ['[N/A]', '[Not Supported]'] else -1,
                        'power_limit': float(values[5]) if values[5] not in ['[N/A]', '[Not Supported]'] else -1,
                        'vram_used': int(values[6]) if values[6] not in ['[N/A]', '[Not Supported]'] else -1,
                        'vram_total': int(values[7]) if values[7] not in ['[N/A]', '[Not Supported]'] else -1,
                        'pstate': values[8] if values[8] not in ['[N/A]', '[Not Supported]'] else '--',
                        'sw_power_cap': values[9] if values[9] not in ['[N/A]', '[Not Supported]'] else '--'
                    }
            cls._last_fetch = now

        except Exception as e:
            logger.warning("GpuCollector fetch failed: %s", e)

    @classmethod
    def get(cls, gpu_id: int, metric: str) -> Union[int, float, str]:
        """Get a specific metric for a GPU."""
        cls._fetch()
        try:
            return cls._cache[gpu_id][metric]
        except KeyError:
            return -1


class CpuCollector:
    """Fetches CPU temp, power, and fan metrics from hwmon/RAPL."""

    _cache: Dict[str, Union[int, float]] = {}
    _last_fetch: float = 0
    _cache_ttl: float = 1.0

    _k10temp_path: Optional[str] = None
    _nct6798_path: Optional[str] = None
    _rapl_path: str = '/sys/class/powercap/intel-rapl:0'

    _prev_energy: Optional[int] = None
    _prev_time: Optional[float] = None

    @classmethod
    def _discover_hwmon(cls):
        """Find hwmon paths by reading name files."""
        if cls._k10temp_path and cls._nct6798_path:
            return

        for path in glob.glob('/sys/class/hwmon/hwmon*/name'):
            try:
                with open(path) as f:
                    name = f.read().strip()
                hwmon_dir = os.path.dirname(path)
                if name == 'k10temp':
                    cls._k10temp_path = hwmon_dir
                elif name == 'nct6798':
                    cls._nct6798_path = hwmon_dir
            except Exception:
                pass

    @classmethod
    def _fetch(cls):
        """Fetch all CPU/cooling metrics."""
        now = time.time()
        if now - cls._last_fetch < cls._cache_ttl and cls._cache:
            return

        cls._discover_hwmon()

        try:
            # CPU temp (k10temp)
            if cls._k10temp_path:
                with open(f'{cls._k10temp_path}/temp1_input') as f:
                    temp_raw = int(f.read().strip())
                cls._cache['temp'] = temp_raw // 1000
            else:
                cls._cache['temp'] = -1

            # Fan speeds (nct6798)
            fan_map = {
                'pump': 'fan7_input',
                'rad': 'fan2_input',
                'rear': 'fan6_input',
                'intake': 'fan5_input'
            }
            for key, filename in fan_map.items():
                try:
                    if cls._nct6798_path:
                        with open(f'{cls._nct6798_path}/{filename}') as f:
                            cls._cache[key] = int(f.read().strip())
                    else:
                        cls._cache[key] = -1
                except Exception:
                    cls._cache[key] = -1

            # CPU power (RAPL delta)
            try:
                with open(f'{cls._rapl_path}/energy_uj') as f:
                    energy = int(f.read().strip())

                if cls._prev_energy is not None and cls._prev_time is not None:
                    delta_energy = energy - cls._prev_energy
                    delta_time = now - cls._prev_time
                    if delta_time > 0:
                        cls._cache['power'] = int(delta_energy / delta_time / 1_000_000)
                    else:
                        cls._cache['power'] = 0
                else:
                    cls._cache['power'] = 0

                cls._prev_energy = energy
                cls._prev_time = now
            except Exception:
                cls._cache['power'] = -1

            cls._last_fetch = now

        except Exception as e:
            logger.warning("CpuCollector fetch failed: %s", e)

    @classmethod
    def get(cls, metric: str) -> Union[int, float]:
        """Get a specific CPU metric."""
        cls._fetch()
        return cls._cache.get(metric, -1)


class BandwidthCollector:
    """Fetches bandwidth metrics from various sources."""

    _cache: Dict[str, Union[int, float]] = {}
    _last_fetch: float = 0
    _cache_ttl: float = 1.0

    # Separate cache for GPU PCIe (slow dmon call)
    _gpu_cache: Dict[str, Union[int, float]] = {'gpu_rx': 0, 'gpu_tx': 0}
    _gpu_last_fetch: float = 0
    _gpu_cache_ttl: float = 5.0

    _prev_disk: Optional[Dict[str, int]] = None
    _prev_net: Optional[Dict[str, int]] = None
    _prev_time: Optional[float] = None

    _disks: Optional[List[str]] = None  # Auto-detected

    @classmethod
    def _detect_disks(cls):
        """Auto-detect NVMe disks."""
        if cls._disks is not None:
            return
        cls._disks = []
        try:
            for name in os.listdir('/sys/block'):
                if name.startswith('nvme') and name[-1].isdigit():
                    cls._disks.append(name)
        except Exception:
            pass

    @classmethod
    def _fetch_gpu_pcie(cls):
        """Fetch GPU PCIe bandwidth (slow, cached for 5s)."""
        now = time.time()
        if now - cls._gpu_last_fetch < cls._gpu_cache_ttl:
            return

        try:
            result = subprocess.run(
                ['nvidia-smi', 'dmon', '-s', 't', '-c', '1'],
                capture_output=True,
                text=True,
                timeout=5
            )
            lines = [l for l in result.stdout.split('\n') if l and not l.startswith('#')]
            gpu_rx_total = 0
            gpu_tx_total = 0
            for line in lines[-2:]:
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        gpu_rx_total += int(parts[1])
                        gpu_tx_total += int(parts[2])
                    except ValueError:
                        pass
            cls._gpu_cache['gpu_rx'] = gpu_rx_total * 8 / 1000
            cls._gpu_cache['gpu_tx'] = gpu_tx_total * 8 / 1000
            cls._gpu_last_fetch = now
        except Exception:
            cls._gpu_cache['gpu_rx'] = -1
            cls._gpu_cache['gpu_tx'] = -1

    @classmethod
    def _fetch(cls):
        """Fetch bandwidth metrics (excluding GPU PCIe)."""
        now = time.time()
        if now - cls._last_fetch < cls._cache_ttl and cls._cache:
            return

        cls._detect_disks()

        try:
            # Disk I/O
            read_sectors = 0
            write_sectors = 0
            with open('/proc/diskstats') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 14 and parts[2] in cls._disks:
                        read_sectors += int(parts[5])
                        write_sectors += int(parts[9])

            # Network I/O
            net_rx = 0
            net_tx = 0
            with open('/proc/net/dev') as f:
                for line in f:
                    if ':' in line and 'lo:' not in line:
                        parts = line.split()
                        if len(parts) >= 10:
                            try:
                                rx_val = int(parts[1].split(':')[-1]) if ':' in parts[1] else int(parts[1])
                                net_rx += rx_val
                                net_tx += int(parts[9])
                            except (ValueError, IndexError):
                                pass

            # Calculate deltas
            if cls._prev_disk is not None and cls._prev_time is not None:
                delta_t = now - cls._prev_time
                if delta_t > 0:
                    delta_read = read_sectors - cls._prev_disk['read']
                    delta_write = write_sectors - cls._prev_disk['write']
                    cls._cache['ssd_read'] = delta_read * 512 * 8 / delta_t / 1e9
                    cls._cache['ssd_write'] = delta_write * 512 * 8 / delta_t / 1e9

                    delta_rx = net_rx - cls._prev_net['rx']
                    delta_tx = net_tx - cls._prev_net['tx']
                    cls._cache['net_rx'] = delta_rx * 8 / delta_t / 1e9
                    cls._cache['net_tx'] = delta_tx * 8 / delta_t / 1e9
            else:
                cls._cache['ssd_read'] = 0
                cls._cache['ssd_write'] = 0
                cls._cache['net_rx'] = 0
                cls._cache['net_tx'] = 0

            cls._prev_disk = {'read': read_sectors, 'write': write_sectors}
            cls._prev_net = {'rx': net_rx, 'tx': net_tx}
            cls._prev_time = now

            # RAM used %
            with open('/proc/meminfo') as f:
                meminfo = {}
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        meminfo[parts[0].rstrip(':')] = int(parts[1])
            total = meminfo.get('MemTotal', 1)
            avail = meminfo.get('MemAvailable', 0)
            cls._cache['ram_used'] = int((total - avail) * 100 / total)

            # SSD used %
            try:
                result = subprocess.run(
                    ['df', '--total', '--output=pcent'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                lines = result.stdout.strip().split('\n')
                cls._cache['ssd_used'] = int(lines[-1].strip().rstrip('%'))
            except Exception:
                cls._cache['ssd_used'] = -1

            cls._last_fetch = now

        except Exception as e:
            logger.warning("BandwidthCollector fetch failed: %s", e)

    @classmethod
    def get(cls, metric: str) -> Union[int, float]:
        """Get a specific bandwidth metric."""
        if metric in ('gpu_rx', 'gpu_tx'):
            cls._fetch_gpu_pcie()
            return cls._gpu_cache.get(metric, -1)
        cls._fetch()
        return cls._cache.get(metric, -1)


# ============================================================================
# GPU0 SENSORS
# ============================================================================

class Gpu0_Temp(CustomDataSource):
    def as_numeric(self) -> float:
        return cast(float, GpuCollector.get(0, 'temp'))

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>3.0f}"

    def last_values(self) -> List[float]:
        return []


class Gpu0_TempMax(CustomDataSource):
    def as_numeric(self) -> float:
        temp = cast(float, GpuCollector.get(0, 'temp'))
        headroom = cast(float, GpuCollector.get(0, 'temp_headroom'))
        if temp < 0 or headroom < 0:
            return -1
        return temp + headroom

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>3.0f}"

    def last_values(self) -> List[float]:
        return []


class Gpu0_Fan(CustomDataSource):
    def as_numeric(self) -> float:
        return cast(float, GpuCollector.get(0, 'fan'))

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>3.0f}"

    def last_values(self) -> List[float]:
        return []


class Gpu0_Load(CustomDataSource):
    def as_numeric(self) -> float:
        return cast(float, GpuCollector.get(0, 'load'))

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>3.0f}"

    def last_values(self) -> List[float]:
        return []


class Gpu0_Power(CustomDataSource):
    def as_numeric(self) -> float:
        return cast(float, GpuCollector.get(0, 'power'))

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>3.0f}"

    def last_values(self) -> List[float]:
        return []


class Gpu0_PowerMax(CustomDataSource):
    def as_numeric(self) -> float:
        return cast(float, GpuCollector.get(0, 'power_limit'))

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>3.0f}"

    def last_values(self) -> List[float]:
        return []


class Gpu0_Vram(CustomDataSource):
    def as_numeric(self) -> float:
        val = cast(float, GpuCollector.get(0, 'vram_used'))
        if val < 0:
            return -1
        return val / 1024

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>5.1f}"

    def last_values(self) -> List[float]:
        return []


class Gpu0_VramMax(CustomDataSource):
    def as_numeric(self) -> float:
        val = cast(float, GpuCollector.get(0, 'vram_total'))
        if val < 0:
            return -1
        return val / 1024

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>4.1f}"

    def last_values(self) -> List[float]:
        return []


class Gpu0_Pstate(CustomDataSource):
    def as_numeric(self) -> float:
        return 0.0

    def as_string(self) -> str:
        val = GpuCollector.get(0, 'pstate')
        return str(val) if val != -1 else "--"

    def last_values(self) -> List[float]:
        return []


class Gpu0_SwPowerCap(CustomDataSource):
    def as_numeric(self) -> float:
        return 0.0

    def as_string(self) -> str:
        val = GpuCollector.get(0, 'sw_power_cap')
        return str(val) if val != -1 else "--"

    def last_values(self) -> List[float]:
        return []


# ============================================================================
# GPU1 SENSORS
# ============================================================================

class Gpu1_Temp(CustomDataSource):
    def as_numeric(self) -> float:
        return cast(float, GpuCollector.get(1, 'temp'))

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>3.0f}"

    def last_values(self) -> List[float]:
        return []


class Gpu1_TempMax(CustomDataSource):
    def as_numeric(self) -> float:
        temp = cast(float, GpuCollector.get(1, 'temp'))
        headroom = cast(float, GpuCollector.get(1, 'temp_headroom'))
        if temp < 0 or headroom < 0:
            return -1
        return temp + headroom

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>3.0f}"

    def last_values(self) -> List[float]:
        return []


class Gpu1_Fan(CustomDataSource):
    def as_numeric(self) -> float:
        return cast(float, GpuCollector.get(1, 'fan'))

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>3.0f}"

    def last_values(self) -> List[float]:
        return []


class Gpu1_Load(CustomDataSource):
    def as_numeric(self) -> float:
        return cast(float, GpuCollector.get(1, 'load'))

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>3.0f}"

    def last_values(self) -> List[float]:
        return []


class Gpu1_Power(CustomDataSource):
    def as_numeric(self) -> float:
        return cast(float, GpuCollector.get(1, 'power'))

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>3.0f}"

    def last_values(self) -> List[float]:
        return []


class Gpu1_PowerMax(CustomDataSource):
    def as_numeric(self) -> float:
        return cast(float, GpuCollector.get(1, 'power_limit'))

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>3.0f}"

    def last_values(self) -> List[float]:
        return []


class Gpu1_Vram(CustomDataSource):
    def as_numeric(self) -> float:
        val = cast(float, GpuCollector.get(1, 'vram_used'))
        if val < 0:
            return -1
        return val / 1024

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>5.1f}"

    def last_values(self) -> List[float]:
        return []


class Gpu1_VramMax(CustomDataSource):
    def as_numeric(self) -> float:
        val = cast(float, GpuCollector.get(1, 'vram_total'))
        if val < 0:
            return -1
        return val / 1024

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>4.1f}"

    def last_values(self) -> List[float]:
        return []


class Gpu1_Pstate(CustomDataSource):
    def as_numeric(self) -> float:
        return 0.0

    def as_string(self) -> str:
        val = GpuCollector.get(1, 'pstate')
        return str(val) if val != -1 else "--"

    def last_values(self) -> List[float]:
        return []


class Gpu1_SwPowerCap(CustomDataSource):
    def as_numeric(self) -> float:
        return 0.0

    def as_string(self) -> str:
        val = GpuCollector.get(1, 'sw_power_cap')
        return str(val) if val != -1 else "--"

    def last_values(self) -> List[float]:
        return []


# ============================================================================
# CPU SENSORS
# ============================================================================

class Cpu_Temp(CustomDataSource):
    def as_numeric(self) -> float:
        return CpuCollector.get('temp')

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>3.0f}"

    def last_values(self) -> List[float]:
        return []


class Cpu_Power(CustomDataSource):
    def as_numeric(self) -> float:
        return CpuCollector.get('power')

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>3.0f}"

    def last_values(self) -> List[float]:
        return []


class Cpu_Pump(CustomDataSource):
    def as_numeric(self) -> float:
        return CpuCollector.get('pump')

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "----"
        return f"{val:>4.0f}"

    def last_values(self) -> List[float]:
        return []


class Cpu_Rad(CustomDataSource):
    def as_numeric(self) -> float:
        return CpuCollector.get('rad')

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "----"
        return f"{val:>4.0f}"

    def last_values(self) -> List[float]:
        return []


class Cpu_Rear(CustomDataSource):
    def as_numeric(self) -> float:
        return CpuCollector.get('rear')

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "----"
        return f"{val:>4.0f}"

    def last_values(self) -> List[float]:
        return []


class Cpu_Intake(CustomDataSource):
    def as_numeric(self) -> float:
        return CpuCollector.get('intake')

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "----"
        return f"{val:>4.0f}"

    def last_values(self) -> List[float]:
        return []


class Cpu_Load(CustomDataSource):
    """CPU load from /proc/stat."""
    _prev_idle: Optional[int] = None
    _prev_total: Optional[int] = None
    _load: float = 0

    def as_numeric(self) -> float:
        try:
            with open('/proc/stat') as f:
                line = f.readline()
            parts = line.split()[1:]  # Skip 'cpu' label
            idle = int(parts[3])
            total = sum(int(p) for p in parts[:8])

            if Cpu_Load._prev_idle is not None:
                delta_idle = idle - Cpu_Load._prev_idle
                delta_total = total - Cpu_Load._prev_total
                if delta_total > 0:
                    Cpu_Load._load = 100 * (1 - delta_idle / delta_total)

            Cpu_Load._prev_idle = idle
            Cpu_Load._prev_total = total
            return Cpu_Load._load
        except Exception:
            return 0

    def as_string(self) -> str:
        return f"{self.as_numeric():>3.0f}%"

    def last_values(self) -> List[float]:
        return []


# ============================================================================
# PLACEHOLDER SENSORS FOR CONDITIONAL FORMATTING
# ============================================================================

class _PlaceholderSensor(CustomDataSource):
    """Base class for placeholder sensors used with CASE/WHEN/ELSE blocks."""
    def as_numeric(self) -> float:
        return 0

    def as_string(self) -> str:
        return "OK"

    def last_values(self) -> List[float]:
        return []


# GPU0 Status Flags
class Gpu0_StateFlag(_PlaceholderSensor):
    pass

class Gpu0_TempFlag(_PlaceholderSensor):
    pass

class Gpu0_PowerFlag(_PlaceholderSensor):
    pass

class Gpu0_CoolFlag(_PlaceholderSensor):
    pass


# GPU1 Status Flags
class Gpu1_StateFlag(_PlaceholderSensor):
    pass

class Gpu1_TempFlag(_PlaceholderSensor):
    pass

class Gpu1_PowerFlag(_PlaceholderSensor):
    pass

class Gpu1_CoolFlag(_PlaceholderSensor):
    pass


# CPU Status Flags
class Cpu_StateFlag(_PlaceholderSensor):
    pass

class Cpu_TempFlag(_PlaceholderSensor):
    pass

class Cpu_PowerFlag(_PlaceholderSensor):
    pass

class Cpu_CoolFlag(_PlaceholderSensor):
    pass


# ============================================================================
# BANDWIDTH SENSORS
# ============================================================================

class Bw_GpuRx(CustomDataSource):
    def as_numeric(self) -> float:
        return BandwidthCollector.get('gpu_rx')

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>2.0f}"

    def last_values(self) -> List[float]:
        return []


class Bw_GpuTx(CustomDataSource):
    def as_numeric(self) -> float:
        return BandwidthCollector.get('gpu_tx')

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>2.0f}"

    def last_values(self) -> List[float]:
        return []


class Bw_SsdRead(CustomDataSource):
    def as_numeric(self) -> float:
        return BandwidthCollector.get('ssd_read')

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>2.0f}"

    def last_values(self) -> List[float]:
        return []


class Bw_SsdWrite(CustomDataSource):
    def as_numeric(self) -> float:
        return BandwidthCollector.get('ssd_write')

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>2.0f}"

    def last_values(self) -> List[float]:
        return []


class Bw_NetRx(CustomDataSource):
    def as_numeric(self) -> float:
        return BandwidthCollector.get('net_rx')

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "----"
        return f"{val:>4.2f}"

    def last_values(self) -> List[float]:
        return []


class Bw_NetTx(CustomDataSource):
    def as_numeric(self) -> float:
        return BandwidthCollector.get('net_tx')

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "----"
        return f"{val:>4.2f}"

    def last_values(self) -> List[float]:
        return []


class Bw_RamUsed(CustomDataSource):
    def as_numeric(self) -> float:
        return BandwidthCollector.get('ram_used')

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>3.0f}"

    def last_values(self) -> List[float]:
        return []


class Bw_SsdUsed(CustomDataSource):
    def as_numeric(self) -> float:
        return BandwidthCollector.get('ssd_used')

    def as_string(self) -> str:
        val = self.as_numeric()
        if val < 0:
            return "-- "
        return f"{val:>3.0f}"

    def last_values(self) -> List[float]:
        return []
