# Production image for Yam. (Dev dependencies are managed by the Nix flake; this
# Dockerfile is the image build so `docker build` works on any host — including
# building/pushing to a registry without a Linux Nix builder.)
#
#   docker build -t yam:latest .
#
# Two stages, so pip and its build machinery stay out of the shipped image.
# Dependencies install into a throwaway prefix and are copied wholesale into
# /usr/local, which is already the runtime image's site-packages and bin root —
# so nothing has to be re-resolved or re-pathed on the other side.
FROM python:3.13-slim AS builder

# The yt-dlp release to bake in. CI resolves the current stable version and
# passes it here so both architectures of one build pin the same version and the
# image can be labelled with it -- that label is what the scheduled workflow
# compares against PyPI to decide whether a rebuild is worth running. A bare
# `docker build` leaves it empty and gets whatever requirements.txt floats to.
ARG YTDLP_VERSION=""

WORKDIR /app
COPY requirements.txt .
RUN if [ -n "$YTDLP_VERSION" ]; then \
        echo "yt-dlp==${YTDLP_VERSION}" > /tmp/constraints.txt; \
    else \
        : > /tmp/constraints.txt; \
    fi \
    && pip install --no-cache-dir --prefix=/install -c /tmp/constraints.txt -r requirements.txt

FROM python:3.13-slim

# Records what the builder actually installed, so the scheduled workflow can read
# it off the published manifest without pulling the image. "unknown" on an
# unpinned local build, which the workflow treats as stale and rebuilds.
ARG YTDLP_VERSION=""
LABEL io.yam.ytdlp.version="${YTDLP_VERSION:-unknown}"

# ffmpeg is needed at runtime to mux the downloaded video + audio streams.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

WORKDIR /app

COPY yam ./yam

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    MEDIA_DIR=/media \
    DATA_DIR=/data

# Runs unprivileged. MEDIA_DIR and DATA_DIR are created and handed to this account
# so a bare `docker run` works out of the box; the Ansible deploy pins its own
# uid:gid on the Compose service (roles/yam), which overrides this, and its bind
# mounts carry the host's ownership regardless. /app itself stays root-owned —
# the app only reads its own code.
RUN useradd --system --no-create-home --shell /usr/sbin/nologin yam \
    && mkdir -p /media /data \
    && chown yam:yam /media /data
USER yam

EXPOSE 8080
CMD ["uvicorn", "yam.main:app", "--host", "0.0.0.0", "--port", "8080"]
