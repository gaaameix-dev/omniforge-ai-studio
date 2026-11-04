FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY omniforge ./omniforge
COPY web ./web

RUN pip install --upgrade pip && pip install . && \
    useradd --create-home --uid 10001 omniforge && \
    mkdir -p /app/data && chown -R omniforge:omniforge /app

USER omniforge

ENV OMNIFORGE_DATA_DIR=/app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "omniforge.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
