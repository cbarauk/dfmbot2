FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables to prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY main.py .

# Create the data directory (Railway volume will overlay this)
RUN mkdir -p /data

# Run the bot
CMD ["python", "main.py"]