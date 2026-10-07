# Build context is the REPO ROOT:  docker build -f intent_engine/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    INTENT_PORT=8088

WORKDIR /app

COPY intent_engine/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY intent_engine /app/intent_engine

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin intent \
    && chown -R intent:intent /app
USER intent

EXPOSE 8088

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8088/healthz', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "intent_engine.main:app", "--host", "0.0.0.0", "--port", "8088"]
