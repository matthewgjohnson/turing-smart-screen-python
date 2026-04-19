# SPDX-License-Identifier: GPL-3.0-or-later
#
# turing-smart-screen-python - a Python system monitor and library for USB-C displays like Turing Smart Screen or XuanFang
# https://github.com/mathoudebine/turing-smart-screen-python/
#
# Copyright (C) 2021 Matthieu Houdebine (mathoudebine)
# Copyright (C) 2022 Rollbacke
# Copyright (C) 2022 Ebag333
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import sched
import threading
import time

import library.config as config
import library.stats as stats
from library.display import get_all_displays

STOPPING = False

# Computed intervals from all displays (populated by compute_intervals())
_intervals = {}


def compute_intervals():
    """
    Compute scheduler intervals by scanning ALL display templates.
    For each stat, use the minimum non-zero interval across all displays.
    This ensures stats run frequently enough for the display that needs them most.
    """
    global _intervals
    _intervals = {}
    
    # Stat paths to scan: (key_name, theme_path)
    stat_paths = [
        ('CPU_PERCENTAGE', ['STATS', 'CPU', 'PERCENTAGE']),
        ('CPU_FREQUENCY', ['STATS', 'CPU', 'FREQUENCY']),
        ('CPU_LOAD', ['STATS', 'CPU', 'LOAD']),
        ('CPU_TEMPERATURE', ['STATS', 'CPU', 'TEMPERATURE']),
        ('CPU_FAN_SPEED', ['STATS', 'CPU', 'FAN_SPEED']),
        ('GPU', ['STATS', 'GPU']),
        ('MEMORY', ['STATS', 'MEMORY']),
        ('DISK', ['STATS', 'DISK']),
        ('NET', ['STATS', 'NET']),
        ('DATE', ['STATS', 'DATE']),
        ('UPTIME', ['STATS', 'UPTIME']),
        ('CUSTOM', ['STATS', 'CUSTOM']),
        ('WEATHER', ['STATS', 'WEATHER']),
        ('PING', ['STATS', 'PING']),
    ]
    
    displays = get_all_displays()
    
    for stat_name, path in stat_paths:
        min_interval = None
        
        for disp in displays:
            # Navigate the theme data path
            data = disp.theme_data
            for key in path:
                data = data.get(key, {}) if isinstance(data, dict) else {}
            
            if data:
                interval = data.get('INTERVAL', 0)
                if interval > 0:
                    if min_interval is None or interval < min_interval:
                        min_interval = interval
        
        # Store the minimum interval found (or 0 if no display needs this stat)
        _intervals[stat_name] = min_interval if min_interval else 0
    
    # Weather has a minimum of 300 seconds (API rate limiting)
    if _intervals.get('WEATHER', 0) > 0:
        _intervals['WEATHER'] = max(300, _intervals['WEATHER'])
    
    return _intervals


def get_interval(stat_name):
    """Get computed interval for a stat. Returns 0 if stat not needed."""
    return _intervals.get(stat_name, 0)


def _run_scheduled(interval_seconds, func, thread_name):
    """Run a function on a repeating schedule in its own thread."""
    if interval_seconds <= 0:
        return None
    
    def thread_func():
        scheduler = sched.scheduler(time.time, time.sleep)
        
        def periodic(sc, interval, action):
            if not STOPPING:
                sc.enter(interval, 1, periodic, (sc, interval, action))
            action()
        
        periodic(scheduler, interval_seconds, func)
        scheduler.run()
    
    thread = threading.Thread(target=thread_func, name=thread_name, daemon=False)
    thread.start()
    return thread


# Stat functions (no decorators - intervals determined at runtime)

def _cpu_percentage():
    stats.CPU.percentage()

def _cpu_frequency():
    stats.CPU.frequency()

def _cpu_load():
    stats.CPU.load()

def _cpu_temperature():
    stats.CPU.temperature()

def _cpu_fan_speed():
    stats.CPU.fan_speed()

def _gpu_stats():
    stats.Gpu.stats()

def _memory_stats():
    stats.Memory.stats()

def _disk_stats():
    stats.Disk.stats()

def _net_stats():
    stats.Net.stats()

def _date_stats():
    stats.Date.stats()

def _uptime_stats():
    stats.SystemUptime.stats()

def _custom_stats():
    stats.Custom.stats()

def _weather_stats():
    stats.Weather.stats()

def _ping_stats():
    stats.Ping.stats()

def _queue_handler():
    if STOPPING:
        while not config.update_queue.empty():
            f, args = config.update_queue.get()
            f(*args)
    else:
        f, args = config.update_queue.get()
        if f:
            f(*args)


# Registry mapping stat names to functions
_STAT_FUNCTIONS = {
    'CPU_PERCENTAGE': _cpu_percentage,
    'CPU_FREQUENCY': _cpu_frequency,
    'CPU_LOAD': _cpu_load,
    'CPU_TEMPERATURE': _cpu_temperature,
    'CPU_FAN_SPEED': _cpu_fan_speed,
    'GPU': _gpu_stats,
    'MEMORY': _memory_stats,
    'DISK': _disk_stats,
    'NET': _net_stats,
    'DATE': _date_stats,
    'UPTIME': _uptime_stats,
    'CUSTOM': _custom_stats,
    'WEATHER': _weather_stats,
    'PING': _ping_stats,
}


def start_all(stagger_delay=0.25):
    """
    Start all stat schedulers based on computed intervals from all displays.
    Call this AFTER all displays are initialized.
    
    Args:
        stagger_delay: Seconds to wait between starting each scheduler (default 0.25)
    """
    import time as time_module
    
    # Compute intervals from all display templates
    intervals = compute_intervals()
    
    threads = []
    
    # Start stats in a specific order with staggering to avoid overwhelming the system
    stat_order = [
        'CPU_PERCENTAGE',
        'CPU_FREQUENCY', 
        'CPU_LOAD',
        'CPU_TEMPERATURE',
        'CPU_FAN_SPEED',
        'GPU',  # Will be skipped if GPU not available
        'MEMORY',
        'DISK',
        'NET',
        'DATE',
        'UPTIME',
        'CUSTOM',
        'WEATHER',
        'PING',
    ]
    
    for stat_name in stat_order:
        interval = get_interval(stat_name)
        if interval <= 0:
            continue
            
        # Check GPU availability
        if stat_name == 'GPU':
            if not stats.Gpu.is_available():
                continue
        
        func = _STAT_FUNCTIONS.get(stat_name)
        if func:
            thread = _run_scheduled(interval, func, f"{stat_name}_Stats")
            if thread:
                threads.append(thread)
            time_module.sleep(stagger_delay)
    
    # Queue handler always runs (1ms interval)
    _run_scheduled(0.001, _queue_handler, "Queue_Handler")
    
    return threads


def is_queue_empty() -> bool:
    return config.update_queue.empty()


# Legacy function names for backward compatibility (if called directly)
def CPUPercentage():
    _run_scheduled(get_interval('CPU_PERCENTAGE'), _cpu_percentage, "CPU_Percentage")

def CPUFrequency():
    _run_scheduled(get_interval('CPU_FREQUENCY'), _cpu_frequency, "CPU_Frequency")

def CPULoad():
    _run_scheduled(get_interval('CPU_LOAD'), _cpu_load, "CPU_Load")

def CPUTemperature():
    _run_scheduled(get_interval('CPU_TEMPERATURE'), _cpu_temperature, "CPU_Temperature")

def CPUFanSpeed():
    _run_scheduled(get_interval('CPU_FAN_SPEED'), _cpu_fan_speed, "CPU_FanSpeed")

def GpuStats():
    _run_scheduled(get_interval('GPU'), _gpu_stats, "GPU_Stats")

def MemoryStats():
    _run_scheduled(get_interval('MEMORY'), _memory_stats, "Memory_Stats")

def DiskStats():
    _run_scheduled(get_interval('DISK'), _disk_stats, "Disk_Stats")

def NetStats():
    _run_scheduled(get_interval('NET'), _net_stats, "Net_Stats")

def DateStats():
    _run_scheduled(get_interval('DATE'), _date_stats, "Date_Stats")

def SystemUptimeStats():
    _run_scheduled(get_interval('UPTIME'), _uptime_stats, "SystemUptime_Stats")

def CustomStats():
    _run_scheduled(get_interval('CUSTOM'), _custom_stats, "Custom_Stats")

def WeatherStats():
    _run_scheduled(get_interval('WEATHER'), _weather_stats, "Weather_Stats")

def PingStats():
    _run_scheduled(get_interval('PING'), _ping_stats, "Ping_Stats")

def QueueHandler():
    _run_scheduled(0.001, _queue_handler, "Queue_Handler")
