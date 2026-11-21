# Changelog

Notable changes to Courier are documented here. This changelog follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases use Semantic Versioning.

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
