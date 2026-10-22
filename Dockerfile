# syntax=docker/dockerfile:1
# Multi-stage build: Node builds the React front end, Python serves it together with the API.

# ---------- stage 1: front end ----------
FROM node:22-alpine AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- stage 2: back end + static files ----------
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    STATIC_DIR=/app/static \
    SAMPLES_DIR=/app/samples \
    STORAGE_LOCAL_DIR=/app/data
WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY samples ./samples
COPY --from=frontend /src/frontend/dist ./static

# Run as a non-root user; /app/data is the local storage fallback (use STORAGE_MODE=blob in production).
RUN useradd --system --uid 10001 --create-home appuser \
 && mkdir -p /app/data \
 && chown -R appuser:appuser /app/data
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

# One uvicorn worker per container; scale out with Container App replicas (jobs persist through storage).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
