# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgeos-dev \
    libproj-dev \
    proj-data \
    proj-bin \
    libgdal-dev \
    gdal-bin \
    libspatialindex-dev \
    curl \
    tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && ln -snf /usr/share/zoneinfo/Europe/Lisbon /etc/localtime \
    && echo "Europe/Lisbon" > /etc/timezone

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ARG CACHEBUST=1
RUN echo "CACHEBUST=$CACHEBUST"

COPY . .

# Coolify Dockerfile pack defaults Ports Exposes / PORT to 3000.
# Healthcheck must use $PORT (runtime), not a hardcoded port.
ENV PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    PORT=3000

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -f http://127.0.0.1:${PORT:-3000}/health || exit 1

CMD ["python", "app.py"]