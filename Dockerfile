# ClipForge Dockerfile
FROM python:3.11-slim-bookworm

# Install system dependencies (ffmpeg is required for audio/video processing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CLIPFORGE_HOST=0.0.0.0 \
    CLIPFORGE_PORT=8000 \
    CLIPFORGE_DATA=/data

WORKDIR /app

# Install Python dependencies first for caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Create persistent data volume
RUN mkdir -p /data
VOLUME /data

# Expose web interface port
EXPOSE 8000

# Start ClipForge
CMD ["python", "main.py"]
