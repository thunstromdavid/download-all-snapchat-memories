FROM python:3.11-slim

WORKDIR /app

# ffmpeg is used to embed dates/locations into videos (no re-encoding)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .

# Create data directory mount point
RUN mkdir -p /data

# Set the entrypoint
ENTRYPOINT ["python", "main.py"]
