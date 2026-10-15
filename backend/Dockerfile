FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ /app/backend/
COPY ai_vision/ /app/ai_vision/
COPY ml_pose/ /app/ml_pose/
RUN useradd --uid 10001 --create-home fitness && mkdir -p /data/media && chown -R fitness:fitness /data /app/backend
WORKDIR /app/backend
ENV PYTHONUNBUFFERED=1 DB_PATH=/data/fitness.db MEDIA_DATA_ROOT=/data/media
USER fitness
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)" || exit 1
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log", "--timeout-graceful-shutdown", "90"]
