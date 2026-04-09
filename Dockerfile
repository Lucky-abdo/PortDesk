FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    scrot \
    xdotool \
    xclip \
    libgl1 \
    libglib2.0-0 \
    libportaudio2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["python", "portdesk-server.py"]
