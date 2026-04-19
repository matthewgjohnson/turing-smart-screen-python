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

import os
import queue
import sys
from pathlib import Path
import yaml

from library.log import logger


def load_yaml(configfile):
    with open(configfile, "rt", encoding='utf8') as stream:
        yamlconfig = yaml.safe_load(stream)
        return yamlconfig


PATH = sys.path[0]
MAIN_DIRECTORY = Path(__file__).parent.parent.resolve()
FONTS_DIR = str(MAIN_DIRECTORY / "res" / "fonts") + "/"
CONFIG_DATA = load_yaml(MAIN_DIRECTORY / "config.yaml")
THEME_DEFAULT = load_yaml(MAIN_DIRECTORY / "res/themes/default.yaml")
THEME_DATA = None




def is_multi_display_mode() -> bool:
    """Check if multi-display configuration is present."""
    return 'multi-display' in CONFIG_DATA and CONFIG_DATA['multi-display']


def get_multi_display_configs() -> list:
    """Get list of display configurations for multi-display mode.

    Returns list of dicts with keys: DEVICE, THEME, REVISION, DISPLAY_REVERSE, BRIGHTNESS
    """
    if not is_multi_display_mode():
        return []
    return CONFIG_DATA['multi-display']


def parse_device_selector(device_value):
    """Parse device selector from config (can be int index or string serial)."""
    if isinstance(device_value, int):
        return device_value
    if isinstance(device_value, str):
        # Try to parse as integer
        try:
            return int(device_value)
        except ValueError:
            # Return as string for serial matching
            return device_value
    return device_value


def load_theme_for_display(theme_name: str) -> dict:
    """Load a specific theme by name and return its data."""
    try:
        theme_path = Path("res/themes/" + theme_name)
        logger.info("Loading theme %s from %s" % (theme_name, theme_path / "theme.yaml"))
        theme_data = load_yaml(MAIN_DIRECTORY / theme_path / "theme.yaml")
        theme_data['PATH'] = str(MAIN_DIRECTORY / theme_path) + "/"
        copy_default(THEME_DEFAULT, theme_data)
        return theme_data
    except Exception as e:
        logger.error(f"Theme {theme_name} not found or contains errors: {e}")
        return None


def copy_default(default, theme):
    """recursively supply default values into a dict of dicts of dicts ...."""
    for k, v in default.items():
        if k not in theme:
            theme[k] = v
        if type(v) == type({}):
            copy_default(default[k], theme[k])


def load_theme():
    global THEME_DATA
    try:
        theme_path = Path("res/themes/" + CONFIG_DATA['config']['THEME'])
        logger.info("Loading theme %s from %s" % (CONFIG_DATA['config']['THEME'], theme_path / "theme.yaml"))
        THEME_DATA = load_yaml(MAIN_DIRECTORY / theme_path / "theme.yaml")
        THEME_DATA['PATH'] = str(MAIN_DIRECTORY / theme_path) + "/"
    except:
        logger.error("Theme not found or contains errors!")
        try:
            sys.exit(0)
        except:
            os._exit(0)

    copy_default(THEME_DEFAULT, THEME_DATA)


def check_theme_compatible(display_size: str):
    # Check if theme is compatible with hardware revision
    if display_size != THEME_DATA['display'].get("DISPLAY_SIZE", '3.5"'):
        logger.error("The selected theme " + CONFIG_DATA['config'][
            'THEME'] + " is not compatible with your display revision " + CONFIG_DATA["display"]["REVISION"])
        try:
            sys.exit(0)
        except:
            os._exit(0)


# Load theme on import
load_theme()

# Queue containing the serial requests to send to the screen
update_queue = queue.Queue()


def get_display_mode(display_config: dict) -> str:
    """Determine display mode from config keys.

    Args:
        display_config: Dict with THEME, IMAGE, or VIDEO keys

    Returns:
        'stats' for THEME mode (default)
        'image' for IMAGE mode
        'video' for VIDEO mode

    Raises:
        ValueError if multiple modes specified
    """
    has_theme = bool('THEME' in display_config and display_config['THEME'])
    has_image = bool('IMAGE' in display_config and display_config['IMAGE'])
    has_video = bool('VIDEO' in display_config and display_config['VIDEO'])

    mode_count = sum([has_theme, has_image, has_video])
    if mode_count > 1:
        raise ValueError("THEME, IMAGE, VIDEO are mutually exclusive - only specify one per display")

    if has_image:
        return 'image'
    if has_video:
        return 'video'
    return 'stats'
