# Build context is the REPO ROOT:  docker build -f exporter/Dockerfile .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY exporter/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY exporter /app

# SECURITY NOTE: this container runs as root on purpose. It reads the Docker
# socket (/var/run/docker.sock, mounted read-only in docker-compose.yml) to
# `docker exec` into the lab containers, and the socket is owned by root:docker
# with a host-specific GID, so a non-root user cannot portably open it. The
# image holds no secrets, publishes no host port by default and only exposes
# read-only metrics on :9200. Do not reuse this pattern outside the lab.
USER root

EXPOSE 9200
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9200/metrics', timeout=2).status == 200 else 1)"

CMD ["python", "/app/ibg_exporter.py"]
