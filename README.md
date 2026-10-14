# Courier

Courier is an async Python forwarder for WhatsApp group messages. It watches one source chat, applies optional sender and keyword filters, and relays matching messages to a target chat.

The project is built with a transport abstraction so the message-forwarding logic stays separate from the WhatsApp implementation. The current adapter uses WAHA as the WhatsApp backend.

## Features

- Forward messages from one WhatsApp chat to another
- Filter by sender ID/name and/or keyword matches
- Deduplicate recent incoming message IDs to avoid loops
- Keep attachment handling isolated to the adapter layer
- Run as a normal Python service or inside Docker

## Requirements

- Python 3.14+
- uv
- A running WAHA instance reachable by the app

## Installation

```bash
cd /workspaces/courier
uv sync --dev
```

## Configuration

Courier reads settings from `config.yaml` and environment variables. Environment values override the YAML file.

Example `config.yaml`:

```yaml
transport: waha
source_chat_id: "120363123456789012@g.us"
target_chat_id: "120363987654321098@g.us"
listen_port: 8000
log_level: INFO
waha_url: "http://localhost:3000"
waha_api_key: "your-wahtsapp-api-key"
waha_session: "default"

filters:
  keywords:
    - "vip"
    - "alert"
  senders:
    - "+1234567890"
    - "alice@example.com"
```

Supported environment variables:

- `TRANSPORT`
- `SOURCE_CHAT_ID`
- `TARGET_CHAT_ID`
- `LISTEN_PORT`
- `LOG_LEVEL`
- `WAHA_URL`
- `WAHA_API_KEY`
- `WAHA_SESSION`
- `CONFIG_PATH`

## Run locally

```bash
cd /workspaces/courier
uv run python -m courier.main
```

You can set overrides inline if needed:

```bash
LOG_LEVEL=DEBUG SOURCE_CHAT_ID="..." TARGET_CHAT_ID="..." \
WAHA_URL="http://localhost:3000" WAHA_API_KEY="..." \
uv run python -m courier.main
```

## Docker

Build the image:

```bash
cd /workspaces/courier
docker build -t courier .
```

Run it with required environment variables:

```bash
docker run --rm -it \
  -e TRANSPORT=waha \
  -e SOURCE_CHAT_ID="..." \
  -e TARGET_CHAT_ID="..." \
  -e WAHA_URL="http://host.docker.internal:3000" \
  -e WAHA_API_KEY="..." \
  courier
```

## VM deployment with WAHA

GitHub Actions runs CI on pushes and pull requests. After CI passes on `main`, it builds and publishes multi-platform images to GitHub Container Registry:

- `ghcr.io/akalex/courier:latest`
- `ghcr.io/akalex/courier:<commit-sha>`

Image publishing does not deploy automatically. To deploy manually on the Oracle VM:

1. Install Docker and clone this repository on the VM.
2. Copy `.env.example` to `.env` and fill in the real runtime values.
3. Make the GHCR package public, or authenticate Docker on the VM with a GitHub token that has `read:packages`.
4. Set `COURIER_TAG` in `.env` to `latest` or the commit SHA you want to deploy.
5. Start WAHA, then pull and start Courier:

```bash
cp .env.example .env
# Edit .env with the real values, including COURIER_TAG.

docker compose up -d waha
docker compose pull courier
docker compose up -d courier
```

To upgrade later, change `COURIER_TAG` in `.env` and run `docker compose pull courier && docker compose up -d courier`. Using a commit-SHA tag makes rollbacks reproducible.

Runtime credentials stay on the VM in the ignored `.env` file, not in Git. WAHA's web UI is bound to the VM's loopback interface; reach it through an SSH port forward rather than exposing port `3000` publicly.

> The WAHA adapter currently does not receive inbound webhook events. The image pipeline and deployment can be exercised, but end-to-end message forwarding requires implementing that receive path first.

## Project structure

```text
courier/
  adapters/
    __init__.py
    waha.py
  __init__.py
  config.py
  domain.py
  filters.py
  forwarder.py
  main.py
  ports.py
```

## Development checks

```bash
uv sync --dev
uv run pytest -q
uv run ruff check .
```

## Notes

- The WAHA adapter is intentionally isolated in `courier/adapters/waha.py`.
- Filters are pluggable and can be combined with `AllOf` or `AnyOf` logic.
- The service keeps a bounded recent-message cache to reduce accidental forwarding loops.
