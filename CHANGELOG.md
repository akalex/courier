# Changelog

Notable changes to Courier are documented here. This changelog follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases use Semantic Versioning.

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
