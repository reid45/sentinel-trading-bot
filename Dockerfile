# Sentinel trading bot. Pure standard-library Python: no pip install step, tiny image.
FROM python:3.11-slim

RUN useradd --system --create-home --uid 10001 sentinel \
 && mkdir /data && chown sentinel /data
WORKDIR /app
COPY --chown=sentinel . /app

ENV HOST=0.0.0.0 \
    PORT=8765 \
    TRADEBOT_DATA=/data \
    PYTHONUNBUFFERED=1 \
    SENTINEL_AUTH=1 \
    SENTINEL_TRUST_PROXY=1

USER sentinel
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/healthz',timeout=4).status==200 else 1)"
CMD ["python", "server.py"]
