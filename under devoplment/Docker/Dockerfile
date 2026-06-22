# ═══════════════════════════════════════════════════════════════════
#  PortDesk — Docker Image (Multi-Stage Build)
# ═══════════════════════════════════════════════════════════════════

# ── Stage 1: Builder ──
FROM python:3.12-slim-bookworm AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ libffi-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY Docker/requirements-docker.txt ./requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: Runtime ──
FROM python:3.12-slim-bookworm AS runtime
LABEL maintainer="Lucky-abdo"
LABEL description="PortDesk — Remote Desktop Server"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    xdotool xclip xsel libportaudio2 ffmpeg libgl1 libglib2.0-0 \
    libdbus-1-3 xvfb python3-uinput \
    libturbojpeg0-dev \
    libjpeg-dev libpng-dev libopenjp2-7-dev \
    && rm -rf /var/lib/apt/lists/*
# NOTE: libturbojpeg0-dev is needed for PyTurboJPEG (hardware JPEG encoding).
# If you don't need it, remove libturbojpeg0-dev above and comment out
# PyTurboJPEG in requirements-docker.txt — the server falls back to cv2.
COPY --from=builder /install /usr/local
WORKDIR /app

# ── Copy server entry point (supports both naming conventions) ──
COPY SERVER.py* portdesk-server.py* ./

# ── Copy modules directory (required — server imports from here) ──
# The bootstrap in SERVER.py auto-discovers this directory and adds it
# to sys.path. If you rename "modules/" to something else, update
# MODULE_DIR_NAMES in SERVER.py and the COPY line below.
COPY modules/ ./modules/

# ── Copy client HTML (supports both naming conventions) ──
COPY CLIENT.html* portdesk_client.html* ./

# Phase 2: pre-subset .woff2 fonts (committed to extras/fonts/) served via /static/fonts/{file}
COPY extras/fonts/ ./extras/fonts/

COPY Docker/entrypoint.sh ./
RUN chmod +x entrypoint.sh || true
VOLUME ["/app/data"]
EXPOSE 5000
# NOTE: For multiprocessing shared_memory to work, run with --shm-size=256m or --ipc=host
# Example: docker run --shm-size=256m -p 5000:5000 portdesk
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/')" || exit 1
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["--host", "0.0.0.0", "--port", "5000"]
