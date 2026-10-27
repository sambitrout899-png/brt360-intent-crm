# BRT 360 API image. The same image runs the API (default command), the
# background scheduler (python -m app.scheduler) and migrations + seed
# (deploy/compose/docker-compose.yml picks the command for each).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# dependencies first: a code-only change reuses this layer
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# never run as root inside the container
RUN useradd --system --uid 10001 --home /app brt && chown -R brt /app
USER brt

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

# WEB_CONCURRENCY sets the worker count (gunicorn reads it); the API sits
# behind the web container's nginx, so forwarded client IPs are trusted.
CMD ["gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000", \
     "--timeout", "120", "--access-logfile", "-", "--forwarded-allow-ips", "*"]
