FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install poetry==1.8.2

# Copy dependency files first (layer cache)
COPY pyproject.toml poetry.lock* ./

# Install deps (no venv — we're in Docker)
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --no-root

# Copy source
COPY . .

EXPOSE 8000
