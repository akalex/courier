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

Each configured keyword is matched as a case-insensitive substring of the message text or caption. If keywords are configured, a message must contain at least one of them to pass the keyword filter; media without a matching caption is not forwarded. Sender filters can also be configured.

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

8. To access the WAHA dashboard and Swagger without an SSH tunnel, put Nginx on the VM in front of WAHA. First, point a DNS name (for example, `waha.example.com`) to the VM's public IP. In the Oracle Cloud network security list or NSG, allow inbound TCP ports 80 and 443; also allow them in the VM's firewall if enabled. Do not open port 3000 publicly.

   Install Nginx and Certbot with the package instructions for the VM's operating system. Create an Nginx HTTP virtual host (for example, `/etc/nginx/conf.d/waha.conf`) and replace `waha.example.com` with your DNS name:

   ```nginx
   server {
       listen 80;
       listen [::]:80;
       server_name waha.example.com;

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

   Validate and reload Nginx, then use Certbot's Nginx plugin to issue a certificate and enable HTTPS:

   ```bash
   sudo nginx -t
   sudo systemctl reload nginx
   sudo certbot --nginx -d waha.example.com
   ```

   Follow Certbot's prompts and enable the HTTP-to-HTTPS redirect if offered. Keep port 3000 bound to `127.0.0.1` as in the Compose file; expose only ports 80/443. Then open `https://waha.example.com/dashboard`, sign in with the generated dashboard credentials, connect using the generated WAHA API key, and scan the QR code. Swagger is at `https://waha.example.com/swagger`; use the generated Swagger username and password as well as the API key when prompted. This makes the WAHA administration interface publicly reachable, so use strong unique credentials and share the URL only with authorized users.

   To find the chat IDs, open WAHA's Swagger UI at `https://waha.example.com/swagger`, authorize with the generated API key, then call `GET /api/default/chats` (replace `default` with the value of `WAHA_SESSION` if different). Find the source and destination groups by name and copy each `id` exactly as returned; group IDs typically end in `@g.us`. Update `SOURCE_CHAT_ID` and `TARGET_CHAT_ID` in `/opt/courier/.env` with those IDs, then apply them by restarting Courier:

   ```bash
   cd /opt/courier
   docker compose up -d courier
   ```

To upgrade later, change `COURIER_TAG` in `.env` to `latest` or a commit SHA, then run `docker compose pull courier && docker compose up -d courier`. Using a commit-SHA tag makes rollbacks reproducible. If you do have repository access on the VM, cloning it is also an option; run Compose from the directory containing `docker-compose.yml`.

Runtime credentials stay on the VM in the ignored `.env` file, not in Git. WAHA's web UI is bound to the VM's loopback interface; use the HTTPS Nginx proxy for browser access and never expose port `3000` publicly.

The WAHA adapter listens for inbound webhook events on `/webhook` using `LISTEN_PORT`. Courier provisions the session webhook to the `courier` service over the private Compose network.

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
