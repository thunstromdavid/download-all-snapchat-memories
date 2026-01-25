FROM python:3.11-slim

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .

# Create data directory mount point
RUN mkdir -p /data

# Set the entrypoint
ENTRYPOINT ["python", "main.py"]
