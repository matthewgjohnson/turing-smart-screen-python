# CLAUDE.md

Project guide for Claude Code sessions working on turing-smart-screen-python.

## Project Overview

Fork of mathoudebine/turing-smart-screen-python with multi-display support for Turing 8.8" Smart Screens.

## Hardware

- Three Turing 8.8" V1.x USB-only screens (VID 0x1cbe, PID 0x0088)
- Product string: TURZX1.0
- Requires branch: dev/add-turing-8.8-v1.x-usb-protocol
- Requires config: REVISION: C_USB

## Python Environment

**IMPORTANT: Use uv for all Python operations. Never use pip/venv directly.**

    # Install dependencies
    cd /home/mgj/turing-smart-screen-python
    ~/.local/bin/uv sync

    # Run the app
    ~/.local/bin/uv run python main.py

    # Run with logging
    ~/.local/bin/uv run python main.py 2>&1 | tee /tmp/turing.log

## Key Commands

Run for testing (foreground):

    cd /home/mgj/turing-smart-screen-python
    ~/.local/bin/uv run python main.py

Run detached for testing (still receives signals):

    cd /home/mgj/turing-smart-screen-python
    ~/.local/bin/uv run python main.py > /tmp/turing.log 2>&1 &

Check logs:

    tail -f /tmp/turing.log

Stop the app:

    pkill -f 'python main.py'

## Production Deployment

For production, use systemd service (managed via ansible).
See: turing-screen.service

## Configuration

Edit config.yaml:
- REVISION: C_USB - Required for 8.8" V1.x USB screens
- THEME: AMD - Horizontal 1920x480 theme for landscape screens
- THEME: "Cyberpunk 2077 Vertical" - Vertical 480x1920 theme

## Critical Notes

1. App must run persistently - Killing the process turns screen dark
2. First USB device = screen 1 - App picks first enumerated device
3. Branch requirement - Main branch does NOT support 8.8" V1.x; must use dev branch
4. Coexists with CLI - turing-smart-screen-cli can control other screens simultaneously

## Related Projects

- /home/mgj/turing-smart-screen-cli - CLI tool for video/image display
- See: Turing Multi-Display Implementation Briefing v1-12.md
