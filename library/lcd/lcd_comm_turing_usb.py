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

import json
import math
import os
import platform
import queue
import struct
import subprocess
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import Optional

import usb.core
import usb.util
from Crypto.Cipher import DES
from PIL import Image

from library.log import logger
from library.lcd.lcd_comm import Orientation, LcdComm

# =============================================================================
# USB Device Constants
# =============================================================================
VENDOR_ID = 0x1CBE
PRODUCT_ID = 0x0088

# =============================================================================
# Protocol Constants - Command IDs
# =============================================================================
CMD_SYNC = 10
CMD_RESTART = 11
CMD_UNKNOWN_13 = 13  # Used in video setup
CMD_BRIGHTNESS = 14
CMD_FRAME_RATE = 15
CMD_OPEN_FILE = 38
CMD_WRITE_FILE = 39
CMD_DELETE_FILE = 40
CMD_UNKNOWN_41 = 41  # Used in video setup
CMD_PLAY = 98
CMD_REFRESH_STORAGE = 100
CMD_SEND_IMAGE = 102
CMD_PLAY_ALT = 110
CMD_UNKNOWN_111 = 111  # Video setup/stop
CMD_UNKNOWN_112 = 112  # Video setup/stop
CMD_PLAY_IMAGE = 113
CMD_SEND_VIDEO_CHUNK = 121
CMD_DELAY = 122
CMD_VIDEO_STOP = 123
CMD_SAVE_SETTINGS = 125

# =============================================================================
# Transfer Constants
# =============================================================================
MAX_CHUNK_BYTES = 1024 * 1024  # 1MB max transfer size
VIDEO_CHUNK_SIZE = 202752  # ~202KB per video chunk
IMAGE_LAYER_MAX_BYTES = 524288  # 512KB max per image layer
DES_KEY = b'slv3tuzx'

# =============================================================================
# Display Constants
# =============================================================================
DISPLAY_WIDTH = 480
DISPLAY_HEIGHT = 1920
DISPLAY_FPS = 25

# =============================================================================
# Retry/Timeout Constants
# =============================================================================
USB_WRITE_TIMEOUT = 2000
USB_READ_TIMEOUT = 2000
USB_RETRY_COUNT = 3
USB_RETRY_DELAY = 0.5

# =============================================================================
# Cache Directory
# =============================================================================
CACHE_DIR = Path("/tmp/turing-screen-cache")


def _ensure_cache_dir():
    """Ensure cache directory exists."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def build_command_packet_header(cmd_id: int) -> bytearray:
    """Build a 500-byte command packet header.

    Args:
        cmd_id: Command ID (see CMD_* constants)

    Returns:
        500-byte packet with header fields populated
    """
    packet = bytearray(500)
    packet[0] = cmd_id
    packet[2] = 0x1A  # Magic byte 1
    packet[3] = 0x6D  # Magic byte 2
    timestamp = int((time.time() - time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))) * 1000)
    packet[4:8] = struct.pack('<I', timestamp)
    return packet


def encrypt_with_des(key: bytes, data: bytes) -> bytes:
    """Encrypt data using DES-CBC."""
    cipher = DES.new(key, DES.MODE_CBC, key)
    padded_len = (len(data) + 7) // 8 * 8
    padded_data = data.ljust(padded_len, b'\x00')
    return cipher.encrypt(padded_data)


def encrypt_command_packet(data: bytearray) -> bytearray:
    """Encrypt a command packet for transmission.

    Returns:
        512-byte encrypted packet with trailer bytes
    """
    encrypted = encrypt_with_des(DES_KEY, data)
    final_packet = bytearray(512)
    final_packet[:len(encrypted)] = encrypted
    final_packet[510] = 0xA1  # Trailer byte 1
    final_packet[511] = 0x1A  # Trailer byte 2
    return final_packet


def _configure_device(dev):
    """Configure a USB device for communication."""
    try:
        dev.set_configuration()
    except usb.core.USBError as exc:
        logger.warning("set_configuration() failed: %s", exc)

    if platform.system() == "Linux":
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except usb.core.USBError as exc:
            logger.warning("detach_kernel_driver failed: %s", exc)

    return dev


def get_device_serial(dev) -> str:
    """Get the serial number for a device, or fallback to bus:address."""
    try:
        serial = dev.serial_number
        if serial:
            return serial
    except (usb.core.USBError, ValueError):
        pass
    return f"bus{dev.bus:03d}:{dev.address:03d}"


def find_all_usb_devices() -> list:
    """Find all connected Turing Smart Screen devices, sorted by serial number."""
    devices = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID, find_all=True)
    if devices is None:
        return []
    device_list = list(devices)
    device_list.sort(key=get_device_serial)
    return device_list


def find_usb_device(device_selector=None):
    """Find a USB device, optionally by index or serial number.

    Args:
        device_selector: None for first device, int for index, str for serial match

    Returns:
        Configured USB device

    Raises:
        ValueError: If no device found or selector doesn't match
    """
    devices = find_all_usb_devices()

    if not devices:
        raise ValueError("No Turing Smart Screen devices found")

    if device_selector is None:
        return _configure_device(devices[0])

    if isinstance(device_selector, int):
        if device_selector < 0 or device_selector >= len(devices):
            raise ValueError(
                f"Device index {device_selector} out of range (0-{len(devices) - 1})"
            )
        return _configure_device(devices[device_selector])

    # Select by serial number (full or partial match)
    serial_str = str(device_selector)
    matches = []
    for dev in devices:
        dev_serial = get_device_serial(dev)
        if dev_serial == serial_str:
            return _configure_device(dev)
        if dev_serial.startswith(serial_str):
            matches.append(dev)

    if len(matches) == 1:
        return _configure_device(matches[0])
    if len(matches) > 1:
        serials = [get_device_serial(d) for d in matches]
        raise ValueError(
            f"Ambiguous serial prefix '{serial_str}' matches: {', '.join(serials)}"
        )

    raise ValueError(f"No device found matching '{serial_str}'")


def list_usb_devices() -> None:
    """List all connected Turing Smart Screen devices."""
    devices = find_all_usb_devices()

    if not devices:
        print("No Turing Smart Screen devices found.")
        return

    print(f"{'Index':<6} {'Serial':<18} {'Bus:Addr':<10} {'Product':<10}")
    print("-" * 50)

    for idx, dev in enumerate(devices):
        serial = get_device_serial(dev)
        bus_addr = f"{dev.bus:03d}:{dev.address:03d}"
        try:
            product = dev.product or "Unknown"
        except (usb.core.USBError, ValueError):
            product = "Unknown"
        print(f"{idx:<6} {serial:<18} {bus_addr:<10} {product:<10}")


def read_flush(ep_in, max_attempts=5):
    """Flush the USB IN endpoint by reading available data until timeout."""
    for _ in range(max_attempts):
        try:
            ep_in.read(512, timeout=100)
        except usb.core.USBError as e:
            if e.errno == 110 or e.args[0] == 'Operation timed out':
                break
            else:
                break


def write_to_device(dev, data, timeout=USB_WRITE_TIMEOUT, retries=USB_RETRY_COUNT):
    """Write data to USB device with retry logic.

    Args:
        dev: USB device
        data: Data to write
        timeout: Timeout in ms
        retries: Number of retry attempts for transient errors

    Returns:
        Response bytes or None on failure
    """
    cfg = dev.get_active_configuration()
    intf = usb.util.find_descriptor(cfg, bInterfaceNumber=0)
    if intf is None:
        logger.error("USB interface 0 not found")
        return None

    ep_out = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
    )
    ep_in = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
    )

    if ep_out is None or ep_in is None:
        logger.error("Could not find USB endpoints")
        return None

    last_error = None
    for attempt in range(retries):
        try:
            ep_out.write(data, timeout)
            response = ep_in.read(512, timeout)
            read_flush(ep_in)
            return bytes(response)
        except usb.core.USBError as e:
            last_error = e
            # Check for transient "Resource busy" errors
            if "Resource busy" in str(e) or e.errno == 16:
                if attempt < retries - 1:
                    logger.debug("USB busy, retrying in %.1fs (attempt %d/%d)",
                                USB_RETRY_DELAY, attempt + 1, retries)
                    time.sleep(USB_RETRY_DELAY)
                    continue
            logger.error("USB error: %s", e)
            return None

    logger.error("USB operation failed after %d retries: %s", retries, last_error)
    return None


def delay_sync(dev):
    """Send sync command with delay."""
    send_sync_command(dev)
    time.sleep(0.2)


def send_sync_command(dev):
    """Send sync command (ID 10)."""
    logger.debug("Sending Sync Command (ID %d)", CMD_SYNC)
    cmd_packet = build_command_packet_header(CMD_SYNC)
    return write_to_device(dev, encrypt_command_packet(cmd_packet))


def send_restart_device_command(dev):
    """Send restart command (ID 11)."""
    logger.info("Sending Restart Command (ID %d)", CMD_RESTART)
    return write_to_device(dev, encrypt_command_packet(build_command_packet_header(CMD_RESTART)))


def send_brightness_command(dev, brightness: int):
    """Send brightness command (ID 14).

    Args:
        brightness: Brightness level (0-102 internal scale)
    """
    logger.debug("Setting brightness to %d", brightness)
    cmd_packet = build_command_packet_header(CMD_BRIGHTNESS)
    cmd_packet[8] = brightness
    return write_to_device(dev, encrypt_command_packet(cmd_packet))


def send_frame_rate_command(dev, frame_rate: int):
    """Send frame rate command (ID 15).

    Args:
        frame_rate: Frame rate in FPS
    """
    logger.debug("Setting frame rate to %d fps", frame_rate)
    cmd_packet = build_command_packet_header(CMD_FRAME_RATE)
    cmd_packet[8] = frame_rate
    return write_to_device(dev, encrypt_command_packet(cmd_packet))


def format_bytes(val):
    """Format byte count as human-readable string."""
    if val > 1024 * 1024:
        return f"{val / (1024 * 1024):.2f} GB"
    else:
        return f"{val / 1024:.2f} MB"


def send_refresh_storage_command(dev):
    """Send refresh storage command (ID 100) and log storage info."""
    logger.info("Refreshing storage info")
    response = write_to_device(dev, encrypt_command_packet(build_command_packet_header(CMD_REFRESH_STORAGE)))

    if response:
        total = format_bytes(int.from_bytes(response[8:12], byteorder='little'))
        used = format_bytes(int.from_bytes(response[12:16], byteorder='little'))
        valid = format_bytes(int.from_bytes(response[16:20], byteorder='little'))
        logger.info("Storage - Total: %s, Used: %s, Valid: %s", total, used, valid)


def send_save_settings_command(dev, brightness=0, startup=0, reserved=0, rotation=0, sleep=0, offline=0):
    """Send save settings command (ID 125).

    Note: Rotation setting only affects static images, NOT video playback.
    """
    logger.info("Saving settings: brightness=%d, rotation=%d, sleep=%d, offline=%d",
               brightness, rotation, sleep, offline)
    cmd_packet = build_command_packet_header(CMD_SAVE_SETTINGS)
    cmd_packet[8] = brightness
    cmd_packet[9] = startup
    cmd_packet[10] = reserved
    cmd_packet[11] = rotation
    cmd_packet[12] = sleep
    cmd_packet[13] = offline
    return write_to_device(dev, encrypt_command_packet(cmd_packet))


def send_image(dev, png_data: bytes):
    """Send PNG image data to display (ID 102).

    Args:
        png_data: Raw PNG file bytes

    Returns:
        Response from device or None on failure
    """
    img_size = len(png_data)
    logger.debug("Sending image: %d bytes", img_size)

    cmd_packet = build_command_packet_header(CMD_SEND_IMAGE)
    cmd_packet[8] = (img_size >> 24) & 0xFF
    cmd_packet[9] = (img_size >> 16) & 0xFF
    cmd_packet[10] = (img_size >> 8) & 0xFF
    cmd_packet[11] = img_size & 0xFF

    full_payload = encrypt_command_packet(cmd_packet) + png_data
    return write_to_device(dev, full_payload)


def clear_image(dev):
    """Send a blank/clear image to the display."""
    img_data = bytearray(
        [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52, 0x00, 0x00,
            0x01, 0xe0, 0x00, 0x00, 0x07, 0x80, 0x08, 0x06, 0x00, 0x00, 0x00, 0x16, 0xf0, 0x84, 0xf5, 0x00, 0x00, 0x00,
            0x01, 0x73, 0x52, 0x47, 0x42, 0x00, 0xae, 0xce, 0x1c, 0xe9, 0x00, 0x00, 0x00, 0x04, 0x67, 0x41, 0x4d, 0x41,
            0x00, 0x00, 0xb1, 0x8f, 0x0b, 0xfc, 0x61, 0x05, 0x00, 0x00, 0x00, 0x09, 0x70, 0x48, 0x59, 0x73, 0x00, 0x00,
            0x0e, 0xc3, 0x00, 0x00, 0x0e, 0xc3, 0x01, 0xc7, 0x6f, 0xa8, 0x64, 0x00, 0x00, 0x0e, 0x0c, 0x49, 0x44, 0x41,
            0x54, 0x78, 0x5e, 0xed, 0xc1, 0x01, 0x0d, 0x00, 0x00, 0x00, 0xc2, 0xa0, 0xf7, 0x4f, 0x6d, 0x0f, 0x07, 0x14,
            0x00, 0x00, 0x00, 0x00, ] + [0x00] * 3568 + [0x00, 0xf0, 0x66, 0x4a, 0xc8, 0x00, 0x01, 0x11, 0x9d, 0x82,
            0x0a, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82])

    logger.debug("Clearing display")
    img_size = len(img_data)

    cmd_packet = build_command_packet_header(CMD_SEND_IMAGE)
    cmd_packet[8] = (img_size >> 24) & 0xFF
    cmd_packet[9] = (img_size >> 16) & 0xFF
    cmd_packet[10] = (img_size >> 8) & 0xFF
    cmd_packet[11] = img_size & 0xFF

    full_payload = encrypt_command_packet(cmd_packet) + img_data
    return write_to_device(dev, full_payload)


# Track delay command count to reduce log spam
_delay_count = 0
_delay_batch_size = 50


def delay(dev, rst):
    """Send delay command (ID 122) and wait for device ready.

    Logs are batched to reduce spam.
    """
    global _delay_count
    time.sleep(0.05)

    _delay_count += 1
    if _delay_count == 1 or _delay_count % _delay_batch_size == 0:
        logger.debug("Sending delay commands (count: %d)", _delay_count)

    cmd_packet = build_command_packet_header(CMD_DELAY)
    response = write_to_device(dev, encrypt_command_packet(cmd_packet))
    if response and response[8] > rst:
        delay(dev, rst)


def reset_delay_counter():
    """Reset the delay command counter (call at start of video loop)."""
    global _delay_count
    _delay_count = 0


def _probe_video(video_path: str) -> dict:
    """Probe video file with ffprobe to get format info.

    Args:
        video_path: Path to video file

    Returns:
        Dict with video info (width, height, codec, profile, fps) or empty dict on error
    """
    try:
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            str(video_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)

        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                # Parse frame rate (can be "25/1" format)
                fps_str = stream.get("r_frame_rate", "0/1")
                if "/" in fps_str:
                    num, den = fps_str.split("/")
                    fps = float(num) / float(den) if float(den) > 0 else 0
                else:
                    fps = float(fps_str)

                return {
                    "width": stream.get("width", 0),
                    "height": stream.get("height", 0),
                    "codec": stream.get("codec_name", ""),
                    "profile": stream.get("profile", "").lower(),
                    "fps": fps,
                }
        return {}
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError) as e:
        logger.warning("Failed to probe video: %s", e)
        return {}


def validate_video(video_path: str) -> list:
    """Validate video file for compatibility with Turing display.

    Args:
        video_path: Path to video file

    Returns:
        List of warning messages (empty if all OK)
    """
    warnings = []
    info = _probe_video(video_path)

    if not info:
        warnings.append(f"Could not probe video: {video_path}")
        return warnings

    # Check resolution
    if info["width"] != DISPLAY_WIDTH or info["height"] != DISPLAY_HEIGHT:
        warnings.append(
            f"Video resolution {info['width']}x{info['height']} differs from display {DISPLAY_WIDTH}x{DISPLAY_HEIGHT}"
        )

    # Check codec
    if info["codec"] != "h264":
        warnings.append(f"Video codec '{info['codec']}' is not H.264 - may not play correctly")

    # Check profile - baseline required
    if info["profile"] and "baseline" not in info["profile"]:
        warnings.append(
            f"Video profile '{info['profile']}' is not baseline - may cause artifacts. "
            "Re-encode with: ffmpeg -i input.mp4 -profile:v baseline -r 25 -bf 0 output.mp4"
        )

    # Check frame rate
    if info["fps"] and abs(info["fps"] - DISPLAY_FPS) > 1:
        warnings.append(f"Video frame rate {info['fps']:.1f} fps differs from display {DISPLAY_FPS} fps")

    return warnings


def _get_video_cache_path(input_path: Path, rotate_180: bool) -> Path:
    """Get cache path for processed H.264 file.

    Args:
        input_path: Original MP4 path
        rotate_180: Whether rotation is applied

    Returns:
        Path to cached H.264 file
    """
    _ensure_cache_dir()
    suffix = "_rotated" if rotate_180 else ""
    return CACHE_DIR / f"{input_path.stem}{suffix}.h264"


def _is_cache_valid(input_path: Path, cache_path: Path) -> bool:
    """Check if cached file is valid (exists and newer than source).

    Args:
        input_path: Source file
        cache_path: Cached file

    Returns:
        True if cache is valid and can be used
    """
    if not cache_path.exists():
        return False

    source_mtime = input_path.stat().st_mtime
    cache_mtime = cache_path.stat().st_mtime

    return cache_mtime > source_mtime


def extract_h264_from_mp4(mp4_path: str, rotate_180: bool = False) -> Path:
    """Extract/convert MP4 to raw H.264 Annex B format.

    Uses smart caching - only re-extracts if source is newer than cache.

    Args:
        mp4_path: Path to input MP4 file
        rotate_180: If True, apply 180-degree rotation with ffmpeg

    Returns:
        Path to H.264 file

    Raises:
        FileNotFoundError: If input file doesn't exist
        subprocess.CalledProcessError: If ffmpeg fails
    """
    input_path = Path(mp4_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Video file not found: {input_path}")

    output_path = _get_video_cache_path(input_path, rotate_180)

    # Check cache validity
    if _is_cache_valid(input_path, output_path):
        logger.debug("Using cached H.264: %s", output_path.name)
        return output_path

    # Validate source video
    warnings = validate_video(str(input_path))
    for warning in warnings:
        logger.warning(warning)

    if rotate_180:
        # Re-encode with rotation and baseline profile
        # CRITICAL: Must use baseline profile, NOT high (causes green artifacts)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-vf", "hflip,vflip",
            "-c:v", "libx264",
            "-profile:v", "baseline",
            "-pix_fmt", "yuv420p",
            "-color_range", "tv",
            "-colorspace", "bt709",
            "-color_primaries", "bt709",
            "-color_trc", "bt709",
            "-r", str(DISPLAY_FPS),
            "-bf", "0",  # No B-frames
            "-b:v", "5000k",
            "-preset", "slow",
            "-an",  # Strip audio
            "-bsf:v", "h264_mp4toannexb",
            "-f", "h264",
            str(output_path)
        ]
        logger.info("Extracting and rotating H.264 from %s...", input_path.name)
    else:
        # Just extract H.264 stream (assumes already properly encoded)
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-c:v", "copy",
            "-bsf:v", "h264_mp4toannexb",
            "-an",
            "-f", "h264",
            str(output_path)
        ]
        logger.info("Extracting H.264 from %s...", input_path.name)

    subprocess.run(cmd, check=True, capture_output=True)
    logger.info("Saved H.264 cache: %s", output_path.name)
    return output_path


def send_video(dev, video_path, loop=False):
    """Send video to device (legacy function).

    For new code, use LcdCommTuringUSB.show_video() instead.
    """
    output_path = extract_h264_from_mp4(video_path)
    write_to_device(dev, encrypt_command_packet(build_command_packet_header(CMD_UNKNOWN_111)))
    write_to_device(dev, encrypt_command_packet(build_command_packet_header(CMD_UNKNOWN_112)))
    write_to_device(dev, encrypt_command_packet(build_command_packet_header(CMD_UNKNOWN_13)))
    send_brightness_command(dev, 32)
    write_to_device(dev, encrypt_command_packet(build_command_packet_header(CMD_UNKNOWN_41)))
    clear_image(dev)
    send_frame_rate_command(dev, DISPLAY_FPS)

    logger.info("Streaming video...")
    try:
        while True:
            reset_delay_counter()
            with open(output_path, 'rb') as f:
                while True:
                    data = f.read(VIDEO_CHUNK_SIZE)
                    if not data:
                        break

                    chunksize = len(data)
                    cmd_packet = build_command_packet_header(CMD_SEND_VIDEO_CHUNK)
                    cmd_packet[8] = (chunksize >> 24) & 0xFF
                    cmd_packet[9] = (chunksize >> 16) & 0xFF
                    cmd_packet[10] = (chunksize >> 8) & 0xFF
                    cmd_packet[11] = chunksize & 0xFF

                    full_payload = encrypt_command_packet(cmd_packet) + data
                    response = write_to_device(dev, full_payload)
                    time.sleep(0.03)
                    if response is None or len(response) < 9 or response[8] <= 3:
                        delay(dev, 2)

            logger.debug("Video loop complete")
            if not loop:
                break
    except KeyboardInterrupt:
        logger.info("Video interrupted by user")
    finally:
        write_to_device(dev, encrypt_command_packet(build_command_packet_header(CMD_VIDEO_STOP)))


def _encode_png(image: Image.Image) -> bytes:
    """Encode PIL Image to PNG bytes with maximum compression."""
    buffer = BytesIO()
    image.save(buffer, format="PNG", compress_level=9)
    return buffer.getvalue()


def compress_image(image: Image.Image, ratio: float) -> Image.Image:
    """Compress image by reducing and restoring resolution."""
    width, height = image.size
    image = image.resize(
        (int(width * ratio * 0.5), int(height * ratio * 0.5)),
        resample=Image.Resampling.LANCZOS
    )
    image = image.resize((width, height))
    return image


def validate_image(image_path: str) -> list:
    """Validate image file for compatibility with Turing display.

    Args:
        image_path: Path to image file

    Returns:
        List of warning messages (empty if all OK)
    """
    warnings = []
    path = Path(image_path)

    if not path.exists():
        warnings.append(f"Image file not found: {image_path}")
        return warnings

    try:
        with Image.open(path) as img:
            width, height = img.size
            if (width, height) != (DISPLAY_WIDTH, DISPLAY_HEIGHT):
                warnings.append(
                    f"Image resolution {width}x{height} differs from display {DISPLAY_WIDTH}x{DISPLAY_HEIGHT}"
                )
    except Exception as e:
        warnings.append(f"Could not open image: {e}")

    return warnings


def send_layered_image(dev, image_path: str, max_chunk_bytes: int = IMAGE_LAYER_MAX_BYTES) -> bool:
    """Send image using layered approach for large images (>512KB).

    Images larger than max_chunk_bytes are split into vertical layers
    and sent from bottom to top.

    Args:
        dev: USB device
        image_path: Path to PNG image
        max_chunk_bytes: Maximum bytes per layer

    Returns:
        True if successful, False on error
    """
    # Validate first
    warnings = validate_image(image_path)
    for warning in warnings:
        logger.warning(warning)

    try:
        with Image.open(image_path) as img:
            img = img.convert("RGBA")
            width, height = img.size

            total_size = len(_encode_png(img))
            num_layers = math.ceil(total_size / max_chunk_bytes)
            logger.info("Image size: %d bytes -> %d layer(s)", total_size, num_layers)

            if num_layers == 1:
                encoded = _encode_png(img)
                return send_image(dev, encoded) is not None

            h = height // num_layers
            results = []

            for i in range(num_layers):
                y_start = max(0, height - (i + 1) * h)
                visible_part = img.crop((0, y_start, width, height - h * i))
                canvas_height = height - i * h
                layer_img = Image.new("RGBA", (width, canvas_height), (0, 0, 0, 0))
                layer_img.paste(visible_part, (0, y_start))

                logger.info("Sending layer %d/%d (%dx%d)", i + 1, num_layers, width, canvas_height)
                encoded = _encode_png(layer_img)
                result = send_image(dev, encoded)
                results.append(result is not None)

            return all(results)
    except Exception as exc:
        logger.error("Failed to send image: %s", exc)
        return False


def upload_file(dev, file_path: str) -> bool:
    """Upload file to device storage."""
    local_path = Path(file_path)
    if not local_path.exists():
        logger.error("File not found: %s", file_path)
        return False

    ext = local_path.suffix.lower()
    if ext == ".png":
        device_path = f"/tmp/sdcard/mmcblk0p1/img/{local_path.name}"
        logger.info("Uploading PNG: %s -> %s", file_path, device_path)
    elif ext == ".mp4":
        h264_path = extract_h264_from_mp4(file_path)
        device_path = f"/tmp/sdcard/mmcblk0p1/video/{h264_path.name}"
        local_path = h264_path
        logger.info("Uploading MP4 as H264: %s -> %s", local_path, device_path)
    else:
        logger.error("Unsupported file type '%s'. Only .png and .mp4 are supported.", ext)
        return False

    if not _open_file_command(dev, device_path):
        logger.error("Failed to open remote file for writing")
        return False

    if not _write_file_command(dev, str(local_path)):
        logger.error("Failed to write file data")
        return False

    logger.info("Upload completed: %s", device_path)
    return True


def _open_file_command(dev, path: str):
    """Open remote file for writing (ID 38)."""
    logger.debug("Opening remote file: %s", path)

    path_bytes = path.encode("ascii")
    length = len(path_bytes)

    packet = build_command_packet_header(CMD_OPEN_FILE)
    packet[8] = (length >> 24) & 0xFF
    packet[9] = (length >> 16) & 0xFF
    packet[10] = (length >> 8) & 0xFF
    packet[11] = length & 0xFF
    packet[12:16] = b"\x00\x00\x00\x00"
    packet[16:16 + length] = path_bytes

    return write_to_device(dev, encrypt_command_packet(packet))


def _delete_command(dev, file_path: str):
    """Delete remote file (ID 40)."""
    logger.debug("Deleting remote file: %s", file_path)

    path_bytes = file_path.encode("ascii")
    length = len(path_bytes)

    packet = build_command_packet_header(CMD_DELETE_FILE)
    packet[8] = (length >> 24) & 0xFF
    packet[9] = (length >> 16) & 0xFF
    packet[10] = (length >> 8) & 0xFF
    packet[11] = length & 0xFF
    packet[12:16] = b"\x00\x00\x00\x00"
    packet[16:16 + length] = path_bytes

    return write_to_device(dev, encrypt_command_packet(packet))


def _play_command(dev, file_path: str):
    """Request playback (ID 98)."""
    logger.debug("Requesting playback: %s", file_path)

    path_bytes = file_path.encode("ascii")
    length = len(path_bytes)

    packet = build_command_packet_header(CMD_PLAY)
    packet[8] = (length >> 24) & 0xFF
    packet[9] = (length >> 16) & 0xFF
    packet[10] = (length >> 8) & 0xFF
    packet[11] = length & 0xFF
    packet[12:16] = b"\x00\x00\x00\x00"
    packet[16:16 + length] = path_bytes

    return write_to_device(dev, encrypt_command_packet(packet))


def _play2_command(dev, file_path: str):
    """Request alternate playback (ID 110)."""
    logger.debug("Requesting alternate playback: %s", file_path)

    path_bytes = file_path.encode("ascii")
    length = len(path_bytes)

    packet = build_command_packet_header(CMD_PLAY_ALT)
    packet[8] = (length >> 24) & 0xFF
    packet[9] = (length >> 16) & 0xFF
    packet[10] = (length >> 8) & 0xFF
    packet[11] = length & 0xFF
    packet[12:16] = b"\x00\x00\x00\x00"
    packet[16:16 + length] = path_bytes

    return write_to_device(dev, encrypt_command_packet(packet))


def _play3_command(dev, file_path: str):
    """Request image playback (ID 113)."""
    logger.debug("Requesting image playback: %s", file_path)

    path_bytes = file_path.encode("ascii")
    length = len(path_bytes)

    packet = build_command_packet_header(CMD_PLAY_IMAGE)
    packet[8] = (length >> 24) & 0xFF
    packet[9] = (length >> 16) & 0xFF
    packet[10] = (length >> 8) & 0xFF
    packet[11] = length & 0xFF
    packet[12:16] = b"\x00\x00\x00\x00"
    packet[16:16 + length] = path_bytes

    return write_to_device(dev, encrypt_command_packet(packet))


def _write_file_command(dev, file_path: str) -> bool:
    """Write file data to device (ID 39)."""
    logger.debug("Writing file: %s", file_path)

    try:
        with open(file_path, "rb") as fh:
            chunk_index = 0
            while True:
                data_chunk = fh.read(VIDEO_CHUNK_SIZE)
                if not data_chunk:
                    break

                chunk_size = len(data_chunk)
                chunk_index += 1
                logger.debug("Writing chunk %d: %d bytes", chunk_index, chunk_size)

                cmd_packet = build_command_packet_header(CMD_WRITE_FILE)
                cmd_packet[8] = (chunk_size >> 24) & 0xFF
                cmd_packet[9] = (chunk_size >> 16) & 0xFF
                cmd_packet[10] = (chunk_size >> 8) & 0xFF
                cmd_packet[11] = chunk_size & 0xFF

                response = write_to_device(dev, encrypt_command_packet(cmd_packet) + data_chunk)
                if response is None:
                    logger.error("Write failed at chunk %d", chunk_index)
                    return False

        logger.info("File write complete (%d chunks)", chunk_index)
        return True
    except FileNotFoundError:
        logger.error("File not found: %s", file_path)
        return False
    except Exception as exc:
        logger.error("Failed to write file: %s", exc)
        return False


class LcdCommTuringUSB(LcdComm):
    """LCD communication class for Turing Smart Screen USB models (5.2" / 8" / 8.8" HW rev 1.x / 9.2").

    These models are detected as USB devices, not serial ports.
    """

    def __init__(self, com_port: str = "AUTO", display_width: int = DISPLAY_WIDTH,
                 display_height: int = DISPLAY_HEIGHT, update_queue: Optional[queue.Queue] = None,
                 device_selector=None):
        super().__init__(com_port, display_width, display_height, update_queue)
        self.device_selector = device_selector
        self.dev = find_usb_device(device_selector)
        self.current_state = Image.new("RGBA", (self.get_width(), self.get_height()), (0, 0, 0, 0))

    def InitializeComm(self):
        """Initialize communication with device."""
        send_sync_command(self.dev)

    def Reset(self):
        """Reset device (currently disabled for USB models)."""
        pass

    def Clear(self):
        """Clear the display."""
        clear_image(self.dev)

    def ScreenOff(self):
        """Turn screen off (clear + zero brightness)."""
        self.Clear()
        self.SetBrightness(0)

    def ScreenOn(self):
        """Turn screen on (restore brightness)."""
        self.SetBrightness()

    def SetBrightness(self, level: int = 25):
        """Set display brightness.

        Args:
            level: Brightness 0-100
        """
        assert 0 <= level <= 100, 'Brightness must be 0-100'
        converted = int(level / 100 * 102)
        send_brightness_command(self.dev, converted)

    def SetOrientation(self, orientation: Orientation):
        """Set display orientation."""
        self.orientation = orientation
        self.current_state = Image.new("RGBA", (self.get_width(), self.get_height()), (0, 0, 0, 0))

    def DisplayPILImage(self, image: Image.Image, x: int = 0, y: int = 0,
                        image_width: int = 0, image_height: int = 0):
        """Display a PIL image at the specified position."""
        if not image_height:
            image_height = image.size[1]
        if not image_width:
            image_width = image.size[0]

        if image.size[1] > self.get_height():
            image_height = self.get_height()
        if image.size[0] > self.get_width():
            image_width = self.get_width()

        if image_width != image.size[0] or image_height != image.size[1]:
            image = image.crop((0, 0, image_width, image_height))

        self.current_state.paste(image, (x, y))

        # Rotate based on orientation
        if self.orientation == Orientation.LANDSCAPE:
            base_image = self.current_state.transpose(Image.Transpose.ROTATE_270)
        elif self.orientation == Orientation.REVERSE_LANDSCAPE:
            base_image = self.current_state.transpose(Image.Transpose.ROTATE_90)
        elif self.orientation == Orientation.PORTRAIT:
            base_image = self.current_state.transpose(Image.Transpose.ROTATE_180)
        else:  # REVERSE_PORTRAIT is native orientation
            base_image = self.current_state

        encoded = _encode_png(base_image)
        send_image(self.dev, encoded)

    def show_video(self, video_path: str, stop_event: threading.Event,
                   brightness: int = 32, rotate_180: bool = False):
        """Stream H.264 video in a continuous loop.

        Args:
            video_path: Path to MP4 file (will be converted to H.264)
            stop_event: Threading event to signal stop
            brightness: Display brightness (0-100)
            rotate_180: If True, rotate video 180 degrees via ffmpeg
                       (device rotation does NOT work for video)
        """
        # Extract/convert with rotation if needed
        output_path = extract_h264_from_mp4(video_path, rotate_180=rotate_180)

        # Video setup commands
        write_to_device(self.dev, encrypt_command_packet(build_command_packet_header(CMD_UNKNOWN_111)))
        write_to_device(self.dev, encrypt_command_packet(build_command_packet_header(CMD_UNKNOWN_112)))
        write_to_device(self.dev, encrypt_command_packet(build_command_packet_header(CMD_UNKNOWN_13)))
        send_brightness_command(self.dev, int(brightness / 100 * 102))
        write_to_device(self.dev, encrypt_command_packet(build_command_packet_header(CMD_UNKNOWN_41)))
        clear_image(self.dev)
        send_frame_rate_command(self.dev, DISPLAY_FPS)

        logger.info("Starting video: %s", video_path)

        try:
            while not stop_event.is_set():
                reset_delay_counter()
                with open(output_path, 'rb') as f:
                    while not stop_event.is_set():
                        data = f.read(VIDEO_CHUNK_SIZE)
                        if not data:
                            break

                        chunksize = len(data)
                        cmd_packet = build_command_packet_header(CMD_SEND_VIDEO_CHUNK)
                        cmd_packet[8] = (chunksize >> 24) & 0xFF
                        cmd_packet[9] = (chunksize >> 16) & 0xFF
                        cmd_packet[10] = (chunksize >> 8) & 0xFF
                        cmd_packet[11] = chunksize & 0xFF

                        full_payload = encrypt_command_packet(cmd_packet) + data
                        response = write_to_device(self.dev, full_payload)
                        time.sleep(0.03)

                        if response is None or len(response) < 9 or response[8] <= 3:
                            delay(self.dev, 2)

                logger.debug("Video loop complete, restarting...")

        except Exception as e:
            logger.error("Video playback error: %s", e)
        finally:
            logger.info("Video stopped")

    def stop_video(self):
        """Stop video playback."""
        logger.info("Stopping video")
        write_to_device(self.dev, encrypt_command_packet(build_command_packet_header(CMD_UNKNOWN_111)))
        write_to_device(self.dev, encrypt_command_packet(build_command_packet_header(CMD_UNKNOWN_112)))
        write_to_device(self.dev, encrypt_command_packet(build_command_packet_header(CMD_VIDEO_STOP)))

    def show_image(self, image_path: str) -> bool:
        """Display a static image.

        Automatically uses layered sending for large images (>512KB).

        Args:
            image_path: Path to PNG file

        Returns:
            True if successful, False on error
        """
        path = Path(image_path)
        if not path.exists():
            logger.error("Image not found: %s", image_path)
            return False

        logger.info("Showing image: %s", image_path)
        return send_layered_image(self.dev, str(path))

