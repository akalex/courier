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

This project is designed to run with WAHA as a sidecar service. The simplest deployment on a small VM is:

1. Create a VM and install Docker
2. Clone this repository on the VM
3. Copy `.env.example` to `.env` and fill in the real values
4. Start both services with Docker Compose

```bash
cp .env.example .env
# edit .env with the real WAHA values

docker compose up -d --build
```

The runtime configuration is intentionally environment-based, so credentials never need to live in Git.

Example `.env`:

```env
TRANSPORT=waha
WAHA_URL=http://waha:3000
WAHA_API_KEY=your-key
WAHA_SESSION=default
SOURCE_CHAT_ID=120363123456789012@g.us
TARGET_CHAT_ID=120363987654321098@g.us
LOG_LEVEL=INFO
LISTEN_PORT=8000
```

After the services start, open the WAHA web UI at `http://<vm-host>:3000` and complete the WhatsApp login flow. Then Courier will begin forwarding matching messages.

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
