# Courier

A small async Python service that watches one WhatsApp group and forwards messages to another, filtered by sender and keywords. WhatsApp access goes through the WAHA sidecar in Docker, and the core only depends on a `Transport` interface, so swapping WhatsApp for another messenger means writing a single adapter.

## Development

```bash
uv sync
LOG_LEVEL=DEBUG uv run python -m bot.main
```

## Run with Docker

```bash
cp .env.example .env   # set WAHA_API_KEY
uv lock                # generates uv.lock (needed by the Dockerfile)
docker compose up -d --build
```