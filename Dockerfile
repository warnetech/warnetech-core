# Use official lightweight Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements specification first to leverage Docker cache layers
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code into container
COPY . .

# Create persistent storage directories for RAG data
RUN mkdir -p /app/data/rag_memory

# Expose ports if running network bridges or MCP servers
EXPOSE 8080

# Define execution entry point
ENTRYPOINT ["python3", "scripts/main.py"]
