# Courier

![Courier logo](./assets/courier-logo.svg)

[![CI](https://github.com/akalex/courier/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/akalex/courier/actions/workflows/ci.yml)
![Python 3.14+](https://img.shields.io/badge/Python-3.14%2B-3776AB?logo=python&logoColor=white)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Container platforms](https://img.shields.io/badge/container-linux%2Famd64%20%7C%20arm64-2496ED?logo=docker&logoColor=white)

Courier is an asynchronous message forwarder. Each Courier process watches one WhatsApp source chat, applies optional sender and keyword filters, and relays eligible messages to one target chat on WhatsApp or Matrix (including Element).

Courier receives WhatsApp messages from WAHA over its HTTP webhook and sends them through a separately configured destination adapter. One process/container manages one source-to-destination route.

## Contents

- [How it works](#how-it-works)
- [Routing examples](#routing-examples)
- [Message behavior and limitations](#message-behavior-and-limitations)
- [Configuration reference](#configuration-reference)
- [Run locally](#run-locally)
- [Run with Docker](#docker)
- [Deploy on an Oracle VM](#vm-deployment-with-waha)
- [Operate and troubleshoot](#operate-and-troubleshoot)
- [Migrating from 0.8.x](#migrating-from-08x)
- [Development](#development)
- [Project structure](#project-structure)

## Features

- Forward message text and supported media from WhatsApp to WhatsApp or a Matrix room
- Select whether a route forwards text, media, or both, and optionally restrict media categories
- Filter by sender ID/name and/or included keywords, with optional excluded keywords
- Deduplicate up to 1,000 handled message IDs in memory
- Download media only for source-chat messages that pass the filters and match the selected media types
- Retry failed webhook deliveries through WAHA's configured retry policy
- Run locally or as a container, including on ARM64 and x86-64

## Requirements

- Python 3.14+
- uv
- A running WAHA instance reachable by the app

## Installation

```bash
cd /workspaces/courier
uv sync --dev
```

## How it works

1. Courier reads its container's YAML config file (see [Configuration reference](#configuration-reference)) and WAHA's API key from `WAHA_API_KEY`. It reports every problem in the file at once and does not start until they are fixed.
2. It connects to WAHA at `waha.url` using that API key.
3. It finds or creates the configured WAHA session, registers its webhook for the events this container handles, and starts the session if needed. A forwarder subscribes to `message` and `message.any`; a health monitor, or a forwarder with `health_alerts: true`, subscribes to `session.status`. Courier sets exactly these events on its own webhook entry and replaces any others on that URL. When upgrading from the old `courier` container, it removes the obsolete `http://courier:8000/webhook` entry from the existing session; no new session or QR scan is needed.
4. WAHA sends events to Courier's `POST /webhook` endpoint.
5. Courier ignores events outside the configured source chat and applies filters before downloading any media. It downloads media only when it can match the selected media types, then sends the message to the configured destination.

A container with `role: health-monitor` sends health notifications when the WAHA session stops, fails, or needs QR authentication, or when the WAHA API becomes unavailable. It checks worker/session health every 30 seconds and sends a recovery notification after service resumes. Alerts go to its `alert_chat_id`; for a WAHA destination, use the recipient's WAHA chat ID, such as `15551234567@c.us`.

In the default multi-route Compose deployment, `courier-health` is the only container that sends health notifications. It subscribes only to `session.status` and never receives or downloads messages. The forwarding routes use `role: forwarder` without `health_alerts`, so they do not subscribe to `session.status`. A standalone forwarder can watch health as well by setting `health_alerts: true` and an `alert_chat_id`.

Courier also exposes `GET /health` on the same listen port. The webhook server binds to all container interfaces; in a Compose deployment, WAHA should reach it through the private service network. Do not expose the webhook port to the public internet unless it is protected by an appropriate network or authentication layer.

The WhatsApp source requires two separate WAHA network paths:

- Courier must be able to call WAHA at `waha.url` for session setup and media downloads (and outgoing messages when WAHA is the destination).
- WAHA must be able to call Courier at `webhook_url` to deliver incoming events.

If WAHA runs on another VM, both paths must be routable between the machines. Prefer a private network/VPN or TLS-protected endpoints with firewall rules allowing only the required traffic. Do not publish WAHA's unauthenticated or unencrypted API port directly to the internet.

## Routing examples

Courier currently receives messages from WhatsApp through WAHA. Select the destination with `destination_transport` and set `target_chat_id` to the destination chat or room. Each example below is a complete config file for a container running next to WAHA on the same machine; WAHA's API key comes from `WAHA_API_KEY`, never from the file.

### 1. WhatsApp → WhatsApp

Use the WhatsApp chat IDs returned by WAHA for both the source and target:

```yaml
role: forwarder
webhook_url: "http://localhost:8000/webhook"

waha:
  url: "http://localhost:3000"
  session: "default"

destination_transport: waha
source_chat_id: "120363123456789012@g.us"
target_chat_id: "120363987654321098@g.us"
```

The message is sent to the target WhatsApp chat by the linked WhatsApp account.

### 2. WhatsApp → Element

Element connects to a Matrix homeserver, so use the homeserver URL and a Matrix room ID. The Matrix account represented by the access token must already have joined that room:

```yaml
role: forwarder
webhook_url: "http://localhost:8000/webhook"

waha:
  url: "http://localhost:3000"
  session: "default"

destination_transport: matrix
source_chat_id: "120363123456789012@g.us"
target_chat_id: "!roomid:example.org"

matrix:
  homeserver_url: "https://matrix.example.org"
  access_token: "your-matrix-access-token"
```

The Matrix access token is a secret, so keep this file private (`chmod 600 config.yaml`). Text and supported media are sent to the configured room.

For either route, add `filters` to the same file if you want to filter messages (see [Configuration reference](#configuration-reference)). In Docker deployments, WAHA must be able to reach Courier at `webhook_url`; use a network-reachable hostname rather than `localhost` when the services run in separate containers.

## Message behavior and limitations

- Text and captions are forwarded without a sender-name prefix. WhatsApp destinations send as the Courier-linked account; neither WhatsApp nor Matrix preserves the original sender identity or all source-side formatting as native metadata.
- Courier forwards at most one attachment per message. WhatsApp destinations use WAHA's `sendImage`, `sendVideo`, or `sendFile` endpoint. Matrix destinations upload media to the homeserver, then send an image, video, audio, or file event to the configured room.
- `forward_content` can limit a route to `text`, `media`, or both (the default). `media_types` can restrict attachments to `image`, `video`, `audio`, and/or `file`. A media-only route drops messages without an eligible attachment and does not forward captions.
- If WAHA reports that inline WEBJS video sending requires the `devlikeapro/waha:chrome` image, Courier retries the video as a file attachment. This fallback may not be downloadable as a video in every WhatsApp client. WAHA's `chrome` image is amd64-only; the ARM image used on ARM64 hosts does not support inline WEBJS video. For reliable inline video delivery, run WAHA on an amd64 host with the `chrome` image.
- With text forwarding enabled, media download failures, unsafe media URLs, and empty media do not discard the text/caption. An attachment larger than `max_attachment_size` is skipped; Courier sends the text/caption instead. The size limit defaults to 20 MiB. Courier stops a media download as soon as it exceeds this limit, so the setting also bounds the memory a download can use.
- When a message has nothing to send except media, and that media download fails temporarily (a WAHA server error, request timeout, or connection failure), Courier answers the webhook with HTTP 500 so WAHA redelivers the message under its retry policy. Media that is missing (other HTTP errors), unsafe, empty, or too large is skipped without a retry.
- Media is downloaded only for messages from the source chat that pass the filters, and only when the attachment can match `media_types`. A generic binary file (`application/octet-stream`) without a video extension is downloaded when either `video` or `file` is selected, because only its bytes show whether it is an MP4.
- Media URLs are fetched only when they resolve to the configured WAHA origin. Loopback media URLs are rewritten to the configured WAHA host. This prevents Courier's WAHA API-key header from being sent to an unrelated host; URLs on another hostname/origin are skipped.
- Included keywords search the message text/caption using case-insensitive substring matching. A keyword filter accepts a message if any configured keyword matches.
- A route can use `message_transform` to extract named regex groups from the text selected for forwarding and render a replacement template. The first match is used; when there is no match, the text is omitted while configured eligible media continues to be forwarded. For example, `група FPV «HAOS» ... FPV “PEGAS 10”` extracts `HAOS` (not the later aircraft model), while `екіпаж FPV «ХЕНТАЙ»` extracts `ХЕНТАЙ`.
- Excluded keywords are also case-insensitive substring matches against the text/caption. If any excluded keyword appears, the message is rejected even when it matches an included keyword or sender. Exclusions are vetoes; included keywords and sender criteria retain their OR behavior.
- Sender filters match the normalized sender ID or sender name. Normalization is case-insensitive and strips a leading `+` and any `@...` suffix.
- If both keyword and sender filters are configured, they are combined with **OR**: matching either filter is sufficient. With no filters configured, every message from the source chat is eligible. A media-only message has no matching keyword text, so it passes a keyword filter only if there is a matching caption.
- Courier ignores messages sent by its linked WhatsApp account by default. Enable `include_from_me` only when needed, and keep source and target chat IDs different to avoid forwarding loops.
- WAHA can deliver the same message as both a `message` and a `message.any` event; Courier processes it once. The deduplication cache holds at most 1,000 message IDs in memory: messages that were sent, and messages dropped after their media was downloaded. Filtered messages are not cached, since checking them again needs no download. Messages without an ID are never deduplicated. The cache is not persisted and is cleared on restart. A failed send is not cached, so WAHA can retry it. Retries reduce message loss but do not guarantee exactly-once delivery: a send may have succeeded at WAHA even if Courier did not receive its response, and a later retry can create a duplicate.
- Courier configures WAHA webhook retries as exponential with `delaySeconds: 2` and `attempts: 10` when adding its webhook or when the existing Courier webhook has no retry configuration. Explicit retry configuration on an existing webhook is preserved. WAHA's retry behavior is not a durable queue.
- Incoming webhook requests have a 1 MiB body limit, a 64 KiB header limit, and a 10-second request timeout. Chunked transfer encoding is not supported.
- During startup, Courier retries WAHA session setup after server errors or connection failures, for up to 12 attempts with a 5-second delay between attempts. Configuration/client errors are surfaced instead of retried.

## Configuration reference

Each Courier container is configured in exactly one place:

| Place | What goes there |
| --- | --- |
| The container's YAML file (`/app/config.yaml` in the container) | Every Courier setting listed below |
| `WAHA_API_KEY` environment variable | WAHA's API key. Under Docker Compose it comes from WAHA's `.env`, so it always matches WAHA's |
| `CONFIG_PATH` environment variable | Which YAML file to read; defaults to `config.yaml` in the working directory (`/app/config.yaml` in the image) |

Courier reads no other environment variables. Settings in the 0.8.x style (`SOURCE_CHAT_ID`, `HEALTH_MONITOR_ONLY`, `LOG_LEVEL`, `waha_url`, …) are rejected at startup with a message saying which key to set instead; see [Migrating from 0.8.x](#migrating-from-08x).

Every file starts with `role`, which decides which keys are allowed:

- `forwarder` forwards messages from one source chat to one target chat. Start from [examples/config-forwarder.yaml](examples/config-forwarder.yaml).
- `health-monitor` watches the WAHA session and sends alerts; it forwards nothing, so it has no chats or filters. Start from [examples/config-health-monitor.yaml](examples/config-health-monitor.yaml).

An unknown or misspelled key, a key that doesn't apply to the role, or an invalid value stops Courier at startup. It lists every problem at once, each naming the file and the key:

```text
Courier cannot start until these config problems are fixed:
/app/config.yaml: `source_chat_id` does not apply to role `health-monitor`
/app/config.yaml: unknown key `filters.keyword`
```

Leaving a key blank (`key:` with no value) is the same as leaving it out.

Keys for both roles:

| Key | Required | Default | What it does |
| --- | --- | --- | --- |
| `role` | yes | — | `forwarder` or `health-monitor`. |
| `webhook_url` | yes | — | URL WAHA calls to deliver events to this container, for example `http://courier-tfl:8000/webhook`. Must be reachable from WAHA and unique per container. |
| `listen_port` | no | `8000` | Port Courier listens on inside the container. Keep it equal to the port in `webhook_url` unless a proxy sits between them. |
| `log_level` | no | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. |
| `waha.url` | no | `http://localhost:3000` | URL Courier uses to call WAHA; `http://waha:3000` under Docker Compose. |
| `waha.session` | no | `default` | WAHA session (the linked WhatsApp account). |
| `destination_transport` | no | `waha` | Where messages and alerts go: `waha` (WhatsApp) or `matrix`. |
| `matrix.homeserver_url` | with `matrix` | — | Matrix homeserver URL. Allowed only with `destination_transport: matrix`. |
| `matrix.access_token` | with `matrix` | — | Access token of the Matrix account that posts. Secret: keep the file private (`chmod 600`). |
| `alert_chat_id` | see note | — | Chat (or Matrix room) that receives health alerts. Required for a health monitor and for a forwarder with `health_alerts: true`; rejected otherwise. It never falls back to `target_chat_id`. |

Forwarder-only keys (rejected on a health monitor):

| Key | Required | Default | What it does |
| --- | --- | --- | --- |
| `source_chat_id` | yes | — | WhatsApp chat messages are forwarded from. |
| `target_chat_id` | yes | — | Chat messages are forwarded to: a WhatsApp chat ID, or a Matrix room ID such as `!roomid:example.org`. |
| `source_transport` | no | `waha` | Where messages come from; only `waha` is supported. |
| `include_from_me` | no | `false` | Also forward messages sent by the linked WhatsApp account. WhatsApp-to-WhatsApp routes then need different source and target chats. |
| `forward_content` | no | `[text, media]` | `text`, `media`, or both. |
| `media_types` | no | `[image, video, audio, file]` | Attachment kinds to forward when forwarding media. |
| `max_attachment_size` | no | `20971520` | Largest attachment in bytes (20 MiB); larger attachments are skipped and the text is still sent. |
| `filters.keywords` | no | empty | Forward a message whose text contains any of these (case-insensitive). A list or a comma-separated string. |
| `filters.exclude_keywords` | no | empty | Never forward a message whose text contains any of these. |
| `filters.senders` | no | empty | Forward a message sent by any of these sender IDs or names. |
| `message_transform` | no | — | Transform text selected by `forward_content` using named regex groups and `$group` placeholders. If text does not match, it is omitted while configured eligible media still forwards. |
| `health_alerts` | no | `false` | Also watch WAHA session health and send alerts to `alert_chat_id`. Normally the health-monitor container does this. |

Filters combine like this: a message must match at least one include criterion (a keyword or a sender), if any are configured, and must not contain an excluded keyword. With only exclusions configured, every message without an excluded keyword is forwarded.

To extract a crew name and send the drafted text with the source message's eligible media, configure:

```yaml
message_transform:
  pattern: '(?:екіпаж|група)\s+FPV\s+[«“"](?P<crew>[^»”"]+)[»”"]'
  template: 'FPV crew "$crew"'
```

This pattern recognizes either `екіпаж FPV` or `група FPV`, with straight quotes, Ukrainian angle quotes (`«...»`), or curly quotes (`“...”`) around the name. It captures the name immediately after that crew label, so later quoted `FPV` model names are not selected. `pattern` is a Python regular expression and must contain at least one named capture group. `template` uses `$name` or `${name}` placeholders matching those group names. Courier uses the first regex match on the text selected for forwarding. If `text` is not selected by `forward_content`, the transform is not applied. If no match is found, text is omitted and eligible media selected by `forward_content` and `media_types` is still forwarded. Existing keyword and sender filters still run against the original source text.

`webhook_url` is the URL WAHA calls to deliver events, so it must be reachable from the WAHA container or network. It is distinct from `waha.url`, which Courier uses to call WAHA. Give each Courier container its own `webhook_url`, typically based on its Compose service name; all containers can share the same WAHA session and internal `listen_port`.

For a Matrix destination, `target_chat_id` (and `alert_chat_id`, if alerts go to Matrix) is a Matrix room ID, and the account behind `matrix.access_token` must already have joined that room.

### Editor support

Courier publishes a JSON Schema for its config files. In VS Code with the [Red Hat YAML extension](https://marketplace.visualstudio.com/items?itemName=redhat.vscode-yaml), make this the first line of a config file to get autocomplete, a description of each key on hover, and errors highlighted as you type:

```text
# yaml-language-server: $schema=https://raw.githubusercontent.com/akalex/courier/0.10.1/courier-config.schema.json
```

Use the version that matches your `COURIER_TAG`. This also works when editing files on the VM over Remote-SSH. The files in [examples/](examples/) point at the schema in this repository.

### Compose deployment

The Compose deployment runs two forwarding routes and a dedicated health monitor, each configured by the YAML file it mounts:

| Service | Config file | Role |
| --- | --- | --- |
| `courier-aircraft-losses` | `config.yaml` | Forwards messages that mention `urgent` or `alert` |
| `courier-tfl` | `config-tfl.yaml` | Forwards only video attachments |
| `courier-health` | `config-health.yaml` | Health monitor; the only container that sends health alerts |

Complete versions of all three files are in [examples/compose/](examples/compose/) and in the [deployment steps](#vm-deployment-with-waha). After changing a file, recreate its container, for example `docker compose up -d --force-recreate courier-health`.

To add another route, for example `courier-blink`: copy a forwarder config to `config-blink.yaml`, set its `webhook_url` to `http://courier-blink:8000/webhook` and its chats, then add a service that extends the shared template and mounts the file:

```yaml
  courier-blink:
    <<: *courier-common
    container_name: courier-blink
    volumes:
      - ./config-blink.yaml:/app/config.yaml:ro
```

## Run locally

Create `config.yaml` from [examples/config-forwarder.yaml](examples/config-forwarder.yaml), set `waha.url` to where WAHA runs (for example `http://localhost:3000`), then run:

```bash
cd /workspaces/courier
WAHA_API_KEY="..." uv run python -m courier.main
```

To run another file, add `CONFIG_PATH=config-tfl.yaml`. The `webhook_url` must be reachable from WAHA: when WAHA runs in Docker and Courier runs on the host, use a host address reachable from the WAHA container rather than `localhost` from WAHA's point of view.

## Docker

The following is a minimal example for a single Docker host. For a production setup with persistent WAHA data and credentials, use the [VM deployment walkthrough](#vm-deployment-with-waha).

Build the image:

```bash
cd /workspaces/courier
docker build -t courier .
```

Run it with your config file mounted and WAHA's API key:

```bash
docker run --rm -it \
  -p 8000:8000 \
  -v "$PWD/config.yaml:/app/config.yaml:ro" \
  -e WAHA_API_KEY="..." \
  courier
```

In that `config.yaml`, set `waha.url: http://host.docker.internal:3000` and `webhook_url: http://host.docker.internal:8000/webhook`. On Linux, `host.docker.internal` may need Docker's `--add-host=host.docker.internal:host-gateway` option. Alternatively, put WAHA and Courier on the same user-defined Docker network and use the Courier container name as the webhook hostname.

## VM deployment with WAHA

GitHub Actions runs CI on pushes and pull requests. After CI passes on `main`, it builds and publishes multi-platform images to GitHub Container Registry:

- `ghcr.io/akalex/courier:latest`
- `ghcr.io/akalex/courier:<commit-sha>`

Image publishing does not deploy automatically. You do not need to clone this private repository on the VM: deploy the published image using a Compose file created there.

The Oracle `VM.Standard.A1.Flex` shape uses ARM64. Courier's published image includes an ARM64 build and Docker selects it automatically. The Compose file defaults to WAHA's ARM NOWEB image, `devlikeapro/waha:noweb-arm`, with `WHATSAPP_DEFAULT_ENGINE=NOWEB`. Override `WAHA_IMAGE` and `WHATSAPP_DEFAULT_ENGINE` in `.env` to select another image or engine. Use a separate WAHA session name when testing another engine so the existing session remains available for rollback, and pair the new session from the Dashboard. WAHA's `devlikeapro/waha:chrome` image supports inline WEBJS video but is amd64-only; it will not run natively on ARM64.

1. Install Docker with the Compose plugin on the Oracle VM.
2. If the GHCR package is private, create a GitHub token with `read:packages` and authenticate Docker on the VM. If the package is public, skip this step:

   ```bash
   docker login ghcr.io -u YOUR_GITHUB_USERNAME
   ```

   Enter the token when prompted for the password; do not put it in the Compose file or `.env`.
3. Create a deployment directory and generate WAHA's production credentials:

   ```bash
   mkdir -p /opt/courier
   cd /opt/courier
   ```

   Generate WAHA's credentials in the deployment directory. If an incomplete `.env` already exists, back it up first; `--force` replaces the whole file:

   ```bash
   if [ -f .env ]; then cp -p .env .env.backup; fi
   docker run --rm -v "$PWD":/app/env devlikeapro/waha:arm init-waha /app/env --force
   chmod 600 .env
   ```

   This generates a new WAHA API key and dashboard/Swagger logins. Use strong, unique values for the API key and passwords. Keep the generated `.env` private; Courier containers receive the same API key from it.
4. `.env` belongs to WAHA. The only Courier line it holds is the image tag every Courier container runs. Add it to the generated file:

   ```bash
   echo "COURIER_TAG=0.10.1" >> .env
   ```

   Use an exact version for a reproducible deployment, or `latest` to follow the newest release. Everything else Courier needs goes in its YAML files; compare with [.env.example](.env.example).

5. Create one YAML file per Courier container in the same directory. Each file fully describes its container; the files below match [examples/compose/](examples/compose/). Use placeholder chat IDs for now and replace them after linking WhatsApp (step 9). The first line enables [editor support](#editor-support).

   `config.yaml` (`courier-aircraft-losses`, forwards messages that mention `urgent` or `alert`):

   ```yaml
   # yaml-language-server: $schema=https://raw.githubusercontent.com/akalex/courier/0.10.1/courier-config.schema.json
   role: forwarder
   webhook_url: http://courier-aircraft-losses:8000/webhook

   waha:
     url: http://waha:3000
     session: default

   source_chat_id: "120363000000000011@g.us"
   target_chat_id: "120363000000000012@g.us"

   filters:
     keywords:
       - urgent
       - alert
   ```

   Keywords are case-insensitive substrings; any one matching keyword is enough. Remove `filters` to forward every message from the source chat.

   `config-tfl.yaml` (`courier-tfl`, forwards only video attachments, without captions):

   ```yaml
   # yaml-language-server: $schema=https://raw.githubusercontent.com/akalex/courier/0.10.1/courier-config.schema.json
   role: forwarder
   webhook_url: http://courier-tfl:8000/webhook

   waha:
     url: http://waha:3000
     session: default

   source_chat_id: "120363000000000021@g.us"
   target_chat_id: "120363000000000022@g.us"

   forward_content: [media]
   media_types: [video]
   ```

   `config-health.yaml` (`courier-health`, the only container that sends health alerts):

   ```yaml
   # yaml-language-server: $schema=https://raw.githubusercontent.com/akalex/courier/0.10.1/courier-config.schema.json
   role: health-monitor
   webhook_url: http://courier-health:8000/webhook
   alert_chat_id: "15551234567@c.us"

   waha:
     url: http://waha:3000
     session: default
   ```

   If a file holds a Matrix access token, keep it private with `chmod 600 <file>`.

6. Create `docker-compose.yml` in the same directory:

   ```bash
   cat > docker-compose.yml <<'EOF'
   x-courier-common: &courier-common
     image: ghcr.io/akalex/courier:${COURIER_TAG:-latest}
     restart: unless-stopped
     depends_on:
       - waha
     environment:
       WAHA_API_KEY: ${WAHA_API_KEY:?Set WAHA_API_KEY in .env}

   services:
     waha:
       image: ${WAHA_IMAGE:-devlikeapro/waha:noweb-arm}
       restart: unless-stopped
       ports:
         - "127.0.0.1:3000:3000"
       volumes:
         - ./waha-data:/app/.sessions
       environment:
         WAHA_API_KEY: ${WAHA_API_KEY:?Set WAHA_API_KEY in .env}
         WAHA_DASHBOARD_USERNAME: ${WAHA_DASHBOARD_USERNAME:?Set WAHA_DASHBOARD_USERNAME in .env}
         WAHA_DASHBOARD_PASSWORD: ${WAHA_DASHBOARD_PASSWORD:?Set WAHA_DASHBOARD_PASSWORD in .env}
         WHATSAPP_SWAGGER_USERNAME: ${WHATSAPP_SWAGGER_USERNAME:?Set WHATSAPP_SWAGGER_USERNAME in .env}
         WHATSAPP_SWAGGER_PASSWORD: ${WHATSAPP_SWAGGER_PASSWORD:?Set WHATSAPP_SWAGGER_PASSWORD in .env}
         WHATSAPP_DEFAULT_ENGINE: ${WHATSAPP_DEFAULT_ENGINE:-NOWEB}
         WAHA_PRINT_QR: "False"

     courier-aircraft-losses:
       <<: *courier-common
       container_name: courier-aircraft-losses
       volumes:
         - ./config.yaml:/app/config.yaml:ro

     courier-tfl:
       <<: *courier-common
       container_name: courier-tfl
       volumes:
         - ./config-tfl.yaml:/app/config.yaml:ro

     courier-health:
       <<: *courier-common
       container_name: courier-health
       volumes:
         - ./config-health.yaml:/app/config.yaml:ro
   EOF
   ```

   Courier services set no environment of their own: each one's settings are in the YAML file it mounts. To add a route, see [Compose deployment](#compose-deployment).

7. Start the services:

   ```bash
   docker compose pull
   docker compose up -d
   ```

   Each container registers its own webhook in the shared WAHA session: the forwarders for message events, `courier-health` for `session.status`.

8. To access the WAHA dashboard and Swagger without an SSH tunnel, put Nginx on the VM in front of WAHA. Allow inbound TCP ports 80 and 443 in the Oracle Cloud NSG or Security List and in the VM firewall if enabled. Port 80 is needed for Let's Encrypt validation; port 443 serves HTTPS. Do not open port 3000 publicly.

   If the VM uses iptables and its `INPUT` chain rejects other incoming connections, add allow rules for HTTP and HTTPS. These commands insert the rules before existing rules (including a final REJECT); they do not alter SSH access:

   ```bash
   sudo iptables -I INPUT 1 -p tcp --dport 80 -j ACCEPT
   sudo iptables -I INPUT 1 -p tcp --dport 443 -j ACCEPT
   sudo iptables -L INPUT -n --line-numbers
   ```

   Confirm both ACCEPT rules appear before any REJECT rule. These rules are runtime-only unless saved. On Ubuntu, install `iptables-persistent` and save the current rules so they survive a reboot:

   ```bash
   sudo apt install -y iptables-persistent
   sudo netfilter-persistent save
   ```

   If the package installer asks whether to save the current IPv4/IPv6 rules, choose to save them only after confirming the current rules retain SSH access. Do not flush or replace the firewall ruleset. Apply equivalent IPv6 rules only if the VM is publicly reachable over IPv6. OCI ingress rules are still required; host iptables rules do not replace them.

   Install Nginx:

   ```bash
   sudo apt update
   sudo apt install -y nginx snapd
   sudo systemctl enable --now nginx
   sudo mkdir -p /var/www/certbot
   sudo snap install --classic certbot
   sudo ln -s /snap/bin/certbot /usr/local/bin/certbot
   certbot --version
   ```

   Create an HTTP virtual host (for Ubuntu, `/etc/nginx/sites-available/waha`; on other distributions use the appropriate Nginx configuration directory). Replace `waha.example.com` with your public FQDN, or with the VM's public IP if using an IP certificate:

   ```nginx
   server {
       listen 80;
       listen [::]:80;
       server_name waha.example.com;

       location ^~ /.well-known/acme-challenge/ {
           root /var/www/certbot;
           default_type text/plain;
       }

       location / {
           return 301 https://$host$request_uri;
       }
   }
   ```

   On Ubuntu, enable the site and check/reload Nginx:

   ```bash
   sudo ln -s /etc/nginx/sites-available/waha /etc/nginx/sites-enabled/waha
   sudo nginx -t
   sudo systemctl reload nginx
   ```

   Obtain a certificate using **one** of the following methods:

   - **Public FQDN:** The name must resolve publicly to this VM, and inbound port 80 must be reachable. Install Certbot using your OS's recommended method, then request a certificate:

     ```bash
     sudo certbot certonly --webroot -w /var/www/certbot -d waha.example.com
     ```

   - **Public IP only:** Install Certbot 5.4 or newer (for example, the current Certbot Snap package), then request a short-lived IP certificate. Replace the placeholder with the VM's actual public IP:

     ```bash
     sudo certbot certonly \
       --webroot --webroot-path /var/www/certbot \
       --preferred-profile shortlived \
       --ip-address <VM_PUBLIC_IP>
     ```

     IP certificates are valid for about 160 hours. Certbot's Nginx plugin does not yet install IP certificates automatically, so configure the HTTPS server block below manually and ensure automatic renewal is working.

   Run `sudo certbot certificates` and use the exact certificate and key paths it reports. Add this HTTPS server block to the same Nginx site. Replace `waha.example.com` with the public FQDN or actual public IP, and replace the certificate paths with those reported by Certbot:

   ```nginx
   server {
       listen 443 ssl;
       listen [::]:443 ssl;
       server_name waha.example.com;

       ssl_certificate /etc/letsencrypt/live/CERTIFICATE_NAME/fullchain.pem;
       ssl_certificate_key /etc/letsencrypt/live/CERTIFICATE_NAME/privkey.pem;
       ssl_protocols TLSv1.2 TLSv1.3;

       location / {
           proxy_pass http://127.0.0.1:3000;
           proxy_http_version 1.1;
           proxy_set_header Upgrade $http_upgrade;
           proxy_set_header Connection "upgrade";
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
           proxy_set_header X-Forwarded-Ssl on;
           proxy_buffering off;
           client_max_body_size 0;
           proxy_read_timeout 36000s;
           proxy_redirect off;
       }
   }
   ```

   Check and reload Nginx:

   ```bash
   sudo nginx -t
   sudo systemctl reload nginx
   ```

   Install a Certbot deploy hook so Nginx reloads after either certificate type is renewed:

   ```bash
   sudo install -d /etc/letsencrypt/renewal-hooks/deploy
   sudo tee /etc/letsencrypt/renewal-hooks/deploy/reload-nginx >/dev/null <<'EOF'
   #!/bin/sh
   systemctl reload nginx
   EOF
   sudo chmod 755 /etc/letsencrypt/renewal-hooks/deploy/reload-nginx
   ```

   Confirm Certbot's automatic renewal timer is enabled and test renewal with `sudo certbot renew --dry-run`. This is especially important for IP certificates because of their short lifetime. Keep port 3000 bound to `127.0.0.1` as in the Compose file; expose only ports 80/443.

   Open `https://waha.example.com/dashboard` (or `https://<VM_PUBLIC_IP>/dashboard` when using an IP certificate), sign in with the generated Dashboard credentials, connect using the generated WAHA API key, and scan the QR code. Swagger is at the same base URL followed by `/swagger`; use the generated Swagger username and password as well as the API key when prompted. This makes the WAHA administration interface publicly reachable, so use strong unique credentials and share the URL only with authorized users.

9. **Find chat IDs.** Open WAHA's Swagger UI at `https://waha.example.com/swagger` (or the IP URL), authorize with the generated API key, then call `GET /api/default/chats` (replace `default` with your `waha.session` if different). Find each route's source and destination groups by name and copy each `id` exactly as returned; group IDs typically end in `@g.us`. Put them in `source_chat_id` and `target_chat_id` in each route's YAML file, and the alert recipient in `alert_chat_id` in `config-health.yaml`. Then recreate the Courier containers, including any additional routes you added:

   ```bash
   cd /opt/courier
   docker compose up -d --force-recreate courier-aircraft-losses courier-tfl courier-health
   ```

## Operate and troubleshoot

Run these commands from the deployment directory containing `docker-compose.yml`:

```bash
docker compose ps
docker compose logs --tail=100 courier-aircraft-losses courier-tfl courier-health
docker compose logs --tail=100 waha
docker compose restart courier-aircraft-losses courier-tfl courier-health
```

After editing a container's YAML file, recreate that container so it reads the file again:

```bash
docker compose up -d --force-recreate courier-tfl
```

After changing `.env` (WAHA's settings or `COURIER_TAG`), recreate everything with `docker compose up -d --force-recreate`.

If Courier rejects its config, the container exits and its log lists every problem, each naming the file and the key. Inside a container the file is always `/app/config.yaml`; the service name at the start of each `docker compose logs` line tells you which host file that is (`courier-health` mounts `config-health.yaml`):

```text
courier-health  | Courier cannot start until these config problems are fixed:
courier-health  | /app/config.yaml: `source_chat_id` does not apply to role `health-monitor`
```

Each Courier container uses one WAHA session and has one role. To use another session or route at the same time, run another Courier container with its own YAML file and a distinct reachable `webhook_url`. Each container can listen on port `8000` internally; host-published ports must be unique if publishing them directly.

| Symptom | Check |
| --- | --- |
| Courier exits during startup with config problems | Read the problem lines in its log (see above); each names the key to fix in that container's YAML file. |
| Courier cannot connect to WAHA | Check `waha.url`, container DNS/networking, WAHA readiness, and that `WAHA_API_KEY` in `.env` matches the WAHA instance. Review both Courier and WAHA logs. |
| WAHA session stays stopped or requires linking | Open WAHA Dashboard, select the session named in `waha.session`, and scan its QR code. A different session name refers to a separate WAHA session; changing the name does not rename or delete the old session. |
| Messages do not arrive at Courier | Verify WAHA can reach `webhook_url`, that it points to `POST /webhook`, and that the Courier port is available on the shared network. |
| Messages arrive but are not forwarded | Verify the exact `source_chat_id`, the `filters`, and `include_from_me`. Courier logs filter decisions at INFO and raw sender details at DEBUG (`log_level: DEBUG`). |
| Health alerts do not arrive | Only a container with `role: health-monitor`, or a forwarder with `health_alerts: true`, sends them, to its `alert_chat_id`. Check that `courier-health` is running and its log says it started as a health monitor. |
| WAHA reports webhook delivery failures | Inspect Courier logs for forwarding exceptions and WAHA logs for retry status. Ensure the target chat exists and Courier's WAHA API key is valid. |
| Media is absent but text arrives | Check WAHA's media URL and network accessibility, whether the resolved URL has the same origin as `waha.url`, the file size limit, and WAHA endpoint support for that media type. |
| Repeated messages appear after restart or retry | Deduplication is in memory and bounded; it is not durable or exactly-once. WAHA retries may resend an event after an uncertain send result. |

To find group IDs, see step 9 of the [deployment walkthrough](#vm-deployment-with-waha).

To upgrade later, change `COURIER_TAG` in `.env` to `latest`, an exact SemVer tag such as `0.9.0`, or a commit SHA, then run `docker compose pull && docker compose up -d` to update every service in the deployment. Exact version and commit-SHA tags make rollbacks reproducible. Before upgrading across a release that changes the config format, read its migration notes, for example [Migrating from 0.8.x](#migrating-from-08x). If you do have repository access on the VM, cloning it is also an option; run Compose from the directory containing `docker-compose.yml`.

### Releases and image tags

`pyproject.toml` is the version source of truth. For each release, update its `project.version`, move the release notes from the `Unreleased` section in `CHANGELOG.md` into a dated `X.Y.Z` section, then push a tag matching that version, without a `v` prefix (for example, `0.1.1`). The Release workflow runs only for numeric SemVer tags and verifies that the tag matches `pyproject.toml` before publishing. Pushes to branches run CI but do not publish Docker images.

A SemVer tag publishes `X.Y.Z`, `X.Y`, `X`, `latest`, and the full commit SHA image tags. Use an exact `X.Y.Z` tag for a pinned deployment; the shorter tags and `latest` move when a newer matching release is published.

Runtime credentials stay on the VM in the ignored `.env` file, not in Git. WAHA's web UI is bound to the VM's loopback interface; use the HTTPS Nginx proxy for browser access and never expose port `3000` publicly.

Courier listens for inbound webhook events on `/webhook` at its `listen_port`. Each container registers its own `webhook_url` in WAHA over the private Compose network.

## Migrating from 0.8.x

0.9.0 moves every Courier setting into the container's YAML file. Started with a 0.8.x setup, Courier 0.9.0 refuses to start and lists each setting to move, but you can migrate in one pass:

1. Back up `.env`, the YAML files, and `docker-compose.yml`.
2. Rewrite each YAML file with `role`, `webhook_url`, and a `waha` section, copying values over from `.env` and `docker-compose.yml` with the table below. Copy `WAHA_SESSION` into every file's `waha.session` if it isn't `default`: a container left on `default` would create and start a new, unlinked WAHA session. The three Compose files are shown in full in step 5 of the [deployment walkthrough](#vm-deployment-with-waha).
3. Only then, in `.env`, delete the Courier lines: `TRANSPORT`, `WAHA_URL`, `WAHA_SESSION`, `LOG_LEVEL`, `LISTEN_PORT`, `INCLUDE_FROM_ME`, and any `SOURCE_CHAT_ID`, `TARGET_CHAT_ID`, `ALERT_CHAT_ID`, `HEALTH_*`, or `MATRIX_*` lines. Keep the WAHA lines and `COURIER_TAG`, and set `COURIER_TAG` to the 0.9 release you are deploying.
4. Replace `docker-compose.yml` with the one in step 6.
5. Run `docker compose pull && docker compose up -d --force-recreate` and check each Courier container's log.

| 0.8.x setting (environment variable or YAML key) | 0.9.0 YAML key |
| --- | --- |
| A forwarding container | `role: forwarder` |
| `HEALTH_MONITOR_ONLY=true`, `health_monitor_only: true` | `role: health-monitor` |
| `HEALTH_ALERTS_ENABLED`, `health_alerts_enabled` | `role: health-monitor` for the health container; `health_alerts: true` on a forwarder that should also alert (now off by default) |
| `WHATSAPP_HOOK_URL` | `webhook_url` (now required) |
| `WAHA_URL`, `waha_url` | `waha.url` |
| `WAHA_SESSION`, `waha_session` | `waha.session` |
| `waha_api_key`, `waha.api_key` | removed: the key comes only from `WAHA_API_KEY` in `.env` |
| `TRANSPORT`, `transport` | `destination_transport` (and `source_transport` if needed) |
| `SOURCE_TRANSPORT`, `DESTINATION_TRANSPORT` | `source_transport`, `destination_transport` |
| `SOURCE_CHAT_ID`, `TARGET_CHAT_ID` | `source_chat_id`, `target_chat_id` |
| `ALERT_CHAT_ID` | `alert_chat_id` (no longer falls back to `target_chat_id`) |
| `LISTEN_PORT`, `LOG_LEVEL`, `INCLUDE_FROM_ME` | `listen_port`, `log_level`, `include_from_me` |
| `MATRIX_HOMESERVER_URL`, `matrix_homeserver_url` | `matrix.homeserver_url` |
| `MATRIX_ACCESS_TOKEN`, `matrix_access_token` | `matrix.access_token` |

If a route was renamed in the past (for example `courier-lupynis` became `courier-tfl` in 0.4.0), its old webhook entry may still be registered in the WAHA session, and WAHA keeps trying to deliver to it. Courier removes only the old `http://courier:8000/webhook` entry. List the registered URLs with:

```bash
curl -s -H "X-Api-Key: $WAHA_API_KEY" http://127.0.0.1:3000/api/sessions/default | jq '.config.webhooks[].url'
```

and remove stale entries from the session's config (for example with `PUT /api/sessions/{session}` in WAHA's Swagger UI).

## Development

Install the project and development dependencies:

```bash
uv sync --dev
```

Run the test suite, type checker, linter, formatter check, and Compose validation:

```bash
uv run pytest -q
uv run mypy courier tests
uv run ruff check .
uv run ruff format --check .
docker compose config --quiet
```

Compose validation needs WAHA's variables; locally, copy `.env.example` to `.env` first.

After changing the config models in `courier/config/model.py`, regenerate the editor schema and commit it; a test fails while the committed schema is out of date:

```bash
uv run python -m courier.config.schema
```

Run the service locally with `WAHA_API_KEY=... uv run python -m courier.main`. The configuration file defaults to `config.yaml`; see [Configuration reference](#configuration-reference).

## Project structure

```text
courier/
  adapters/
    matrix.py          # Matrix destination
    waha.py            # WAHA session client: sending, health probe, session setup
    waha_ingress.py    # WAHA webhook events -> forwarder and health monitor
    waha_media.py      # lazy WAHA media references and origin-checked downloads
    waha_payload.py    # WAHA message payload parsing
  config/
    model.py           # the config file's keys, per role (Pydantic models)
    loader.py          # reads the file and reports every problem at once
    migration.py       # settings retired in 0.9.0 and where they moved
    schema.py          # generates courier-config.schema.json for editors
  core/
    domain.py          # Message, Attachment, MediaRef
    filters.py
    forwarder.py       # filtering, media selection, deduplication, sending
    health.py          # alert-once health state and polling
    media.py           # media-kind classification
    ports.py
  infrastructure/      # webhook HTTP server and logging context
  main.py
examples/              # annotated configs per role, and the Compose deployment's three files
courier-config.schema.json  # editor schema generated from config/model.py
tests/
```

`main.py` wires one route: the destination, the `Forwarder`, the optional `SessionHealthMonitor`, and the `WAHAWebhookIngress` that the webhook server calls. `core/` holds the forwarding and health decisions and has no WAHA or HTTP knowledge. The WAHA adapter translates between WAHA's API and those core types.
