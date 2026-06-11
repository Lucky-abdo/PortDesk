#!/usr/bin/env bash
set -e

echo "════════════════════════════════════════════════"
echo "  PortDesk Docker Container Starting..."
echo "════════════════════════════════════════════════"

# Flexible: if SERVER.py not found, use the old name
if [ -f /app/SERVER.py ]; then
    SERVER_FILE="/app/SERVER.py"
elif [ -f /app/portdesk-server.py ]; then
    SERVER_FILE="/app/portdesk-server.py"
else
    echo "❌ SERVER.py or portdesk-server.py not found"
    exit 1
fi

# Start Xvfb if no display is available (headless mode)
if [ -z "$DISPLAY" ]; then
    echo "  ℹ  Starting Xvfb on :99..."
    Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp &
    export DISPLAY=:99
    sleep 1
fi

# Ensure uinput device is accessible
if [ -e /dev/uinput ]; then
    chmod 666 /dev/uinput 2>/dev/null || true
fi

echo "════════════════════════════════════════════════"
echo "  Starting PortDesk Server..."
echo "════════════════════════════════════════════════"

exec python "$SERVER_FILE" "$@" $EXTRA_ARGS