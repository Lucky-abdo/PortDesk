# ═══════════════════════════════════════════════════════════════════
#  Build:    docker build -t portdesk-server .
#  Run:      docker run -d --name portdesk -p 5000:5000 portdesk-server
#  Full:     docker run -d --name portdesk --privileged \
#              -p 5000:5000 -v /dev/uinput:/dev/uinput \
#              -e PORTDESK_ARGS="--verbose" portdesk-server
# ═══════════════════════════════════════════════════════════════════

# ── Stage 1: Builder — install Python dependencies ───────────────
FROM python:3.11-slim-bookworm AS builder

WORKDIR /build

# System deps needed for compiling Python packages with C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    libffi-dev \
    libssl-dev \
    libx11-dev \
    libxtst-dev \
    libxrandr-dev \
    libglib2.0-dev \
    libgl1-mesa-dev \
    libudev-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages into a separate prefix for clean copy
COPY requirements-docker.txt ./requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: Runtime — minimal image with only what's needed ─────
FROM python:3.11-slim-bookworm AS runtime

LABEL maintainer="Lucky-abdo"
LABEL description="PortDesk — Remote Desktop Server"
LABEL org.opencontainers.image.source="https://github.com/Lucky-abdo/PortDesk"

# Prevent Python from writing .pyc files and enable unbuffered stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install ONLY runtime system dependencies (no build tools)
#   - xdotool, xclip, xsel : needed by pyautogui/pyperclip on Linux
#   - libportaudio2         : needed for audio streaming
#   - ffmpeg                : H.264 hardware encoding (falls back to JPEG without it)
#   - libgl1                : OpenCV runtime dependency
#   - libglib2.0-0          : OpenCV runtime dependency
#   - dbus                  : pystray system tray support
#   - xvfb                  : Virtual X server for headless screen capture
RUN apt-get update && apt-get install -y --no-install-recommends \
    xdotool \
    xclip \
    xsel \
    libportaudio2 \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    libdbus-1-3 \
    xvfb \
    python3-uinput \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder stage
COPY --from=builder /install /usr/local

# Create app directory
WORKDIR /app

# Copy application files
COPY portdesk-server.py .
COPY portdesk_client.html .
COPY entrypoint.sh .

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Create volume mount points
#   - /app/data      : security config, logs, scheduled tasks
#   - /app/uploads   : uploaded files
#   - /dev/uinput    : virtual keyboard (needs --privileged)
VOLUME ["/app/data"]

# Expose the default PortDesk port
EXPOSE 5000

# Health check — verify the server is responding
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/')" || exit 1

# Use entrypoint script for flexible configuration
ENTRYPOINT ["/app/entrypoint.sh"]

# Default arguments (can be overridden via docker run or env vars)
CMD ["--host", "0.0.0.0", "--port", "5000"]
