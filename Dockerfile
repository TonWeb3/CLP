# ClipForge Dockerfile
FROM python:3.11-slim-bookworm

# Install system dependencies (ffmpeg for audio/video, libglib2.0-0 and libgl1 for OpenCV face tracking)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    libglib2.0-0 \
    libgl1 \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CLIPFORGE_HOST=0.0.0.0 \
    CLIPFORGE_PORT=8120 \
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
EXPOSE 8120

# Start ClipForge
CMD ["python", "main.py"]
