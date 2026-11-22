# Changelog

Notable changes to Courier are documented here. This changelog follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases use Semantic Versioning.

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
