# Changelog

Notable changes to Courier are documented here. This changelog follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases use Semantic Versioning.

## [0.10.1] - 2026-11-28

### Fixed

- When a message does not match the configured text transform, omit its text but preserve forwarding of media selected by the route's `forward_content` and `media_types` settings.

## [0.10.0] - 2026-11-28

### Added

- Extract named values from message text with an optional regex-and-template `message_transform`, and send the drafted text with eligible media. Crew-name matching supports `екіпаж FPV` and `група FPV` labels with straight, angle, or curly quotes.

## [0.9.1] - 2026-11-23

### Fixed

- Report every config problem even when `role` is missing or invalid, checking the file as the role its other keys point to. Previously a 0.8.x file showed only the missing `role` and hid the rest until the next restart.
- Reject a key that appears twice in the same YAML mapping instead of silently keeping the last value.
- Describe YAML syntax errors by line and column only, so a secret such as a Matrix access token on the offending line is not copied into container logs.
- Ignore local `config*.yaml` files in git and keep `.env`, local configs, and `waha-data/` out of Docker images built from a working checkout (new `.dockerignore`).
- Reorder the "Migrating from 0.8.x" steps so values are copied into the YAML files before they are deleted from `.env`, and point the deployment snippets at 0.9.1.

## [0.9.0] - 2026-11-23

### Changed

- **Breaking:** each Courier container is configured by exactly one YAML file. Courier reads no environment variables except `WAHA_API_KEY` and `CONFIG_PATH`; see "Migrating from 0.8.x" in the README.
- **Breaking:** a required `role` key (`forwarder` or `health-monitor`) replaces `health_alerts_enabled` and `health_monitor_only`. A health monitor accepts no forwarding keys; a forwarder sends health alerts only with `health_alerts: true` (previously on by default).
- **Breaking:** `webhook_url` is required, and `alert_chat_id` no longer falls back to `target_chat_id`.
- **Breaking:** each setting has one spelling: `waha.url`, `waha.session`, `matrix.homeserver_url`, and `matrix.access_token`. The legacy `transport` key and the WAHA API key in YAML are gone.
- Reject unknown or misspelled keys, keys that do not apply to the container's role, and pre-0.9.0 environment variables and keys, with a hint saying where each setting moved. Every problem in the file is reported at once.
- `.env` holds only WAHA's settings and `COURIER_TAG`; Compose passes only `WAHA_API_KEY` to Courier containers.
- Validate configuration with Pydantic v2, a new runtime dependency.
- Add annotated example configs per role and for the Compose deployment, and a JSON Schema (`courier-config.schema.json`) for editor autocomplete and validation.

## [0.8.1] - 2026-11-23

### Fixed

- Allow `HEALTH_MONITOR_ONLY` configurations to omit forwarding source and target chat IDs.
- Validate an explicit `alert_chat_id` for health-monitor-only containers and skip forwarding-only loop checks.

## [0.8.0] - 2026-11-23

### Changed

- Configure route-specific source and destination chat IDs in each mounted YAML config file instead of route-specific environment variables.
- Remove shared route chat-ID variables from `.env.example` and document YAML-only route configuration for Compose deployments.

## [0.7.0] - 2026-11-23

### Fixed

- Mount `config-health.yaml` for the dedicated `courier-health` Compose container instead of sharing `config.yaml`.

## [0.6.1] - 2026-11-22

### Fixed

- Ask WAHA to redeliver a media-only message when its media download fails temporarily (a WAHA server error, request timeout, or connection failure) instead of silently dropping it. Routes that also forward text still send the text alone.

## [0.6.0] - 2026-11-22

### Fixed

- Download WAHA media only for source-chat messages that pass the filters and can match the selected media types. Previously every container downloaded media for every message in every chat before discarding it.
- Stop a media download once it exceeds `max_attachment_size` instead of downloading the whole file first.
- Forward a second message that has no ID instead of dropping it as a duplicate.
- Drop inline media whose base64 data cannot be decoded instead of forwarding an empty attachment.

### Changed

- Deduplicate in one place, the forwarder, instead of in both the WAHA adapter and the forwarder. Filtered messages no longer take up deduplication cache entries.
- Register only the WAHA webhook events each container handles, and replace the event list on Courier's own webhook entry rather than merging into it. The `courier-health` container now subscribes only to `session.status`; forwarding routes with health alerts disabled no longer subscribe to it.
- Refuse to start when `HEALTH_MONITOR_ONLY` is enabled but `HEALTH_ALERTS_ENABLED` is disabled.
- Keep polling WAHA session health after an unexpected probe error instead of silently stopping.
- Split the WAHA transport into a session client, a webhook ingress, and a core `SessionHealthMonitor`; health alert state now lives in core.

## [0.5.0] - 2026-11-22

### Changed

- Organize Courier modules into core, configuration, infrastructure, and adapter packages.
- Split WAHA payload parsing and media hydration from the transport.
- Move Matrix attachment upload and content construction into focused helpers.
- Simplify configuration environment overrides and Forwarder concurrency.
- Extract HTTP parsing and response handling from the webhook server.

## [0.4.4] - 2026-11-22

### Fixed

- Correlate WAHA webhook deliveries with media downloads and forwarding decisions in logs.

### Changed

- Add webhook request, event, and message identifiers to Courier log context.
- Reduce noisy `httpx` INFO output.

## [0.4.3] - 2026-11-21

### Fixed

- Disable WAHA QR-code output in container logs.
- Retry WAHA session setup after an HTTP request timeout.

### Changed

- Default WAHA to the `noweb-arm` image and NOWEB engine.

## [0.4.2] - 2026-11-21

### Fixed

- Detect MP4 attachments by content when WAHA reports a generic MIME type and filename.
- Clarify that WAHA's ARM image may not deliver MP4s through Courier's document fallback.

### Changed

- Allow the WAHA container image to be overridden with `WAHA_IMAGE` in Docker Compose.
- Allow the WAHA default engine to be selected with `WHATSAPP_DEFAULT_ENGINE` in Docker Compose.

## [0.4.1] - 2026-11-21

### Fixed

- Send `.mp4` attachments with a generic MIME type through WAHA's inline video endpoint.

## [0.4.0] - 2026-11-21

### Changed

- Rename the Lupynis Compose route and configuration variables to TFL.

## [0.3.0] - 2026-11-21

### Fixed

- Add a dedicated `courier-health` container and prevent forwarding routes from emitting duplicate health and recovery notifications.

### Added

- Add a health-monitor-only mode for running the dedicated `courier-health` container without forwarding messages.

## [0.2.2] - 2026-11-21

### Fixed

- Pass `ALERT_CHAT_ID` through Compose so health notifications from every route use the configured recipient.

## [0.2.1] - 2026-11-21

### Changed

- Include the installed Courier version in WAHA and Matrix HTTP User-Agent headers.
- Use named HTTP status and WAHA session retry constants for clearer request handling.

## [0.2.0] - 2026-11-21

### Added

- Select whether a route forwards message text, media, or both, and restrict forwarding to specific media types.
- Add a reusable Compose template for Courier routes; configure `courier-lupynis` as a video-only route alongside `courier-aircraft-losses`.

### Changed

- Remove the obsolete `http://courier:8000/webhook` from existing WAHA sessions during startup.

## [0.1.1] - 2026-11-19

### Changed

- Identify WAHA health notifications as Courier Bot and use clearer recovery wording.

## [0.1.0] - 2026-11-06

### Added

- Asynchronous forwarding of WhatsApp messages from a WAHA source to WhatsApp or Matrix destinations.
- Optional sender, included-keyword, and excluded-keyword filters.
- Text and supported media forwarding, with attachment size limits and in-memory duplicate suppression.
- WAHA session and worker health notifications, including a configurable alert recipient.
- Multi-architecture Docker images for `linux/amd64` and `linux/arm64`, with SemVer release tags.

### Fixed

- Restrict setuptools package discovery to the `courier` package so package builds do not include unrelated top-level directories.
