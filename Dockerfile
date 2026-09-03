FROM python:3.11-slim AS base

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . /app/

ENV PYTHONPATH=/app
# Unbuffered stdout/stderr — without this, `print()` output from scripts run
# via `docker exec` (or redirected into a cron log file) doesn't show up
# until the process exits, which looks indistinguishable from a hang.
ENV PYTHONUNBUFFERED=1

# Test image: production deps plus requirements-dev (pytest, pyflakes).
# Build with `docker build --target test .` — see README. Deliberately NOT the
# last stage: Docker builds the final stage by default, so keeping `app` last
# means an unqualified `docker build` / `docker compose build` still produces
# the production image and can never accidentally ship dev dependencies.
#
# This exists because tests/test_send_notifications.py uses `X | Y` union
# syntax, which needs Python 3.10+ and cannot be collected on a 3.9 host, and
# pytest is absent from the production image — so that file had no environment
# it could run in at all.
FROM base AS test
RUN pip install --no-cache-dir -r requirements-dev.txt
CMD ["python3", "-m", "pytest", "tests/", "-q"]

FROM base AS app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
