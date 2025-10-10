FROM python:3.13-slim

# Install build dependencies and playwright dependencies
RUN apt-get update && apt-get install -y \
    binutils \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set working directory
WORKDIR /app

# Copy project files
COPY pyproject.toml uv.lock ./
COPY src ./src

# Sync dependencies
RUN uv sync

# Install playwright and download chromium
RUN uv run python -m playwright install chromium

# Build the executable
RUN uv run build

# The executable will be in /app/dist/pyinstaller-test
CMD ["sh", "-c", "cp /app/dist/* /output/"]
