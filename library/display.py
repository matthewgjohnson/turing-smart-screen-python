# SPDX-License-Identifier: GPL-3.0-or-later
#
# turing-smart-screen-python - a Python system monitor and library for USB-C displays like Turing Smart Screen or XuanFang
# https://github.com/mathoudebine/turing-smart-screen-python/
#
# Copyright (C) 2021 Matthieu Houdebine (mathoudebine)
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

import atexit
import signal
import threading
from pathlib import Path
from typing import Optional
from library import config
from library.lcd.lcd_comm import Orientation
from library.lcd.lcd_comm_rev_a import LcdCommRevA
from library.lcd.lcd_comm_rev_b import LcdCommRevB
from library.lcd.lcd_comm_rev_c import LcdCommRevC
from library.lcd.lcd_comm_turing_usb import LcdCommTuringUSB, DISPLAY_WIDTH, DISPLAY_HEIGHT
from library.lcd.lcd_comm_rev_d import LcdCommRevD
from library.lcd.lcd_comm_weact_a import LcdCommWeActA
from library.lcd.lcd_comm_weact_b import LcdCommWeActB
from library.lcd.lcd_simulated import LcdSimulated
from library.log import logger


# =============================================================================
# Display Size Constants
# =============================================================================
DISPLAY_SIZES = {
    '0.96"': (80, 160),
    '2.1"': (480, 480),
    '3.5"': (320, 480),
    '5"': (480, 800),
    '8.8"': (480, 1920),
}
DEFAULT_DISPLAY_SIZE = (320, 480)


def _get_full_path(path, name):
    if name:
        return path + name
    else:
        return None


def _get_theme_orientation() -> Orientation:
    if config.THEME_DATA["display"]["DISPLAY_ORIENTATION"] == 'portrait':
        if config.CONFIG_DATA["display"].get("DISPLAY_REVERSE", False):
            return Orientation.REVERSE_PORTRAIT
        else:
            return Orientation.PORTRAIT
    elif config.THEME_DATA["display"]["DISPLAY_ORIENTATION"] == 'landscape':
        if config.CONFIG_DATA["display"].get("DISPLAY_REVERSE", False):
            return Orientation.REVERSE_LANDSCAPE
        else:
            return Orientation.LANDSCAPE
    else:
        logger.warning("Unknown orientation '%s', using portrait",
                      config.THEME_DATA["display"]["DISPLAY_ORIENTATION"])
        return Orientation.PORTRAIT


def _get_theme_size() -> tuple[int, int]:
    size_str = config.THEME_DATA["display"].get("DISPLAY_SIZE", '')
    if size_str in DISPLAY_SIZES:
        return DISPLAY_SIZES[size_str]
    logger.warning("Unknown display size '%s' in theme %s, defaulting to 3.5\"",
                  size_str, config.CONFIG_DATA["config"]["THEME"])
    return DEFAULT_DISPLAY_SIZE


def _get_theme_size_for_theme(theme_data) -> tuple[int, int]:
    """Get theme size from theme data dict."""
    size_str = theme_data["display"].get("DISPLAY_SIZE", '')
    if size_str in DISPLAY_SIZES:
        return DISPLAY_SIZES[size_str]
    logger.warning("Unknown display size '%s' in theme, defaulting to 3.5\"", size_str)
    return DEFAULT_DISPLAY_SIZE


def _get_theme_orientation_for_config(theme_data, display_config) -> Orientation:
    """Get orientation from theme data and display config."""
    orientation_str = theme_data["display"].get("DISPLAY_ORIENTATION", "portrait")
    display_reverse = display_config.get("DISPLAY_REVERSE", False)

    if orientation_str == 'portrait':
        return Orientation.REVERSE_PORTRAIT if display_reverse else Orientation.PORTRAIT
    elif orientation_str == 'landscape':
        return Orientation.REVERSE_LANDSCAPE if display_reverse else Orientation.LANDSCAPE
    else:
        logger.warning("Unknown orientation '%s', using portrait", orientation_str)
        return Orientation.PORTRAIT


def validate_config(display_config: dict, theme_data: Optional[dict] = None) -> list:
    """Validate display configuration.

    Args:
        display_config: Display config dict
        theme_data: Optional theme data dict

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    # Validate revision
    valid_revisions = ["A", "B", "C", "C_USB", "D", "WEACT_A", "WEACT_B", "SIMU"]
    revision = display_config.get("REVISION", "A")
    if revision not in valid_revisions:
        errors.append(f"Invalid REVISION '{revision}'. Valid options: {', '.join(valid_revisions)}")

    # Validate brightness
    brightness = display_config.get("BRIGHTNESS", 25)
    if not isinstance(brightness, int) or brightness < 0 or brightness > 100:
        errors.append(f"BRIGHTNESS must be 0-100, got: {brightness}")

    # Validate theme data if provided
    if theme_data:
        size_str = theme_data.get("display", {}).get("DISPLAY_SIZE", "")
        if size_str and size_str not in DISPLAY_SIZES:
            errors.append(f"Unknown DISPLAY_SIZE '{size_str}' in theme")

        orientation = theme_data.get("display", {}).get("DISPLAY_ORIENTATION", "")
        if orientation and orientation not in ["portrait", "landscape"]:
            errors.append(f"Unknown DISPLAY_ORIENTATION '{orientation}' in theme")

    return errors


def validate_paths(image_path: Optional[str] = None, video_path: Optional[str] = None) -> list:
    """Validate image/video file paths.

    Args:
        image_path: Optional image path to validate
        video_path: Optional video path to validate

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    if image_path:
        full_path = Path(config.MAIN_DIRECTORY) / image_path
        if not full_path.exists():
            errors.append(f"Image file not found: {full_path}")
        elif not full_path.suffix.lower() in ['.png', '.jpg', '.jpeg', '.bmp']:
            errors.append(f"Unsupported image format: {full_path.suffix}")

    if video_path:
        full_path = Path(config.MAIN_DIRECTORY) / video_path
        if not full_path.exists():
            errors.append(f"Video file not found: {full_path}")
        elif not full_path.suffix.lower() in ['.mp4', '.avi', '.mkv', '.mov']:
            errors.append(f"Unsupported video format: {full_path.suffix}")

    return errors


class Display:
    def __init__(self, display_config=None, theme_data=None, device_selector=None):
        """Initialize display.

        Args:
            display_config: Display config dict (REVISION, BRIGHTNESS, etc.)
                           If None, uses global CONFIG_DATA["display"]
            theme_data: Theme data dict. If None, uses global THEME_DATA
            device_selector: For C_USB revision, the device index/serial to use
        """
        self.lcd = None

        # Use provided config or fall back to global
        if display_config is None:
            display_config = config.CONFIG_DATA["display"]
        if theme_data is None:
            theme_data = config.THEME_DATA

        self.display_config = display_config
        self.theme_data = theme_data
        self.device_selector = device_selector

        # Mode tracking for image/video support
        self.mode = 'stats'  # 'stats', 'image', or 'video'
        self.video_thread = None
        self.video_stop_event = None
        self.image_path = None
        self.video_path = None

        # Validate config
        errors = validate_config(display_config, theme_data)
        for error in errors:
            logger.error("Config error: %s", error)

        width, height = _get_theme_size_for_theme(theme_data)
        revision = display_config.get("REVISION", "A")

        if revision == "A":
            self.lcd = LcdCommRevA(com_port=config.CONFIG_DATA['config']['COM_PORT'],
                                   update_queue=config.update_queue)
        elif revision == "B":
            self.lcd = LcdCommRevB(com_port=config.CONFIG_DATA['config']['COM_PORT'],
                                   update_queue=config.update_queue)
        elif revision == "C":
            self.lcd = LcdCommRevC(com_port=config.CONFIG_DATA['config']['COM_PORT'],
                                   update_queue=config.update_queue, display_width=width, display_height=height)
        elif revision == "C_USB":
            self.lcd = LcdCommTuringUSB(display_width=width, display_height=height,
                                        device_selector=device_selector)
        elif revision == "D":
            self.lcd = LcdCommRevD(com_port=config.CONFIG_DATA['config']['COM_PORT'],
                                   update_queue=config.update_queue)
        elif revision == "WEACT_A":
            self.lcd = LcdCommWeActA(com_port=config.CONFIG_DATA['config']['COM_PORT'],
                                   update_queue=config.update_queue)
        elif revision == "WEACT_B":
            self.lcd = LcdCommWeActB(com_port=config.CONFIG_DATA['config']['COM_PORT'],
                                   update_queue=config.update_queue)
        elif revision == "SIMU":
            self.lcd = LcdSimulated(display_width=width, display_height=height)
        else:
            logger.error("Unknown display revision '%s'", revision)

    def initialize_display(self):
        """Initialize the display hardware."""
        if self.lcd is None:
            logger.error("Cannot initialize display: lcd is None")
            return
        # Reset screen if configured
        if self.display_config.get("RESET_ON_STARTUP", True):
            self.lcd.Reset()
        else:
            logger.debug("RESET_ON_STARTUP disabled, skipping reset")

        self.lcd.InitializeComm()
        self.turn_on()

        orientation = _get_theme_orientation_for_config(self.theme_data, self.display_config)
        self.lcd.SetOrientation(orientation)

    def turn_on(self):
        """Turn on display and set brightness."""
        if self.lcd is None:
            return
        self.lcd.ScreenOn()
        self.lcd.SetBrightness(self.display_config.get("BRIGHTNESS", 25))
        self.lcd.SetBackplateLedColor(self.theme_data['display'].get("DISPLAY_RGB_LED", (255, 255, 255)))

    def turn_off(self):
        """Turn off display."""
        if self.lcd is None:
            return
        self.lcd.ScreenOff()
        self.lcd.SetBackplateLedColor(led_color=(0, 0, 0))

    def display_static_images(self):
        """Display static images defined in theme."""
        if self.lcd is None:
            return
        if self.theme_data.get('static_images', False):
            for image in self.theme_data['static_images']:
                logger.debug("Drawing Image: %s", image)
                self.lcd.DisplayBitmap(
                    bitmap_path=self.theme_data['PATH'] + self.theme_data['static_images'][image].get("PATH"),
                    x=self.theme_data['static_images'][image].get("X", 0),
                    y=self.theme_data['static_images'][image].get("Y", 0),
                    width=self.theme_data['static_images'][image].get("WIDTH", 0),
                    height=self.theme_data['static_images'][image].get("HEIGHT", 0)
                )

    def display_static_text(self):
        """Display static text defined in theme."""
        if self.lcd is None:
            return
        if self.theme_data.get('static_text', False):
            for text in self.theme_data['static_text']:
                logger.debug("Drawing Text: %s", text)
                self.lcd.DisplayText(
                    text=self.theme_data['static_text'][text].get("TEXT"),
                    x=self.theme_data['static_text'][text].get("X", 0),
                    y=self.theme_data['static_text'][text].get("Y", 0),
                    width=self.theme_data['static_text'][text].get("WIDTH", 0),
                    height=self.theme_data['static_text'][text].get("HEIGHT", 0),
                    font=config.FONTS_DIR + self.theme_data['static_text'][text].get("FONT",
                                                                                       "roboto-mono/RobotoMono-Regular.ttf"),
                    font_size=self.theme_data['static_text'][text].get("FONT_SIZE", 10),
                    font_color=self.theme_data['static_text'][text].get("FONT_COLOR", (0, 0, 0)),
                    background_color=self.theme_data['static_text'][text].get("BACKGROUND_COLOR", (255, 255, 255)),
                    background_image=_get_full_path(self.theme_data['PATH'],
                                                    self.theme_data['static_text'][text].get("BACKGROUND_IMAGE",
                                                                                               None)),
                    align=self.theme_data['static_text'][text].get("ALIGN", "left"),
                    anchor=self.theme_data['static_text'][text].get("ANCHOR", "lt"),
                )

    def show_image(self, image_path: str) -> bool:
        """Display a static image (uses layered sending for large images).

        Args:
            image_path: Path to image file (relative to MAIN_DIRECTORY)

        Returns:
            True if successful, False on error
        """
        self.mode = 'image'
        self.image_path = image_path

        # Validate path
        errors = validate_paths(image_path=image_path)
        for error in errors:
            logger.error(error)
            return False

        full_path = Path(config.MAIN_DIRECTORY) / image_path
        logger.info("Showing image: %s", full_path)
        if not isinstance(self.lcd, LcdCommTuringUSB):
            logger.error("show_image is only supported on LcdCommTuringUSB (C_USB revision)")
            return False
        return self.lcd.show_image(str(full_path))

    def show_video(self, video_path: str, brightness: int = 32, rotate_180: bool = False,
                   restart_minutes=None, reinit_minutes=None) -> bool:
        """Start video playback in a loop.

        Args:
            video_path: Path to MP4 file (relative to MAIN_DIRECTORY)
            brightness: Display brightness (0-100)
            rotate_180: If True, rotate video 180 degrees
            restart_minutes: Soft restart interval (reopen file, restart loop)
            reinit_minutes: Hard restart interval (re-send USB setup commands)

        Returns:
            True if started successfully, False on error
        """
        self.mode = 'video'
        self.video_path = video_path

        # Validate path
        errors = validate_paths(video_path=video_path)
        for error in errors:
            logger.error(error)
            return False

        full_path = Path(config.MAIN_DIRECTORY) / video_path
        logger.info("Starting video: %s (rotate_180=%s)", full_path, rotate_180)

        if not isinstance(self.lcd, LcdCommTuringUSB):
            logger.error("show_video is only supported on LcdCommTuringUSB (C_USB revision)")
            return False

        # Create stop event and start video thread
        self.video_stop_event = threading.Event()
        self.video_thread = threading.Thread(
            target=self.lcd.show_video,
            args=(str(full_path), self.video_stop_event, brightness, rotate_180,
                  restart_minutes, reinit_minutes),
            daemon=False
        )
        self.video_thread.start()
        return True

    def stop_video(self):
        """Stop video playback if running."""
        if self.mode != 'video':
            return

        if self.video_stop_event:
            logger.info("Stopping video...")
            self.video_stop_event.set()

        if self.video_thread and self.video_thread.is_alive():
            self.video_thread.join(timeout=5)

        if isinstance(self.lcd, LcdCommTuringUSB):
            self.lcd.stop_video()

    def shutdown(self):
        """Graceful shutdown - stop video and clear display."""
        logger.debug("Shutting down display")
        if self.mode == 'video':
            self.stop_video()
        if self.lcd:
            try:
                self.lcd.Clear()
            except Exception as e:
                logger.debug("Error clearing display on shutdown: %s", e)



# =============================================================================
# Display Registry
# =============================================================================
_all_displays = []


def register_display(disp):
    """Register a display instance."""
    _all_displays.append(disp)


def clear_displays():
    """Clear all registered displays."""
    _all_displays.clear()


def get_all_displays():
    """Get all registered displays."""
    if _all_displays:
        return _all_displays
    return [display]


def shutdown_all_displays():
    """Graceful shutdown of all displays."""
    logger.info("Shutting down all displays...")
    for disp in _all_displays:
        try:
            disp.shutdown()
        except Exception as e:
            logger.debug("Error shutting down display: %s", e)


# =============================================================================
# Multi-Display Support
# =============================================================================
class MultiDisplay:
    """Manages multiple Display instances for multi-display configurations."""

    def __init__(self):
        self.displays = {}  # device_id -> Display instance
        self._shutdown_registered = False

    def add_display(self, device_id, display_config, theme_data, device_selector):
        """Add a display instance."""
        logger.info("Adding display %s", device_id)
        self.displays[device_id] = Display(
            display_config=display_config,
            theme_data=theme_data,
            device_selector=device_selector
        )

    def initialize_all(self):
        """Initialize all displays."""
        for device_id, display in self.displays.items():
            logger.info("Initializing display %s", device_id)
            display.initialize_display()

    def display_static_content_all(self):
        """Display static content on all displays."""
        for device_id, display in self.displays.items():
            display.display_static_images()
            display.display_static_text()

    def turn_off_all(self):
        """Turn off all displays."""
        for device_id, display in self.displays.items():
            display.turn_off()

    def shutdown_all(self):
        """Graceful shutdown of all displays."""
        logger.info("Shutting down multi-display...")
        for device_id, disp in self.displays.items():
            try:
                disp.shutdown()
            except Exception as e:
                logger.debug("Error shutting down display %s: %s", device_id, e)

    def get_display(self, device_id):
        """Get a specific display by device ID."""
        return self.displays.get(device_id)

    def __iter__(self):
        """Iterate over all displays."""
        return iter(self.displays.values())

    def items(self):
        """Iterate over (device_id, display) pairs."""
        return self.displays.items()

    def register_shutdown_handler(self):
        """Register atexit and signal handlers for graceful shutdown."""
        if self._shutdown_registered:
            return

        atexit.register(self.shutdown_all)

        def signal_handler(signum, frame):
            logger.info("Received signal %d, shutting down...", signum)
            self.shutdown_all()

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        self._shutdown_registered = True


def create_multi_display() -> Optional[MultiDisplay]:
    """Create a MultiDisplay instance from config.

    Returns:
        MultiDisplay with all configured displays, or None if not in multi-display mode.
    """
    if not config.is_multi_display_mode():
        return None

    multi_display = MultiDisplay()

    for display_cfg in config.get_multi_display_configs():
        device_value = display_cfg.get('DEVICE', 0)
        device_selector = config.parse_device_selector(device_value)

        # Determine display mode
        mode = config.get_display_mode(display_cfg)

        # Build display config from multi-display entry
        display_config = {
            'REVISION': display_cfg.get('REVISION', 'C_USB'),
            'BRIGHTNESS': display_cfg.get('BRIGHTNESS', 25),
            'DISPLAY_REVERSE': display_cfg.get('DISPLAY_REVERSE', False),
            'RESET_ON_STARTUP': display_cfg.get('RESET_ON_STARTUP', True),
            'ORIENTATION': display_cfg.get('ORIENTATION', 'portrait'),
            'RESTART_MINUTES': display_cfg.get('RESTART_MINUTES'),
            'REINIT_MINUTES': display_cfg.get('REINIT_MINUTES'),
        }

        # Validate paths early
        if mode == 'image':
            image_path = display_cfg.get('IMAGE')
            errors = validate_paths(image_path=image_path)
            for error in errors:
                logger.error("Display %s: %s", device_value, error)
        elif mode == 'video':
            video_path = display_cfg.get('VIDEO')
            errors = validate_paths(video_path=video_path)
            for error in errors:
                logger.error("Display %s: %s", device_value, error)

        if mode == 'stats':
            # Stats mode: load theme
            theme_name = display_cfg.get('THEME', config.CONFIG_DATA['config']['THEME'])
            theme_data = config.load_theme_for_display(theme_name)
            if theme_data is None:
                logger.error("Failed to load theme '%s' for display %s", theme_name, device_value)
                continue
        else:
            # Image/video mode: use minimal theme data for 8.8" screen
            theme_data = {
                'display': {
                    'DISPLAY_SIZE': '8.8"',
                    'DISPLAY_ORIENTATION': 'portrait',
                },
                'PATH': str(config.MAIN_DIRECTORY) + '/'
            }

        disp = Display(
            display_config=display_config,
            theme_data=theme_data,
            device_selector=device_selector
        )
        disp.mode = mode

        if mode == 'image':
            disp.image_path = display_cfg.get('IMAGE')
        elif mode == 'video':
            disp.video_path = display_cfg.get('VIDEO')

        multi_display.displays[device_value] = disp

        # Only register stats displays for scheduler updates
        if mode == 'stats':
            register_display(disp)

    # Register shutdown handler
    multi_display.register_shutdown_handler()

    return multi_display


def initialize_multi_display(multi_display: MultiDisplay):
    """Initialize all displays in a MultiDisplay, handling different modes.

    Stats displays: normal initialization + static content
    Image displays: send image directly
    Video displays: start video loop thread (initialized last to avoid USB contention)
    """
    import time

    # Initialize in order: stats first, then image, then video (to avoid USB contention)
    video_displays = []

    for device_id, disp in multi_display.items():
        if disp.mode == 'stats':
            logger.info("Initializing display %s in stats mode", device_id)
            disp.initialize_display()
            disp.display_static_images()
            disp.display_static_text()
            time.sleep(1)  # Allow USB operations to complete
        elif disp.mode == 'image':
            logger.info("Initializing display %s in image mode", device_id)
            disp.initialize_display()
            disp.lcd.Clear()
            time.sleep(0.5)
            disp.show_image(disp.image_path)
            time.sleep(1)  # Allow USB operations to complete
        elif disp.mode == 'video':
            # Defer video to last
            video_displays.append((device_id, disp))

    # Initialize video displays last
    for device_id, disp in video_displays:
        logger.info("Initializing display %s in video mode", device_id)
        brightness = disp.display_config.get('BRIGHTNESS', 32)
        rotate_180 = disp.display_config.get('DISPLAY_REVERSE', False)
        restart_minutes = disp.display_config.get('RESTART_MINUTES')
        reinit_minutes = disp.display_config.get('REINIT_MINUTES')
        disp.show_video(disp.video_path, brightness, rotate_180, restart_minutes, reinit_minutes)


def stop_all_videos(multi_display: MultiDisplay):
    """Stop all video playback in a MultiDisplay."""
    if multi_display is None:
        return
    for device_id, disp in multi_display.items():
        if disp.mode == 'video':
            disp.stop_video()


# Global display instance (backwards compatibility)
display = Display()
