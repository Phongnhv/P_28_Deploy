FROM python:3.11-slim

WORKDIR /app

# Install system build dependencies if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data

# Fail the image build if production-only imports are missing.  Cloud Run
# worker dispatch uses the same image as the API and requires the v2 client.
RUN python -c "import src.main; import src.worker; from google.cloud import run_v2; print('production-import-smoke-ok')"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/status')" || exit 1

# Cloud Run injects the listening port through the PORT environment variable.
# Keep 8000 as the local Docker default.
CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
