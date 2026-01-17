# SPDX-License-Identifier: GPL-3.0-or-later
#
# turing-smart-screen-python - a Python system monitor and library for USB-C displays like Turing Smart Screen or XuanFang
# https://github.com/mathoudebine/turing-smart-screen-python/
#
# Copyright (C) 2021 Matthieu Houdebine (mathoudebine)
# Copyright (C) 2022 Rollbacke
# Copyright (C) 2022 Ebag333
# Copyright (C) 2022 w1ld3r
# Copyright (C) 2022 Charles Ferguson (gerph)
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

import datetime
import locale
import math
import os
import platform
import sys
from typing import List

import babel.dates
import requests
try:
    from ping3 import ping
except ImportError:
    ping = None
from psutil._common import bytes2human
from uptime import uptime

import library.config as config
from library.display import display, get_all_displays
from library.log import logger

DEFAULT_HISTORY_SIZE = 10

ETH_CARD = config.CONFIG_DATA["config"].get("ETH", "")
WLO_CARD = config.CONFIG_DATA["config"].get("WLO", "")
HW_SENSORS = config.CONFIG_DATA["config"].get("HW_SENSORS", "AUTO")
CPU_FAN = config.CONFIG_DATA["config"].get("CPU_FAN", "AUTO")
PING_DEST = config.CONFIG_DATA["config"].get("PING", "127.0.0.1")

if HW_SENSORS == "PYTHON":
    if platform.system() == 'Windows':
        logger.warning("It is recommended to use LibreHardwareMonitor integration for Windows instead of Python "
                       "libraries (require admin. rights)")
    import library.sensors.sensors_python as sensors
elif HW_SENSORS == "LHM":
    if platform.system() == 'Windows':
        import library.sensors.sensors_librehardwaremonitor as sensors
    else:
        logger.error("LibreHardwareMonitor integration is only available on Windows")
        try:
            sys.exit(0)
        except:
            os._exit(0)
elif HW_SENSORS == "STUB":
    logger.warning("Stub sensors, not real HW sensors")
    import library.sensors.sensors_stub_random as sensors
elif HW_SENSORS == "STATIC":
    logger.warning("Stub sensors, not real HW sensors")
    import library.sensors.sensors_stub_static as sensors
elif HW_SENSORS == "AUTO":
    if platform.system() == 'Windows':
        import library.sensors.sensors_librehardwaremonitor as sensors
    else:
        import library.sensors.sensors_python as sensors
else:
    logger.error("Unsupported HW_SENSORS value in config.yaml")
    try:
        sys.exit(0)
    except:
        os._exit(0)

import library.sensors.sensors_custom as sensors_custom

from library.conditional import apply_case


# Multi-display helper functions
def _get_stat_theme_data(disp, *path):
    """Get theme data for a stat path from a display's theme."""
    data = disp.theme_data
    for key in path:
        data = data.get(key, {})
        if not data:
            return None
    return data


def _display_on(disp, theme_data, func_name, *args, **kwargs):
    """Call a display function on a specific display."""
    func = getattr(disp.lcd, func_name)
    func(*args, **kwargs)



def get_theme_file_path(name):
    if name:
        return os.path.join(config.THEME_DATA['PATH'], name)
    else:
        return None


def _get_display_context(disp):
    """Get lcd target and theme path for a display (or global fallback)."""
    if disp:
        return disp.lcd, disp.theme_data.get("PATH", "")
    return display.lcd, config.THEME_DATA.get("PATH", "")


def display_themed_value(theme_data, value, min_size=0, unit='', disp=None):
    """Display themed value. If disp is None, uses legacy global display."""
    if not theme_data or not theme_data.get("SHOW", False):
        return

    if value is None:
        return

    min_size = theme_data.get("MIN_SIZE", min_size)
    text = f"{{:>{min_size}}}".format(value)
    if theme_data.get("SHOW_UNIT", True) and unit:
        text += str(unit)

    target, theme_path = _get_display_context(disp)
    bg_image = theme_data.get("BACKGROUND_IMAGE")
    bg_path = os.path.join(theme_path, bg_image) if bg_image else None

    target.DisplayText(
        text=text,
        x=theme_data.get("X", 0),
        y=theme_data.get("Y", 0),
        width=theme_data.get("WIDTH", 0),
        height=theme_data.get("HEIGHT", 0),
        font=config.FONTS_DIR + theme_data.get("FONT", "roboto-mono/RobotoMono-Regular.ttf"),
        font_size=theme_data.get("FONT_SIZE", 10),
        font_color=theme_data.get("FONT_COLOR", (0, 0, 0)),
        background_color=theme_data.get("BACKGROUND_COLOR", (255, 255, 255)),
        background_image=bg_path,
        align=theme_data.get("ALIGN", "left"),
        anchor=theme_data.get("ANCHOR", "lt"),
        )


def display_themed_percent_value(theme_data, value, disp=None):
    display_themed_value(
        theme_data=theme_data,
        value=int(value) if value is not None else None,
        min_size=3,
        unit="%",
        disp=disp
    )


def display_themed_temperature_value(theme_data, value, disp=None):
    display_themed_value(
        theme_data=theme_data,
        value=int(value) if value is not None else None,
        min_size=3,
        unit="°C",
        disp=disp
    )


def display_themed_progress_bar(theme_data, value, disp=None):
    if not theme_data or not theme_data.get("SHOW", False):
        return

    target, theme_path = _get_display_context(disp)

    target.DisplayProgressBar(
        x=theme_data.get("X", 0),
        y=theme_data.get("Y", 0),
        width=theme_data.get("WIDTH", 0),
        height=theme_data.get("HEIGHT", 0),
        value=int(value),
        min_value=theme_data.get("MIN_VALUE", 0),
        max_value=theme_data.get("MAX_VALUE", 100),
        bar_color=theme_data.get("BAR_COLOR", (0, 0, 0)),
        bar_outline=theme_data.get("BAR_OUTLINE", False),
        background_color=theme_data.get("BACKGROUND_COLOR", (255, 255, 255)),
        background_image=get_theme_file_path(theme_data.get("BACKGROUND_IMAGE", None))
    )


def display_themed_radial_bar(theme_data, value, min_size=0, unit='', custom_text=None, disp=None):
    if not theme_data or not theme_data.get("SHOW", False):
        return

    if theme_data.get("SHOW_TEXT", False):
        if custom_text:
            text = custom_text
        else:
            text = f"{{:>{min_size}}}".format(value)
            if theme_data.get("SHOW_UNIT", True) and unit:
                text += str(unit)
    else:
        text = ""

    target, theme_path = _get_display_context(disp)
    target.DisplayRadialProgressBar(
        xc=theme_data.get("X", 0),
        yc=theme_data.get("Y", 0),
        radius=theme_data.get("RADIUS", 1),
        bar_width=theme_data.get("WIDTH", 1),
        min_value=theme_data.get("MIN_VALUE", 0),
        max_value=theme_data.get("MAX_VALUE", 100),
        angle_start=theme_data.get("ANGLE_START", 0),
        angle_end=theme_data.get("ANGLE_END", 360),
        angle_steps=theme_data.get("ANGLE_STEPS", 1),
        angle_sep=theme_data.get("ANGLE_SEP", 0),
        clockwise=theme_data.get("CLOCKWISE", False),
        value=value,
        bar_color=theme_data.get("BAR_COLOR", (0, 0, 0)),
        text=text,
        font=config.FONTS_DIR + theme_data.get("FONT", "roboto-mono/RobotoMono-Regular.ttf"),
        font_size=theme_data.get("FONT_SIZE", 10),
        font_color=theme_data.get("FONT_COLOR", (0, 0, 0)),
        background_color=theme_data.get("BACKGROUND_COLOR", (0, 0, 0)),
        background_image=os.path.join(theme_path, theme_data.get("BACKGROUND_IMAGE", "")) if theme_data.get("BACKGROUND_IMAGE") else None,
        custom_bbox=theme_data.get("CUSTOM_BBOX", (0, 0, 0, 0)),
        text_offset=theme_data.get("TEXT_OFFSET", (0, 0)),
        bar_background_color=theme_data.get("BAR_BACKGROUND_COLOR", (0, 0, 0)),
        draw_bar_background=theme_data.get("DRAW_BAR_BACKGROUND", False),
        bar_decoration=theme_data.get("BAR_DECORATION", "")
    )


def display_themed_percent_radial_bar(theme_data, value, disp=None):
    display_themed_radial_bar(
        theme_data=theme_data,
        value=int(value) if value is not None else 0,
        unit="%",
        min_size=3,
        disp=disp
    )


def display_themed_temperature_radial_bar(theme_data, value, disp=None):
    display_themed_radial_bar(
        theme_data=theme_data,
        value=int(value) if value is not None else None,
        min_size=3,
        unit="°C",
        disp=disp
    )


def display_themed_line_graph(theme_data, values, disp=None):
    if not theme_data or not theme_data.get("SHOW", False):
        return

    line_color = theme_data.get("LINE_COLOR", (0, 0, 0))
    target, theme_path = _get_display_context(disp)

    target.DisplayLineGraph(
        x=theme_data.get("X", 0),
        y=theme_data.get("Y", 0),
        width=theme_data.get("WIDTH", 1),
        height=theme_data.get("HEIGHT", 1),
        values=values,
        min_value=theme_data.get("MIN_VALUE", 0),
        max_value=theme_data.get("MAX_VALUE", 100),
        autoscale=theme_data.get("AUTOSCALE", False),
        line_color=line_color,
        line_width=theme_data.get("LINE_WIDTH", 2),
        graph_axis=theme_data.get("AXIS", False),
        axis_color=theme_data.get("AXIS_COLOR", line_color),  # If no color specified, use line color for axis
        axis_font=config.FONTS_DIR + theme_data.get("AXIS_FONT", "roboto/Roboto-Black.ttf"),
        axis_font_size=theme_data.get("AXIS_FONT_SIZE", 10),
        background_color=theme_data.get("BACKGROUND_COLOR", (0, 0, 0)),
        background_image=get_theme_file_path(theme_data.get("BACKGROUND_IMAGE", None))
    )


def save_last_value(value: float, last_values: List[float], history_size: int):
    # Initialize last values list the first time with given size
    if len(last_values) != history_size:
        last_values[:] = last_values_list(size=history_size)
    # Store the value to the list that can then be used for line graph
    last_values.append(value)
    # Also remove the oldest value from list
    last_values.pop(0)


def last_values_list(size: int) -> List[float]:
    return [math.nan] * size


class CPU:
    last_values_cpu_percentage = []
    last_values_cpu_temperature = []
    last_values_cpu_fan_speed = []
    last_values_cpu_frequency = []

    @classmethod
    def percentage(cls):
        # Get CPU percentage once
        cpu_percentage = sensors.Cpu.percentage(interval=None)

        # Update on each display
        for disp in get_all_displays():
            theme_data = _get_stat_theme_data(disp, 'STATS', 'CPU', 'PERCENTAGE')
            if not theme_data:
                continue

            save_last_value(cpu_percentage, cls.last_values_cpu_percentage,
                            theme_data.get('LINE_GRAPH', {}).get("HISTORY_SIZE", DEFAULT_HISTORY_SIZE))

            display_themed_progress_bar(theme_data.get('GRAPH'), cpu_percentage, disp)
            display_themed_percent_radial_bar(theme_data.get('RADIAL'), cpu_percentage, disp)
            display_themed_percent_value(theme_data.get('TEXT'), cpu_percentage, disp)
            display_themed_line_graph(theme_data.get('LINE_GRAPH'), cls.last_values_cpu_percentage, disp)

    @classmethod
    def frequency(cls):
        freq_ghz = sensors.Cpu.frequency() / 1000

        for disp in get_all_displays():
            theme_data = _get_stat_theme_data(disp, 'STATS', 'CPU', 'FREQUENCY')
            if not theme_data:
                continue

            save_last_value(freq_ghz, cls.last_values_cpu_frequency,
                            theme_data.get('LINE_GRAPH', {}).get("HISTORY_SIZE", DEFAULT_HISTORY_SIZE))

            display_themed_value(theme_data.get('TEXT'), f'{freq_ghz:.2f}', unit=" GHz", min_size=4, disp=disp)
            display_themed_progress_bar(theme_data.get('GRAPH'), freq_ghz, disp)
            display_themed_radial_bar(theme_data.get('RADIAL'), f'{freq_ghz:.2f}', unit=" GHz", min_size=4, disp=disp)
            display_themed_line_graph(theme_data.get('LINE_GRAPH'), cls.last_values_cpu_frequency, disp)

    @classmethod
    def load(cls):
        cpu_load = sensors.Cpu.load()

        for disp in get_all_displays():
            load_theme_data = _get_stat_theme_data(disp, 'STATS', 'CPU', 'LOAD')
            if not load_theme_data:
                continue

            display_themed_percent_value(load_theme_data.get('ONE', {}).get('TEXT'), cpu_load[0], disp)
            display_themed_percent_value(load_theme_data.get('FIVE', {}).get('TEXT'), cpu_load[1], disp)
            display_themed_percent_value(load_theme_data.get('FIFTEEN', {}).get('TEXT'), cpu_load[2], disp)

    @classmethod
    def temperature(cls):
        temperature = sensors.Cpu.temperature()
        if math.isnan(temperature):
            temperature = 0

        for disp in get_all_displays():
            theme_data = _get_stat_theme_data(disp, 'STATS', 'CPU', 'TEMPERATURE')
            if not theme_data:
                continue

            save_last_value(temperature, cls.last_values_cpu_temperature,
                            theme_data.get('LINE_GRAPH', {}).get("HISTORY_SIZE", DEFAULT_HISTORY_SIZE))

            display_themed_temperature_value(theme_data.get('TEXT'), temperature, disp)
            display_themed_progress_bar(theme_data.get('GRAPH'), temperature, disp)
            display_themed_temperature_radial_bar(theme_data.get('RADIAL'), temperature, disp)
            display_themed_line_graph(theme_data.get('LINE_GRAPH'), cls.last_values_cpu_temperature, disp)

    @classmethod
    def fan_speed(cls):
        if CPU_FAN != "AUTO":
            fan_percent = sensors.Cpu.fan_percent(CPU_FAN)
        else:
            fan_percent = sensors.Cpu.fan_percent()

        if math.isnan(fan_percent):
            fan_percent = 0

        for disp in get_all_displays():
            theme_data = _get_stat_theme_data(disp, 'STATS', 'CPU', 'FAN_SPEED')
            if not theme_data:
                continue

            save_last_value(fan_percent, cls.last_values_cpu_fan_speed,
                            theme_data.get('LINE_GRAPH', {}).get("HISTORY_SIZE", DEFAULT_HISTORY_SIZE))

            display_themed_percent_value(theme_data.get('TEXT'), fan_percent, disp)
            display_themed_progress_bar(theme_data.get('GRAPH'), fan_percent, disp)
            display_themed_percent_radial_bar(theme_data.get('RADIAL'), fan_percent, disp)
            display_themed_line_graph(theme_data.get('LINE_GRAPH'), cls.last_values_cpu_fan_speed, disp)


class Gpu:
    last_values_gpu_percentage = []
    last_values_gpu_mem_percentage = []
    last_values_gpu_temperature = []
    last_values_gpu_fps = []
    last_values_gpu_fan_speed = []
    last_values_gpu_frequency = []

    @classmethod
    def stats(cls):
        # Get sensor values once
        load, memory_percentage, memory_used_mb, total_memory_mb, temperature = sensors.Gpu.stats()
        fps = sensors.Gpu.fps()
        fan_percent = sensors.Gpu.fan_percent()
        freq_ghz = sensors.Gpu.frequency() / 1000

        # Handle NaN values
        if math.isnan(load):
            load = 0
        if math.isnan(memory_percentage):
            memory_percentage = 0
        if math.isnan(memory_used_mb):
            memory_used_mb = 0
        if math.isnan(total_memory_mb):
            total_memory_mb = 0
        if math.isnan(temperature):
            temperature = 0
        if fps < 0 or math.isnan(fps):
            fps = 0
        if math.isnan(fan_percent):
            fan_percent = 0

        # Update each display
        for disp in get_all_displays():
            theme_gpu_data = _get_stat_theme_data(disp, 'STATS', 'GPU')
            if not theme_gpu_data:
                continue

            # Save history values
            save_last_value(load, cls.last_values_gpu_percentage,
                            theme_gpu_data.get('PERCENTAGE', {}).get('LINE_GRAPH', {}).get("HISTORY_SIZE", DEFAULT_HISTORY_SIZE))
            save_last_value(memory_percentage, cls.last_values_gpu_mem_percentage,
                            theme_gpu_data.get('MEMORY_PERCENT', {}).get('LINE_GRAPH', {}).get("HISTORY_SIZE", DEFAULT_HISTORY_SIZE))
            save_last_value(temperature, cls.last_values_gpu_temperature,
                            theme_gpu_data.get('TEMPERATURE', {}).get('LINE_GRAPH', {}).get("HISTORY_SIZE", DEFAULT_HISTORY_SIZE))
            save_last_value(fps, cls.last_values_gpu_fps,
                            theme_gpu_data.get('FPS', {}).get('LINE_GRAPH', {}).get("HISTORY_SIZE", DEFAULT_HISTORY_SIZE))
            save_last_value(fan_percent, cls.last_values_gpu_fan_speed,
                            theme_gpu_data.get('FAN_SPEED', {}).get('LINE_GRAPH', {}).get("HISTORY_SIZE", DEFAULT_HISTORY_SIZE))
            save_last_value(freq_ghz, cls.last_values_gpu_frequency,
                            theme_gpu_data.get('FREQUENCY', {}).get('LINE_GRAPH', {}).get("HISTORY_SIZE", DEFAULT_HISTORY_SIZE))

            # GPU usage (%)
            pct = theme_gpu_data.get('PERCENTAGE', {})
            display_themed_progress_bar(pct.get('GRAPH'), load, disp)
            display_themed_percent_radial_bar(pct.get('RADIAL'), load, disp)
            display_themed_percent_value(pct.get('TEXT'), load, disp)
            display_themed_line_graph(pct.get('LINE_GRAPH'), cls.last_values_gpu_percentage, disp)

            # GPU mem usage (%) - new style
            mem_pct = theme_gpu_data.get('MEMORY_PERCENT', {})
            display_themed_progress_bar(mem_pct.get('GRAPH'), memory_percentage, disp)
            display_themed_percent_radial_bar(mem_pct.get('RADIAL'), memory_percentage, disp)
            display_themed_percent_value(mem_pct.get('TEXT'), memory_percentage, disp)
            display_themed_line_graph(mem_pct.get('LINE_GRAPH'), cls.last_values_gpu_mem_percentage, disp)

            # GPU mem (M) - backward compat
            mem = theme_gpu_data.get('MEMORY', {})
            display_themed_progress_bar(mem.get('GRAPH'), memory_percentage, disp)
            display_themed_percent_radial_bar(mem.get('RADIAL'), memory_percentage, disp)
            display_themed_value(mem.get('TEXT'), int(memory_used_mb), min_size=5, unit=" M", disp=disp)

            # GPU mem used (M)
            mem_used = theme_gpu_data.get('MEMORY_USED', {})
            display_themed_value(mem_used.get('TEXT'), int(memory_used_mb), min_size=5, unit=" M", disp=disp)

            # GPU mem total (M)
            mem_total = theme_gpu_data.get('MEMORY_TOTAL', {})
            display_themed_value(mem_total.get('TEXT'), int(total_memory_mb), min_size=5, unit=" M", disp=disp)

            # GPU temperature
            temp = theme_gpu_data.get('TEMPERATURE', {})
            display_themed_temperature_value(temp.get('TEXT'), temperature, disp)
            display_themed_progress_bar(temp.get('GRAPH'), temperature, disp)
            display_themed_temperature_radial_bar(temp.get('RADIAL'), temperature, disp)
            display_themed_line_graph(temp.get('LINE_GRAPH'), cls.last_values_gpu_temperature, disp)

            # GPU FPS
            fps_data = theme_gpu_data.get('FPS', {})
            display_themed_progress_bar(fps_data.get('GRAPH'), fps, disp)
            display_themed_value(fps_data.get('TEXT'), int(fps), min_size=4, unit=" FPS", disp=disp)
            display_themed_radial_bar(fps_data.get('RADIAL'), int(fps), min_size=4, unit=" FPS", disp=disp)
            display_themed_line_graph(fps_data.get('LINE_GRAPH'), cls.last_values_gpu_fps, disp)

            # GPU Fan Speed (%)
            fan = theme_gpu_data.get('FAN_SPEED', {})
            display_themed_percent_value(fan.get('TEXT'), fan_percent, disp)
            display_themed_progress_bar(fan.get('GRAPH'), fan_percent, disp)
            display_themed_percent_radial_bar(fan.get('RADIAL'), fan_percent, disp)
            display_themed_line_graph(fan.get('LINE_GRAPH'), cls.last_values_gpu_fan_speed, disp)

            # GPU Frequency (GHz)
            freq = theme_gpu_data.get('FREQUENCY', {})
            display_themed_value(freq.get('TEXT'), f'{freq_ghz:.2f}', unit=" GHz", min_size=4, disp=disp)
            display_themed_progress_bar(freq.get('GRAPH'), freq_ghz, disp)
            display_themed_radial_bar(freq.get('RADIAL'), f'{freq_ghz:.2f}', unit=" GHz", min_size=4, disp=disp)
            display_themed_line_graph(freq.get('LINE_GRAPH'), cls.last_values_gpu_frequency, disp)

    @staticmethod
    def is_available():
        return sensors.Gpu.is_available()


class Memory:
    last_values_memory_swap = []
    last_values_memory_virtual = []

    @classmethod
    def stats(cls):
        # Get sensor values once
        swap_percent = sensors.Memory.swap_percent()
        virtual_percent = sensors.Memory.virtual_percent()
        virtual_used = int(sensors.Memory.virtual_used() / 1024 ** 2)
        virtual_free = int(sensors.Memory.virtual_free() / 1024 ** 2)
        virtual_total = virtual_used + virtual_free

        for disp in get_all_displays():
            theme_data = _get_stat_theme_data(disp, 'STATS', 'MEMORY')
            if not theme_data:
                continue

            # Swap
            swap = theme_data.get('SWAP', {})
            save_last_value(swap_percent, cls.last_values_memory_swap,
                            swap.get('LINE_GRAPH', {}).get("HISTORY_SIZE", DEFAULT_HISTORY_SIZE))
            display_themed_progress_bar(swap.get('GRAPH'), swap_percent, disp)
            display_themed_percent_radial_bar(swap.get('RADIAL'), swap_percent, disp)
            display_themed_line_graph(swap.get('LINE_GRAPH'), cls.last_values_memory_swap, disp)

            # Virtual
            virtual = theme_data.get('VIRTUAL', {})
            save_last_value(virtual_percent, cls.last_values_memory_virtual,
                            virtual.get('LINE_GRAPH', {}).get("HISTORY_SIZE", DEFAULT_HISTORY_SIZE))
            display_themed_progress_bar(virtual.get('GRAPH'), virtual_percent, disp)
            display_themed_percent_radial_bar(virtual.get('RADIAL'), virtual_percent, disp)
            display_themed_percent_value(virtual.get('PERCENT_TEXT'), virtual_percent, disp)
            display_themed_line_graph(virtual.get('LINE_GRAPH'), cls.last_values_memory_virtual, disp)

            display_themed_value(virtual.get('USED'), virtual_used, min_size=5, unit=" M", disp=disp)
            display_themed_value(virtual.get('FREE'), virtual_free, min_size=5, unit=" M", disp=disp)
            display_themed_value(virtual.get('TOTAL'), virtual_total, min_size=5, unit=" M", disp=disp)


class Disk:
    last_values_disk_usage = []

    @classmethod
    def stats(cls):
        # Get sensor values once
        used = sensors.Disk.disk_used()
        free = sensors.Disk.disk_free()
        disk_usage_percent = sensors.Disk.disk_usage_percent()
        used_gb = int(used / 1000000000)
        free_gb = int(free / 1000000000)
        total_gb = int((free + used) / 1000000000)

        for disp in get_all_displays():
            theme_data = _get_stat_theme_data(disp, 'STATS', 'DISK')
            if not theme_data:
                continue

            used_data = theme_data.get('USED', {})
            save_last_value(disk_usage_percent, cls.last_values_disk_usage,
                            used_data.get('LINE_GRAPH', {}).get("HISTORY_SIZE", DEFAULT_HISTORY_SIZE))
            display_themed_progress_bar(used_data.get('GRAPH'), disk_usage_percent, disp)
            display_themed_percent_radial_bar(used_data.get('RADIAL'), disk_usage_percent, disp)
            pct_text_data = used_data.get('PERCENT_TEXT')
            display_themed_percent_value(pct_text_data, disk_usage_percent, disp)
            display_themed_line_graph(used_data.get('LINE_GRAPH'), cls.last_values_disk_usage, disp)
            display_themed_value(used_data.get('TEXT'), used_gb, min_size=5, unit=" G", disp=disp)

            display_themed_value(theme_data.get('TOTAL', {}).get('TEXT'), total_gb, min_size=5, unit=" G", disp=disp)
            display_themed_value(theme_data.get('FREE', {}).get('TEXT'), free_gb, min_size=5, unit=" G", disp=disp)


class Net:
    last_values_wlo_upload = []
    last_values_wlo_download = []
    last_values_eth_upload = []
    last_values_eth_download = []

    @classmethod
    def stats(cls):
        # Get sensor values once (interval taken from first display or default)
        interval = None
        upload_wlo, uploaded_wlo, download_wlo, downloaded_wlo = sensors.Net.stats(WLO_CARD, interval)
        upload_eth, uploaded_eth, download_eth, downloaded_eth = sensors.Net.stats(ETH_CARD, interval)

        for disp in get_all_displays():
            net_theme_data = _get_stat_theme_data(disp, 'STATS', 'NET')
            if not net_theme_data:
                continue

            # WLO Upload
            wlo = net_theme_data.get('WLO', {})
            wlo_up = wlo.get('UPLOAD', {})
            save_last_value(upload_wlo, cls.last_values_wlo_upload,
                            wlo_up.get('LINE_GRAPH', {}).get("HISTORY_SIZE", DEFAULT_HISTORY_SIZE))
            Net._show_themed_tax_rate(wlo_up.get('TEXT'), upload_wlo, disp)
            Net._show_themed_total_data(wlo.get('UPLOADED', {}).get('TEXT'), uploaded_wlo, disp)
            display_themed_line_graph(wlo_up.get('LINE_GRAPH'), cls.last_values_wlo_upload, disp)

            # WLO Download
            wlo_down = wlo.get('DOWNLOAD', {})
            save_last_value(download_wlo, cls.last_values_wlo_download,
                            wlo_down.get('LINE_GRAPH', {}).get("HISTORY_SIZE", DEFAULT_HISTORY_SIZE))
            Net._show_themed_tax_rate(wlo_down.get('TEXT'), download_wlo, disp)
            Net._show_themed_total_data(wlo.get('DOWNLOADED', {}).get('TEXT'), downloaded_wlo, disp)
            display_themed_line_graph(wlo_down.get('LINE_GRAPH'), cls.last_values_wlo_download, disp)

            # ETH Upload
            eth = net_theme_data.get('ETH', {})
            eth_up = eth.get('UPLOAD', {})
            save_last_value(upload_eth, cls.last_values_eth_upload,
                            eth_up.get('LINE_GRAPH', {}).get("HISTORY_SIZE", DEFAULT_HISTORY_SIZE))
            Net._show_themed_tax_rate(eth_up.get('TEXT'), upload_eth, disp)
            Net._show_themed_total_data(eth.get('UPLOADED', {}).get('TEXT'), uploaded_eth, disp)
            display_themed_line_graph(eth_up.get('LINE_GRAPH'), cls.last_values_eth_upload, disp)

            # ETH Download
            eth_down = eth.get('DOWNLOAD', {})
            save_last_value(download_eth, cls.last_values_eth_download,
                            eth_down.get('LINE_GRAPH', {}).get("HISTORY_SIZE", DEFAULT_HISTORY_SIZE))
            Net._show_themed_tax_rate(eth_down.get('TEXT'), download_eth, disp)
            Net._show_themed_total_data(eth.get('DOWNLOADED', {}).get('TEXT'), downloaded_eth, disp)
            display_themed_line_graph(eth_down.get('LINE_GRAPH'), cls.last_values_eth_download, disp)

    @staticmethod
    def _show_themed_total_data(theme_data, amount, disp=None):
        display_themed_value(
            theme_data=theme_data,
            value=f"{bytes2human(amount)}",
            min_size=6,
            disp=disp
        )

    @staticmethod
    def _show_themed_tax_rate(theme_data, rate, disp=None):
        display_themed_value(
            theme_data=theme_data,
            value=f"{bytes2human(rate, '%(value).1f %(symbol)s/s')}",
            min_size=10,
            disp=disp
        )


class Date:
    @staticmethod
    def stats():
        if HW_SENSORS == "STATIC":
            date_now = datetime.datetime.fromtimestamp(1694014609)
        else:
            date_now = datetime.datetime.now()

        try:
            if platform.system() == "Windows":
                lc_time = locale.getdefaultlocale()[0]
            else:
                lc_time = babel.dates.LC_TIME
        except:
            lc_time = None

        if not lc_time:
            lc_time = "en_US"

        for disp in get_all_displays():
            date_theme_data = _get_stat_theme_data(disp, 'STATS', 'DATE')
            if not date_theme_data:
                continue

            day_theme_data = date_theme_data.get('DAY', {}).get('TEXT', {})
            date_format = day_theme_data.get("FORMAT", 'medium')
            display_themed_value(
                theme_data=day_theme_data,
                value=f"{babel.dates.format_date(date_now, format=date_format, locale=lc_time)}",
                disp=disp
            )

            hour_theme_data = date_theme_data.get('HOUR', {}).get('TEXT', {})
            time_format = hour_theme_data.get("FORMAT", 'medium')
            display_themed_value(
                theme_data=hour_theme_data,
                value=f"{babel.dates.format_time(date_now, format=time_format, locale=lc_time)}",
                disp=disp
            )


class SystemUptime:
    @staticmethod
    def stats():
        if HW_SENSORS == "STATIC":
            uptimesec = 4294036
        else:
            uptimesec = int(uptime())

        uptimeformatted = str(datetime.timedelta(seconds=uptimesec))

        for disp in get_all_displays():
            theme_data = _get_stat_theme_data(disp, 'STATS', 'UPTIME')
            if not theme_data:
                continue

            display_themed_value(theme_data.get('SECONDS', {}).get('TEXT'), uptimesec, disp=disp)
            display_themed_value(theme_data.get('FORMATTED', {}).get('TEXT'), uptimeformatted, disp=disp)


class Custom:
    @staticmethod
    def stats():
        # Custom sensors - need to iterate per display
        for disp in get_all_displays():
            custom_theme = _get_stat_theme_data(disp, 'STATS', 'CUSTOM')
            if not custom_theme:
                continue

            for custom_stat in custom_theme:
                if custom_stat == "INTERVAL":
                    continue

                try:
                    custom_stat_class = getattr(sensors_custom, str(custom_stat))()
                    numeric_value = custom_stat_class.as_numeric()
                    string_value = custom_stat_class.as_string()
                    last_values = custom_stat_class.last_values()
                except Exception as e:
                    logger.error(f"Error loading custom sensor class {custom_stat}: {e}")
                    continue

                if string_value is None:
                    string_value = str(numeric_value) if numeric_value is not None else None

                stat_data = custom_theme.get(custom_stat, {})

                # Display text (with CASE conditional support)
                text_data = stat_data.get("TEXT")
                if text_data:
                    text_data = apply_case(text_data)
                    # Empty TEXT means don't render
                    if text_data.get("TEXT", None) == "":
                        pass  # Skip rendering
                    elif string_value is not None or text_data.get("TEXT"):
                        # Use TEXT from conditional override if present, else sensor value
                        display_value = text_data.get("TEXT", string_value)
                        display_themed_value(theme_data=text_data, value=display_value, disp=disp)

                # Display graph from numeric value (with CASE conditional support)
                graph_data = stat_data.get("GRAPH")
                if graph_data:
                    graph_data = apply_case(graph_data)
                    if graph_data.get("SHOW", True) and numeric_value is not None and not math.isnan(numeric_value):
                        display_themed_progress_bar(theme_data=graph_data, value=numeric_value, disp=disp)

                # Display radial from numeric and text value (with CASE conditional support)
                radial_data = stat_data.get("RADIAL")
                if radial_data:
                    radial_data = apply_case(radial_data)
                    if radial_data.get("SHOW", True) and numeric_value is not None and not math.isnan(numeric_value) and string_value:
                        display_themed_radial_bar(theme_data=radial_data, value=numeric_value, custom_text=string_value, disp=disp)

                # Display plot graph from histo values
                line_data = stat_data.get("LINE_GRAPH")
                if line_data and last_values is not None:
                    display_themed_line_graph(theme_data=line_data, values=last_values, disp=disp)


class Weather:
    @staticmethod
    def stats():
        WEATHER_UNITS = {'metric': '°C', 'imperial': '°F', 'standard': '°K'}

        # Check if any display has weather configured
        has_weather = False
        for disp in get_all_displays():
            weather_data = _get_stat_theme_data(disp, 'STATS', 'WEATHER')
            if weather_data:
                has_weather = True
                break

        if not has_weather:
            return

        # Get weather data once
        temp = None
        feel = None
        time = None
        humidity = None
        desc = None

        if HW_SENSORS in ["STATIC", "STUB"]:
            temp = "17.5°C"
            feel = "(17.2°C)"
            desc = "Cloudy"
            time = "@15:33"
            humidity = "45%"
        else:
            lat = config.CONFIG_DATA['config'].get('WEATHER_LATITUDE', "")
            lon = config.CONFIG_DATA['config'].get('WEATHER_LONGITUDE', "")
            api_key = config.CONFIG_DATA['config'].get('WEATHER_API_KEY', "")
            units = config.CONFIG_DATA['config'].get('WEATHER_UNITS', "metric")
            lang = config.CONFIG_DATA['config'].get('WEATHER_LANGUAGE', "en")
            deg = WEATHER_UNITS.get(units, '°?')
            if api_key:
                url = f'https://api.openweathermap.org/data/3.0/onecall?lat={lat}&lon={lon}&exclude=minutely,hourly,daily,alerts&appid={api_key}&units={units}&lang={lang}'
                try:
                    response = requests.get(url)
                    if response.status_code == 200:
                        data = response.json()
                        temp = f"{data['current']['temp']:.1f}{deg}"
                        feel = f"({data['current']['feels_like']:.1f}{deg})"
                        desc = data['current']['weather'][0]['description'].capitalize()
                        humidity = f"{data['current']['humidity']:.0f}%"
                        now = datetime.datetime.now()
                        time = f"@{now.hour:02d}:{now.minute:02d}"
                    else:
                        desc = response.json().get('message', 'API Error')
                except Exception as e:
                    logger.error(f"Error fetching OpenWeatherMap API: {str(e)}")
                    desc = "Error fetching API"
            else:
                desc = "No API key"

        # Display on each configured screen
        for disp in get_all_displays():
            weather_data = _get_stat_theme_data(disp, 'STATS', 'WEATHER')
            if not weather_data:
                continue

            display_themed_value(weather_data.get('TEMPERATURE', {}).get('TEXT'), temp, disp=disp)
            display_themed_value(weather_data.get('TEMPERATURE_FELT', {}).get('TEXT'), feel, disp=disp)
            display_themed_value(weather_data.get('UPDATE_TIME', {}).get('TEXT'), time, disp=disp)
            display_themed_value(weather_data.get('HUMIDITY', {}).get('TEXT'), humidity, disp=disp)
            display_themed_value(weather_data.get('WEATHER_DESCRIPTION', {}).get('TEXT'), desc, disp=disp)

class Ping:
    last_values_ping = []

    @classmethod
    def stats(cls):
        delay = ping(dest_addr=PING_DEST, unit="ms")
        if delay is None:
            delay = 0

        for disp in get_all_displays():
            theme_data = _get_stat_theme_data(disp, 'STATS', 'PING')
            if not theme_data:
                continue

            save_last_value(delay, cls.last_values_ping,
                            theme_data.get('LINE_GRAPH', {}).get("HISTORY_SIZE", DEFAULT_HISTORY_SIZE))

            display_themed_progress_bar(theme_data.get('GRAPH'), delay, disp)
            display_themed_radial_bar(theme_data.get('RADIAL'), int(delay), unit="ms", min_size=6, disp=disp)
            display_themed_value(theme_data.get('TEXT'), int(delay), unit="ms", min_size=6, disp=disp)
            display_themed_line_graph(theme_data.get('LINE_GRAPH'), cls.last_values_ping, disp)
