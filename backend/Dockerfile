# Dockerfile for Smart Fitness Backend
# Exposes HTTP 8080 (API) and HTTPS 8443 (camera)

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libgl1-mesa-glx libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend/ /app/backend/
# Copy sibling ai_vision package so backend's `../ai_vision` resolves
COPY ai_vision/ /app/ai_vision/

WORKDIR /app/backend

# Expose ports
EXPOSE 8080 8443

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# Run with uvicorn (no HTTPS by default; mount certs + override CMD if needed)
CMD ["python", "main.py"]
