FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN useradd --create-home --uid 10001 bot

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY scripts/healthcheck.py ./scripts/healthcheck.py

USER bot
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python scripts/healthcheck.py

# Migrations (index creation) run automatically at startup, before the bot begins polling.
CMD ["python", "-m", "app.main"]
