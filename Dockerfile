# Production image for Yam. (Dev dependencies are managed by the Nix flake; this
# Dockerfile is the image build so `docker build` works on any host — including
# building/pushing to a registry without a Linux Nix builder.)
#
#   docker build -t yam:latest .
FROM python:3.13-slim

# ffmpeg is needed at runtime to mux the downloaded video + audio streams.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY yam ./yam

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    MEDIA_DIR=/media \
    DATA_DIR=/data

EXPOSE 8080
CMD ["uvicorn", "yam.main:app", "--host", "0.0.0.0", "--port", "8080"]
