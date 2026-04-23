#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  PortDesk — Docker Entrypoint Script
#
#  This script handles:
#  1. Starting Xvfb (virtual X server) for headless screen capture
#  2. Setting up /dev/uinput permissions for virtual keyboard
#  3. Merging CLI args from environment variable + command line
#  4. Starting the PortDesk server
# ═══════════════════════════════════════════════════════════════
set -e

echo "════════════════════════════════════════════════"
echo "  PortDesk Docker Container Starting..."
echo "════════════════════════════════════════════════"

# ── 1. Start Xvfb (Virtual X Server) ──────────────────────────
# Required for screen capture (mss) and mouse/keyboard (pyautogui)
# in headless Docker containers without a physical display.
if [ -z "$DISPLAY" ]; then
    echo "  ℹ  Starting Xvfb on :99..."
    Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp &
    export DISPLAY=:99
    # Give Xvfb a moment to start
    sleep 1
    echo "  ✅ Xvfb started (DISPLAY=$DISPLAY)"
else
    echo "  ℹ  Using existing DISPLAY=$DISPLAY"
fi

# ── 2. /dev/uinput permissions ────────────────────────────────
# Virtual keyboard (python-uinput) requires /dev/uinput access.
# This only works with --privileged flag or explicit device mapping.
if [ -e /dev/uinput ]; then
    if [ ! -w /dev/uinput ]; then
        echo "  ⚠ /dev/uinput exists but not writable."
        echo "     Run with: --privileged OR --device /dev/uinput"
        echo "     Virtual keyboard will be unavailable (xdotool fallback will be used)."
    else
        echo "  ✅ /dev/uinput is writable — virtual keyboard available"
    fi
else
    echo "  ℹ /dev/uinput not found — virtual keyboard unavailable (xdotool fallback)"
fi

# ── 3. Security config symlink ────────────────────────────────
# If /app/data/portdesk_security.json exists in the volume, use it.
# Otherwise, the server will create a new one automatically.
if [ -f /app/data/portdesk_security.json ]; then
    ln -sf /app/data/portdesk_security.json /app/portdesk_security.json
    echo "  ✅ Using persisted security config from /app/data/"
fi

# ── 4. Merge CLI arguments ────────────────────────────────────
# Priority: Environment variable PORTDESK_ARGS > CMD arguments
# Example: docker run -e PORTDESK_ARGS="--verbose --grey" ...
EXTRA_ARGS=""
if [ -n "$PORTDESK_ARGS" ]; then
    EXTRA_ARGS="$PORTDESK_ARGS"
    echo "  ℹ  Extra args from PORTDESK_ARGS: $EXTRA_ARGS"
fi

# ── 5. Start the server ──────────────────────────────────────
echo "════════════════════════════════════════════════"
echo "  Starting PortDesk Server..."
echo "════════════════════════════════════════════════"

exec python /app/portdesk-server.py "$@" $EXTRA_ARGS