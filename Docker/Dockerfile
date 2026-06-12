# ═══════════════════════════════════════════════════════════════════
#  PortDesk — Docker Image (Multi-Stage Build)
# ═══════════════════════════════════════════════════════════════════

# ── Stage 1: Builder ──
FROM python:3.11-slim-bookworm AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ libffi-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY Docker/requirements-docker.txt ./requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: Runtime ──
FROM python:3.11-slim-bookworm AS runtime
LABEL maintainer="Lucky-abdo"
LABEL description="PortDesk — Remote Desktop Server"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    xdotool xclip xsel libportaudio2 ffmpeg libgl1 libglib2.0-0 \
    libdbus-1-3 xvfb python3-uinput \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /install /usr/local
WORKDIR /app
COPY SERVER.py* portdesk-server.py* ./
COPY CLIENT.html* portdesk_client.html* ./
COPY Docker/entrypoint.sh ./
RUN chmod +x entrypoint.sh || true
VOLUME ["/app/data"]
EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/')" || exit 1
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["--host", "0.0.0.0", "--port", "5000"]