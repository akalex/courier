# Courier configuration layout — design

Status: approved by the maintainer; validation library (Pydantic v2) and editor schema approved 2026-11-23 · Target release: 0.9.0 · Date: 2026-11-23

## Why

Configuring a Courier container is error-prone. Most settings can be set both in the container's YAML file and as an environment variable; the environment silently wins, and the Compose file sets some variables in two places (the shared `x-courier-common` block and each service block). While switching the `courier-health` container to health-monitor-only mode, the maintainer repeatedly edited YAML values that never took effect.

Concrete problems in 0.8.1:

- `courier-health`'s health flags are set in `config-health.yaml`, in `x-courier-common.environment`, and in its service `environment`. Under Compose the YAML values are dead, although the README says to put them there.
- `.env.example` contains `TRANSPORT` and `WAHA_URL`, which `docker-compose.yml` never references, so they do nothing.
- `LISTEN_PORT` comes from `.env`, but every `WHATSAPP_HOOK_URL` in Compose hard-codes `:8000`; changing the port breaks webhook delivery.
- `INCLUDE_FROM_ME` comes from `.env` and applies to every route, although it is a per-route choice validated per route.
- The README still tells operators to put chat IDs in `.env` (deployment step 4, chat-ID lookup step) and says Compose passes `ALERT_CHAT_ID` to every container; both stopped being true in 0.8.0.
- Settings have an environment name and a YAML key, and some have two YAML spellings (`waha_url` / `waha.url`, `matrix_homeserver_url` / `matrix.homeserver_url`). Unknown YAML keys, including typos, are silently ignored.
- `health_alerts_enabled` + `health_monitor_only` allow an invalid combination, and `alert_chat_id` silently falls back to `target_chat_id`.

## Goal

One clear, obvious, well-documented way to configure each service:

- **`.env` belongs to WAHA.** It holds WAHA's settings plus `COURIER_TAG`.
- **Each Courier container is fully described by its own YAML file**, except the WAHA API key, which Courier receives from `.env` through Compose so it always matches WAHA's.
- Every setting has exactly one place and one spelling. A setting in the wrong place, an unknown key, or a key that does not apply to the container's role stops Courier at startup with a message naming the file, the key, and where it belongs.
- **Each container reads only the settings it needs to operate** (maintainer's requirement). `courier-health` has no `source_chat_id` or `target_chat_id`; a forwarding route without health alerts, such as `courier-tfl`, has no `alert_chat_id`. Such keys are rejected, not ignored, and the loaded configuration does not carry them.
- The documentation cannot drift from the code: example YAML files are tracked in the repo and loaded by tests, and an editor schema generated from the config model gives autocomplete, inline docs, and error highlighting while editing a config file.

## Non-goals

- Running several routes in one Courier process.
- Changing WAHA's own configuration.
- Hot-reloading configuration.
- An environment-variable-only mode.
- Changing forwarding, filtering, or health behaviour beyond what the new keys express.

## Anti-goals (failure even if it technically works)

- Any Courier setting readable from two places, or any silent override.
- A misspelled or misplaced key that Courier ignores instead of rejecting.
- Courier secrets in `docker-compose.yml`.
- Example configs, README snippets, or the editor schema disagreeing with what Courier accepts.

## Approaches considered

### Layout

- **A. Each container's YAML holds everything except secrets** (chosen by the maintainer). Refined in conversation: `.env` is WAHA's file; Courier takes only `WAHA_API_KEY` from it. Trade-off accepted: settings every container must agree on (WAHA URL, session) are repeated in each YAML, so switching the WAHA session means editing every Courier YAML file.
- **B. Split by scope** (shared settings in `.env`, per-container in YAML) **and fail loudly on overlap.** Not chosen as a layout, but its fail-loudly rules (unknown keys, misplaced settings, a single `role`) are adopted into A because they are what prevents the silent-override mistakes.
- **C. Environment only, no YAML.** Rejected: filter lists become comma strings and every route change means editing `docker-compose.yml`.
- **D. Keep both sources, fix the docs.** Rejected: leaves the silent overrides that caused the problem.

### Validation library

Chosen by the maintainer on the designer's recommendation: **plain Pydantic v2** (`pydantic>=2.14,<3`; not `pydantic-settings`). It gives, declaratively, every rule this design needs: unknown keys rejected at every level (`extra="forbid"`), different keys per `role` (a discriminated union), conditional requirements (model validators), all problems reported in one error with key paths, typed config objects, and a JSON Schema for editors. Wheels for `pydantic-core` 2.50.5 exist for CPython 3.14 and 3.15 on Linux ARM64 (the deployment VM) and macOS ARM64.

Rejected:

- **Hand-written validation with PyYAML** — workable with no new dependency, but every rule is code to write and test (estimated 350–450 lines), a missed rule silently recreates the problem, and there is no editor schema.
- **pydantic-settings** — its core feature is environment variables and `.env` overriding the file, which is exactly the behaviour being removed.
- **Dynaconf** — built around layered, merged settings with environment overrides; untyped; latest release requires Python < 3.15.
- **Hydra / OmegaConf** — config composition and CLI overrides for ML experiments; no per-role key sets; stops at the first error.
- **python-decouple** — flat environment / `.ini` reading with casting; no nested YAML or schema; last release 2023.
- **dependency-injector** — an object-wiring container, not a validator; `build_route` already does the wiring in about 35 lines.
- **msgspec** — small and strict, but stops at the first error, recreating the one-fix-per-restart loop.

## Design

### Where each setting lives

| Place | Contents |
| --- | --- |
| `.env` | WAHA only: `WAHA_API_KEY`, `WAHA_DASHBOARD_USERNAME`, `WAHA_DASHBOARD_PASSWORD`, `WHATSAPP_SWAGGER_USERNAME`, `WHATSAPP_SWAGGER_PASSWORD`, `WAHA_IMAGE`, `WHATSAPP_DEFAULT_ENGINE`; plus `COURIER_TAG` (the deployment's version pin, kept so upgrading stays a one-line edit — maintainer agreed). |
| Courier environment | Exactly two variables: `WAHA_API_KEY` (passed by Compose from `.env`; empty means WAHA has no key) and `CONFIG_PATH` (bootstrap: which YAML file to read; default `config.yaml`, which resolves to `/app/config.yaml` in the image). |
| Container YAML | Every other Courier setting, including the WAHA URL and session, the webhook URL, listen port, log level, and the Matrix access token. |

The Matrix access token lives in the route's YAML (maintainer agreed). The README tells operators to `chmod 600` YAML files that contain it.

### YAML schema

Every key has one spelling. `role` is required and decides which keys are allowed.

Keys for both roles:

| Key | Required | Default | Notes |
| --- | --- | --- | --- |
| `role` | yes | — | `forwarder` or `health-monitor`. Replaces `health_alerts_enabled` and `health_monitor_only`. |
| `webhook_url` | yes | — | URL WAHA calls to reach this container. *Designer's call:* required, because a wrong default silently registers an unreachable URL in WAHA. |
| `listen_port` | no | `8000` | Port Courier binds inside the container. Not cross-checked against `webhook_url` (a proxy may sit between them); documented instead. |
| `log_level` | no | `INFO` | Any Python logging level name. |
| `waha.url` | no | `http://localhost:3000` | Courier → WAHA. Compose configs set `http://waha:3000`. |
| `waha.session` | no | `default` | WAHA session name. |
| `destination_transport` | no | `waha` | `waha` or `matrix`. Health monitors use it to send alerts. |
| `matrix.homeserver_url` | when `destination_transport: matrix` | — | Rejected otherwise. |
| `matrix.access_token` | when `destination_transport: matrix` | — | Secret. Rejected otherwise. |
| `alert_chat_id` | see below | — | Recipient of health alerts. Never falls back to `target_chat_id`. |

Forwarder-only keys (rejected on a health monitor):

| Key | Required | Default | Notes |
| --- | --- | --- | --- |
| `source_transport` | no | `waha` | Only `waha` is supported. Kept so the routing examples read naturally. |
| `source_chat_id` | yes | — | |
| `target_chat_id` | yes | — | |
| `include_from_me` | no | `false` | Moves out of `.env`. Existing loop check (WAHA→WAHA with equal source and target) stays. |
| `forward_content` | no | `[text, media]` | Unchanged semantics. |
| `media_types` | no | `[image, video, audio, file]` | Unchanged semantics. |
| `max_attachment_size` | no | `20971520` | Unchanged semantics. |
| `filters.keywords`, `filters.exclude_keywords`, `filters.senders` | no | empty | Unchanged semantics, including list-or-comma-string. `filters` accepts only these three keys. |
| `health_alerts` | no | `false` | Lets a standalone forwarder also monitor WAHA health. Default changes from today's `true`: alerting is the health monitor's job, and a new route copied from another forwarder should not start alerting. |

`alert_chat_id` rules:

- `role: health-monitor` → required.
- `role: forwarder` with `health_alerts: true` → required.
- `role: forwarder` without `health_alerts: true` → rejected (it would be ignored).

### Config model

The YAML schema above is expressed as Pydantic v2 models in `courier/config/`:

- A shared base model for the keys both roles have, with nested models for `waha` and `matrix`.
- `ForwarderConfig` (`role: Literal["forwarder"]`) adds the forwarder-only keys, with a nested model for `filters`.
- `HealthMonitorConfig` (`role: Literal["health-monitor"]`) adds a required `alert_chat_id` and nothing else.
- The config type is the discriminated union `ForwarderConfig | HealthMonitorConfig` on `role`. A health monitor's config object therefore has no chat or filter attributes at all, and a forwarder's `alert_chat_id` is `None` whenever alerts are off — code cannot read a setting the role does not have.
- Every model forbids extra keys. Conditional rules (`alert_chat_id` vs `health_alerts`, `matrix` vs `destination_transport`, the include-from-me loop check) are model validators.
- Every field carries a `description`; these become the hover text in the editor schema.
- The WAHA API key is not a YAML field. `load_config` reads `WAHA_API_KEY` from the environment and returns it alongside the validated model (how it is attached is the builder's choice, but it must not be settable from YAML).

### Startup validation

`load_config(path=None)` reads `CONFIG_PATH` (default `config.yaml`), parses YAML with PyYAML, and reports every problem it finds in one `ValueError` rather than stopping at the first (designer's call, approved with the doc: a migration usually touches several keys, and one-error-per-restart is the loop that caused the original pain).

The file is named by the path Courier read, which under Compose is always `/app/config.yaml` (designer's call: Courier cannot know the host file name, and `docker compose logs` prefixes every line with the service name, so `courier-health | /app/config.yaml: unknown key …` identifies the host file). The README troubleshooting section says so.

Validation runs in two passes whose messages are combined into the one error:

1. **Migration pass (before Pydantic).** Reports retired environment variables and retired YAML keys with hints, then removes the retired keys so Pydantic does not report them a second time as unknown.
   - Retired environment variable is set (non-empty): `TRANSPORT`, `SOURCE_TRANSPORT`, `DESTINATION_TRANSPORT`, `SOURCE_CHAT_ID`, `TARGET_CHAT_ID`, `ALERT_CHAT_ID`, `HEALTH_ALERTS_ENABLED`, `HEALTH_MONITOR_ONLY`, `LISTEN_PORT`, `LOG_LEVEL`, `WAHA_URL`, `WAHA_SESSION`, `WHATSAPP_HOOK_URL`, `MATRIX_HOMESERVER_URL`, `MATRIX_ACCESS_TOKEN`, `INCLUDE_FROM_ME`. Message: the variable is no longer read; set `<yaml key>` in `<config file>`.
   - Retired YAML key:

     | Retired key | Hint |
     | --- | --- |
     | `transport` | use `destination_transport` (and `source_transport` if needed) |
     | `health_alerts_enabled`, `health_monitor_only` | use `role` (and `health_alerts` on a forwarder) |
     | `waha_url`, `waha_session` | use `waha.url`, `waha.session` |
     | `waha_api_key`, `waha.api_key` | the WAHA API key comes from `WAHA_API_KEY` in `.env` |
     | `matrix_homeserver_url`, `matrix_access_token` | use `matrix.homeserver_url`, `matrix.access_token` |

2. **Schema pass (Pydantic).** Each Pydantic error is rewritten into a plain line naming the file and the YAML key path as the operator writes it (`filters.keywords`, never Pydantic internals such as the union tag prefix). In particular:
   - an extra key that belongs to the other role reads as "`source_chat_id` does not apply to role `health-monitor`", not as an unknown key;
   - other extra keys read as "unknown key `health_monitor_ony`";
   - a missing or invalid `role` reads as "`role` is required: `forwarder` or `health-monitor`";
   - existing checks keep their meaning: required keys, allowed values, positive integers, log level names, Matrix credentials for a Matrix destination, the include-from-me loop check, the file must be a YAML mapping.

### Runtime behaviour

Unchanged apart from configuration:

- `role: forwarder` → forwarding as today; health monitoring only with `health_alerts: true`.
- `role: health-monitor` → today's health-monitor-only mode (subscribes to `session.status` only, alerts to `alert_chat_id`).
- `main.py` must not read environment variables except through `load_config`, and branches on the config type (or `role`) rather than on flags.
- `build_waha`, `build_matrix_destination`, and `build_filter_policy` take the specific settings they need (the WAHA settings plus API key, the Matrix settings, the filters) rather than the whole config.

### Editor schema

- A JSON Schema for the YAML files is generated from the config model and committed as `courier-config.schema.json` at the repo root (designer's call). It describes the YAML keys only (no `WAHA_API_KEY`), including the per-role key sets and every field's description.
- One command regenerates it: `uv run python -m courier.config.schema` (designer's call). A test fails when the committed file differs from the generated one.
- Tracked example YAML files start with `# yaml-language-server: $schema=../courier-config.schema.json` (relative path; adjust to the example's location). README tells operators to start deployment YAML files with the tag-pinned URL, `# yaml-language-server: $schema=https://raw.githubusercontent.com/akalex/courier/0.9.0/courier-config.schema.json`, which works in VS Code with the Red Hat YAML extension, including over Remote-SSH on the VM.

### Compose and `.env.example`

- `x-courier-common.environment` contains only `WAHA_API_KEY: ${WAHA_API_KEY:?Set WAHA_API_KEY in .env}`.
- Each Courier service has `container_name` and its YAML mount, and no `environment` block.
- `.env.example` lists only the WAHA variables above and `COURIER_TAG`. `TRANSPORT`, `WAHA_URL`, `WAHA_SESSION`, `LOG_LEVEL`, `LISTEN_PORT`, and `INCLUDE_FROM_ME` are removed.
- `tests/test_compose_config.py` is updated to assert the new shape (no Courier service sets environment beyond the shared `WAHA_API_KEY`; each service mounts its own YAML). The WAHA assertions stay.

### Documentation

- Tracked, annotated examples: one per role (for example `examples/config-forwarder.yaml`, `examples/config-health-monitor.yaml`; exact location left to the builder). Every optional key appears with its default, commented where it is not set. The examples use placeholder chat IDs and URLs that pass validation.
- The three deployment configs (`config.yaml`, `config-tfl.yaml`, `config-health.yaml`) are also tracked as example files (for example `examples/compose/`), and the README deployment steps show them in full (designer's call).
- Keeping docs loadable (designer's call):
  - a test loads every tracked example file through `load_config`;
  - a test extracts every fenced `yaml` block in `README.md` that declares `role` and loads it through `load_config`. Partial fragments (for example a `filters`-only snippet) are allowed only if they do not declare `role`; the README labels them as fragments of a full config.
- README configuration section rewritten around the two places: a short "where does a setting go" rule, one table of YAML keys (key, role, required, default), the two environment variables, and how to turn on editor support with the schema. Remove the env-variable column and every statement that environment variables override YAML.
- README "Routing examples", "Run locally", "Docker", and "Operate and troubleshoot" sections updated so none of them configures Courier through environment variables other than `WAHA_API_KEY` and `CONFIG_PATH`; the routing examples become complete configs with `role` and `webhook_url`, and without `waha.api_key`.
- README deployment steps updated: `.env` holds WAHA's values and `COURIER_TAG`; the three YAML files (`config.yaml`, `config-tfl.yaml`, `config-health.yaml`) are shown complete; the chat-ID lookup step points to the YAML files; the stale `ALERT_CHAT_ID` sentence is removed; `chmod 600` for YAML containing a Matrix token.
- A "Migrating from 0.8.x" README section: old → new table (env var or YAML key → new YAML key), and the VM steps — trim `.env`, rewrite the three YAML files with `role`, `webhook_url`, and `waha`, replace `docker-compose.yml`, recreate the Courier containers. Mention that the old `courier-lupynis` webhook entry, if still present in WAHA, can be removed.
- `CHANGELOG.md` 0.9.0 entry under **Changed** (breaking; also lists the new Pydantic dependency), and version bump to 0.9.0 in `pyproject.toml` and the courier entry in `uv.lock`.

## Constraints

- Python 3.14.
- One new runtime dependency: `pydantic>=2.14,<3`. No `pydantic-settings`. PyYAML still parses files.
- `uv.lock` is regenerated for the new dependency. The maintainer's local uv (0.11.28) is older than the one that last wrote the lock and downgrades its `revision` line from 5 to 3; restore `revision = 5` (or use a newer uv) so the lock format does not regress.
- The pre-commit mypy hook (`.pre-commit-config.yaml`, `mirrors-mypy`) runs in its own environment: add `pydantic` to its `additional_dependencies`. Whether to enable Pydantic's mypy plugin is the builder's choice.
- ruff (line length 120, max complexity 10), mypy clean, `ruff format`.
- Existing behaviour tests for forwarding and health keep passing; config and compose tests are rewritten for the new rules.
- Released as 0.9.0: a breaking change for existing config files on the VM, which the startup errors and the migration section make explicit.

## Left to the builder

- Names and internal layout of the config models (within the rules in Config model), validator style, and how `WAHA_API_KEY` is returned alongside the model.
- Exact error-message wording, provided each names the file, the key path, and where it belongs.
- Location and file names of the tracked examples.
- How tests feed environment variables and files (fixtures, `tmp_path`, `monkeypatch`).
- Whether to enable Pydantic's mypy plugin.
