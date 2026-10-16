# Courier

Courier is an asynchronous WhatsApp message forwarder. Each Courier process watches one source chat, applies optional sender and keyword filters, and relays eligible messages to one target chat through WAHA.

Courier communicates with WAHA over its HTTP API and receives messages through a webhook. One process/container manages one WAHA session and one source-to-target route.

## Contents

- [How it works](#how-it-works)
- [Message behavior and limitations](#message-behavior-and-limitations)
- [Configuration reference](#configuration-reference)
- [Run locally](#run-locally)
- [Run with Docker](#docker)
- [Deploy on an Oracle VM](#vm-deployment-with-waha)
- [Operate and troubleshoot](#operate-and-troubleshoot)
- [Development](#development)
- [Project structure](#project-structure)

## Features

- Forward message text and supported media from one WhatsApp chat to another
- Filter by sender ID/name and/or included keywords, with optional excluded keywords
- Deduplicate up to 1,000 successfully forwarded message IDs in memory
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

1. Courier loads and validates its settings from YAML and environment variables.
2. It connects to the configured WAHA URL using the configured API key.
3. It finds or creates the configured WAHA session, registers its webhook for `message` and `message.any`, and starts the session if needed.
4. WAHA sends message events to Courier's `POST /webhook` endpoint.
5. Courier ignores events outside the configured source chat, applies filters, and sends the message to the target chat through WAHA.

Courier also exposes `GET /health` on the same listen port. The webhook server binds to all container interfaces; in a Compose deployment, WAHA should reach it through the private service network. Do not expose the webhook port to the public internet unless it is protected by an appropriate network or authentication layer.

There are two separate network paths to account for:

- Courier must be able to call WAHA at `WAHA_URL` for session setup, media downloads, and outgoing messages.
- WAHA must be able to call Courier at `WHATSAPP_HOOK_URL` to deliver incoming events.

If WAHA runs on another VM, both paths must be routable between the machines. Prefer a private network/VPN or TLS-protected endpoints with firewall rules allowing only the required traffic. Do not publish WAHA's unauthenticated or unencrypted API port directly to the internet.

## Message behavior and limitations

- Text and captions are forwarded without a sender-name prefix. The forwarded WhatsApp message is sent by the Courier-linked account; WhatsApp does not preserve the original sender identity or all source-side formatting as native metadata.
- Courier forwards at most one attachment per message. Images use WAHA's `sendImage` endpoint, videos use `sendVideo`, and other files use `sendFile`.
- If WAHA reports that inline WEBJS video sending requires the `devlikeapro/waha:chrome` image, Courier retries the video as a file. This is useful with WAHA's ARM image, where inline WEBJS video is not supported.
- Media download failures, unsafe media URLs, and empty media do not discard the text/caption. An attachment larger than `max_attachment_size` is skipped; Courier sends the text/caption instead. The size limit defaults to 20 MiB. Media is downloaded before this forwarding-size check, so the setting does not limit download bandwidth or peak download memory.
- Media URLs are fetched only when they resolve to the configured WAHA origin. Loopback media URLs are rewritten to the configured WAHA host. This prevents Courier's WAHA API-key header from being sent to an unrelated host; URLs on another hostname/origin are skipped.
- Included keywords search the message text/caption using case-insensitive substring matching. A keyword filter accepts a message if any configured keyword matches.
- Excluded keywords are also case-insensitive substring matches against the text/caption. If any excluded keyword appears, the message is rejected even when it matches an included keyword or sender. Exclusions are vetoes; included keywords and sender criteria retain their OR behavior.
- Sender filters match the normalized sender ID or sender name. Normalization is case-insensitive and strips a leading `+` and any `@...` suffix.
- If both keyword and sender filters are configured, they are combined with **OR**: matching either filter is sufficient. With no filters configured, every message from the source chat is eligible. A media-only message has no matching keyword text, so it passes a keyword filter only if there is a matching caption.
- Courier ignores messages sent by its linked WhatsApp account by default. Enable `include_from_me` only when needed, and keep source and target chat IDs different to avoid forwarding loops.
- The successful-message deduplication cache holds at most 1,000 message IDs in memory. It is not persisted and is cleared on restart. A failed send is not cached, so WAHA can retry it. Retries reduce message loss but do not guarantee exactly-once delivery: a send may have succeeded at WAHA even if Courier did not receive its response, and a later retry can create a duplicate.
- Courier configures WAHA webhook retries as exponential with `delaySeconds: 2` and `attempts: 10` when adding its webhook or when the existing Courier webhook has no retry configuration. Explicit retry configuration on an existing webhook is preserved. WAHA's retry behavior is not a durable queue.
- Incoming webhook requests have a 1 MiB body limit, a 64 KiB header limit, and a 10-second request timeout. Chunked transfer encoding is not supported.
- During startup, Courier retries WAHA session setup after server errors or connection failures, for up to 12 attempts with a 5-second delay between attempts. Configuration/client errors are surfaced instead of retried.

## Configuration reference

Courier reads a YAML mapping from `config.yaml` by default. Set `CONFIG_PATH` to use another file. Environment variables take precedence over the corresponding YAML setting; `INCLUDE_FROM_ME` is treated as unset when it is blank.

Example `config.yaml`:

```yaml
transport: waha
source_chat_id: "120363123456789012@g.us"
target_chat_id: "120363987654321098@g.us"
listen_port: 8000
log_level: INFO
waha_url: "http://localhost:3000"
waha_api_key: "your-waha-api-key"
waha_session: "default"
webhook_url: "http://localhost:8000/webhook"
include_from_me: false
max_attachment_size: 20971520

filters:
  keywords:
    - "vip"
    - "alert"
  exclude_keywords:
    - "city"
    - "New York"
  senders:
    - "+1234567890"
    - "alice@example.com"
```

| Setting | Environment variable | YAML key | Default / requirement |
| --- | --- | --- | --- |
| Transport | `TRANSPORT` | `transport` | `waha`; currently the only supported transport |
| Source chat ID | `SOURCE_CHAT_ID` | `source_chat_id` | Required |
| Target chat ID | `TARGET_CHAT_ID` | `target_chat_id` | Required |
| Webhook listen port | `LISTEN_PORT` | `listen_port` | `8000`; must be a positive integer |
| Log level | `LOG_LEVEL` | `log_level` | `INFO`; must be a recognized Python logging level |
| WAHA base URL | `WAHA_URL` | `waha_url` or `waha.url` | `http://localhost:3000` |
| WAHA API key | `WAHA_API_KEY` | `waha_api_key` or `waha.api_key` | Empty (only use this if WAHA does not require a key) |
| WAHA session name | `WAHA_SESSION` | `waha_session` or `waha.session` | `default` |
| Courier webhook URL registered in WAHA | `WHATSAPP_HOOK_URL` | `webhook_url` | `http://localhost:8000/webhook` |
| Include messages sent by the linked account | `INCLUDE_FROM_ME` | `include_from_me` | `false`; accepts true/false, yes/no, on/off, or 1/0 |
| Maximum outgoing attachment size in bytes | — | `max_attachment_size` | `20971520` (20 MiB); must be positive |
| Required/include keyword values | — | `filters.keywords` | Empty; accepts a YAML list or comma-separated string; any one match satisfies the keyword criterion |
| Excluded keyword values | — | `filters.exclude_keywords` | Empty; accepts a YAML list or comma-separated string; any one match rejects the message |
| Sender filter values | — | `filters.senders` | Empty; accepts a YAML list or comma-separated string |
| Config file path | `CONFIG_PATH` | — | `config.yaml` |

`WHATSAPP_HOOK_URL` is the URL WAHA calls to deliver events; it must be reachable from the WAHA container/network. This is distinct from `WAHA_URL`, which Courier uses to call WAHA. For separate Courier containers, use a unique service name or otherwise unique webhook URL for each instance. `LISTEN_PORT` is the port Courier binds inside its container; separate containers can use the same internal port.

`max_attachment_size` and filter values are configured in YAML; they have no environment-variable overrides. Unknown transport names and invalid typed settings fail during startup. If `INCLUDE_FROM_ME` is true, source and target chat IDs must differ. Filter order/combination is: the message must pass at least one configured include criterion (keyword or sender), if any are configured, and must not match any excluded keyword. When only exclusions are configured, all messages without excluded terms are eligible.

## Run locally

Create `config.yaml` using the [configuration reference](#configuration-reference), make sure WAHA is reachable at the configured URL, then run:

```bash
cd /workspaces/courier
uv run python -m courier.main
```

For a quick local run, environment variables can supply the required chat IDs and override YAML values:

```bash
LOG_LEVEL=DEBUG SOURCE_CHAT_ID="..." TARGET_CHAT_ID="..." \
WAHA_URL="http://localhost:3000" WAHA_API_KEY="..." \
uv run python -m courier.main
```

The webhook URL in `WHATSAPP_HOOK_URL` must be reachable from WAHA. When WAHA runs in Docker and Courier runs on the host, use a host address reachable from the WAHA container rather than `localhost` from WAHA's point of view.

## Docker

The following is a minimal example for a single Docker host. Make sure the configured webhook URL is reachable from WAHA. For a production setup with persistent WAHA data and credentials, use the [VM deployment walkthrough](#vm-deployment-with-waha).

Build the image:

```bash
cd /workspaces/courier
docker build -t courier .
```

Run it with required environment variables:

```bash
docker run --rm -it \
  -p 8000:8000 \
  -e TRANSPORT=waha \
  -e SOURCE_CHAT_ID="..." \
  -e TARGET_CHAT_ID="..." \
  -e WAHA_URL="http://host.docker.internal:3000" \
  -e WAHA_API_KEY="..." \
  -e WHATSAPP_HOOK_URL="http://host.docker.internal:8000/webhook" \
  courier
```

On Linux, `host.docker.internal` may need to be added with Docker's `--add-host=host.docker.internal:host-gateway` option. Alternatively, put WAHA and Courier on the same user-defined Docker network and use the Courier service/container name as the webhook hostname.

## VM deployment with WAHA

GitHub Actions runs CI on pushes and pull requests. After CI passes on `main`, it builds and publishes multi-platform images to GitHub Container Registry:

- `ghcr.io/akalex/courier:latest`
- `ghcr.io/akalex/courier:<commit-sha>`

Image publishing does not deploy automatically. You do not need to clone this private repository on the VM: deploy the published image using a Compose file created there.

The Oracle `VM.Standard.A1.Flex` shape uses ARM64. Courier's published image includes an ARM64 build and Docker selects it automatically. WAHA's `latest` image is x86-only, so use WAHA's ARM image tag, `devlikeapro/waha:arm`, on A1 instances.

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

   This generates a new WAHA API key and dashboard/Swagger logins. Use strong, unique values for the API key and passwords. Keep the generated `.env` private; Courier and WAHA use the same API key. Continue with the next step to add Courier's settings to this file.
4. Add Courier's settings to `.env`. You can temporarily use the values below for the chat IDs; replace them with the real IDs after linking WhatsApp and looking up your chats:

   ```dotenv
   WAHA_SESSION=default
   SOURCE_CHAT_ID=replace-after-linking
   TARGET_CHAT_ID=replace-after-linking
   LOG_LEVEL=INFO
   LISTEN_PORT=8000
   COURIER_TAG=latest
   ```

5. Create `config.yaml` in the same directory (the Compose file mounts it into Courier). This example forwards messages or media whose text/caption contains `urgent` or `alert`:

   ```yaml
   filters:
     keywords:
       - urgent
       - alert
   ```

   Keywords are case-insensitive substrings; any one matching keyword is sufficient. To disable keyword filtering, use an empty config file (`{}`) instead.
6. Create `docker-compose.yml` in the same directory:

   ```bash
   cat > docker-compose.yml <<'EOF'
   services:
     waha:
       image: devlikeapro/waha:arm
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

     courier:
       image: ghcr.io/akalex/courier:${COURIER_TAG:-latest}
       restart: unless-stopped
       depends_on:
         - waha
       volumes:
         - ./config.yaml:/app/config.yaml:ro
       environment:
         TRANSPORT: waha
         CONFIG_PATH: /app/config.yaml
         WAHA_URL: http://waha:3000
         WAHA_API_KEY: ${WAHA_API_KEY:?Set WAHA_API_KEY in .env}
         WAHA_SESSION: ${WAHA_SESSION:-default}
         WHATSAPP_HOOK_URL: http://courier:8000/webhook
         SOURCE_CHAT_ID: ${SOURCE_CHAT_ID}
         TARGET_CHAT_ID: ${TARGET_CHAT_ID}
         LOG_LEVEL: ${LOG_LEVEL:-INFO}
         LISTEN_PORT: ${LISTEN_PORT:-8000}
   EOF
   ```

7. Start both services:

   ```bash
   docker compose pull
   docker compose up -d
   ```

   Courier creates the configured WAHA session and registers its `message` webhook automatically.

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

   To find the chat IDs, open WAHA's Swagger UI at `https://waha.example.com/swagger` (or the IP URL), authorize with the generated API key, then call `GET /api/default/chats` (replace `default` with the value of `WAHA_SESSION` if different). Find the source and destination groups by name and copy each `id` exactly as returned; group IDs typically end in `@g.us`. Update `SOURCE_CHAT_ID` and `TARGET_CHAT_ID` in `/opt/courier/.env` with those IDs, then apply them by restarting Courier:

   ```bash
   cd /opt/courier
   docker compose up -d courier
   ```

## Operate and troubleshoot

Run these commands from the deployment directory containing `docker-compose.yml`:

```bash
docker compose ps
docker compose logs --tail=100 courier
docker compose logs --tail=100 waha
docker compose restart courier
```

When changing `SOURCE_CHAT_ID`, `TARGET_CHAT_ID`, `WAHA_SESSION`, or other `.env` values, recreate the Courier container so it receives the updated environment:

```bash
docker compose up -d --force-recreate courier
```

Each Courier process/container manages exactly one WAHA session and one source-to-target route. To use another session or route at the same time, run another Courier container with its own environment values and a distinct reachable webhook URL/service name. Give each instance its own routing settings. Each container can listen on port `8000` internally; host-published ports must be unique if publishing them directly.

| Symptom | Check |
| --- | --- |
| Courier exits during startup with missing/invalid config | Check `SOURCE_CHAT_ID`, `TARGET_CHAT_ID`, `CONFIG_PATH`, the YAML shape, positive `LISTEN_PORT`/`max_attachment_size`, and `LOG_LEVEL`. |
| Courier cannot connect to WAHA | Check `WAHA_URL`, container DNS/networking, WAHA readiness, and that `WAHA_API_KEY` matches the WAHA instance. Review both Courier and WAHA logs. |
| WAHA session stays stopped or requires linking | Open WAHA Dashboard, select the configured `WAHA_SESSION`, and scan its QR code. A different session name refers to a separate WAHA session; changing the name does not rename or delete the old session. |
| Messages do not arrive at Courier | Verify WAHA can reach `WHATSAPP_HOOK_URL`, that it points to `POST /webhook`, and that the Courier port is available on the shared network. |
| Messages arrive but are not forwarded | Verify the exact `SOURCE_CHAT_ID`, keyword/sender filter settings, and `INCLUDE_FROM_ME`. Courier logs filter decisions at INFO and raw sender details at DEBUG. |
| WAHA reports webhook delivery failures | Inspect Courier logs for forwarding exceptions and WAHA logs for retry status. Ensure the target chat exists and Courier's WAHA API credentials are valid. |
| Media is absent but text arrives | Check WAHA's media URL and network accessibility, whether the resolved URL has the same origin as `WAHA_URL`, the file size limit, and WAHA endpoint support for that media type. |
| Repeated messages appear after restart or retry | Deduplication is in memory and bounded; it is not durable or exactly-once. WAHA retries may resend an event after an uncertain send result. |

For group IDs, use WAHA Swagger's `GET /api/{session}/chats`, find each group by name, and copy its `id` exactly. Group IDs usually end in `@g.us`. Update `SOURCE_CHAT_ID` and `TARGET_CHAT_ID` and recreate the Courier container.

To upgrade later, change `COURIER_TAG` in `.env` to `latest` or a commit SHA, then run `docker compose pull courier && docker compose up -d courier`. Using a commit-SHA tag makes rollbacks reproducible. If you do have repository access on the VM, cloning it is also an option; run Compose from the directory containing `docker-compose.yml`.

Runtime credentials stay on the VM in the ignored `.env` file, not in Git. WAHA's web UI is bound to the VM's loopback interface; use the HTTPS Nginx proxy for browser access and never expose port `3000` publicly.

The WAHA adapter listens for inbound webhook events on `/webhook` using `LISTEN_PORT`. Courier provisions the session webhook to the `courier` service over the private Compose network.

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

The Compose validation command may print warnings if optional deployment variables such as `SOURCE_CHAT_ID` or `TARGET_CHAT_ID` are unset in the local environment. Those variables are required for a configured deployment.

Run the service locally with `uv run python -m courier.main`. The configuration file defaults to `config.yaml`; see [Configuration reference](#configuration-reference).

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
  webhook_server.py
tests/
  test_config.py
  test_filters.py
  test_forwarder.py
  test_waha_adapter.py
  test_webhook_server.py
```

`main.py` composes configuration, filters, forwarding, and the transport. `forwarder.py` contains source filtering, attachment-size checks, and bounded in-memory deduplication. `adapters/waha.py` handles WAHA API/session behavior and message normalization. `webhook_server.py` provides the inbound HTTP endpoint.
