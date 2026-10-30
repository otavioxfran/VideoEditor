FROM python:3.11-slim

# Prevent python from buffering stdout/stderr and writing .pyc files
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SOURCE_VIDEOS_DIR=/app/videos \
    JOBS_DIR=/app/jobs \
    SERVER_HOST=0.0.0.0 \
    SERVER_PORT=8765

# Install system dependencies: FFmpeg and libsndfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first for caching layer
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Ensure storage directories exist
RUN mkdir -p /app/videos /app/jobs

EXPOSE 8765

# Run the local Web Studio
CMD ["python", "cli.py", "studio"]
