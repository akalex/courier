from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any

import httpx

from ..core.domain import Attachment, MediaRef, TransientMediaError
from .waha_payload import first


logger = logging.getLogger(__name__)

MEDIA_DOWNLOAD_TIMEOUT = httpx.Timeout(20.0, connect=10.0, read=20.0)
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class WAHAMediaFetcher:
    """Turn WAHA media descriptors into lazy references and download them from WAHA only."""

    def __init__(self, *, base_url: str, http: httpx.AsyncClient) -> None:
        self._base_url = httpx.URL(base_url.rstrip("/"))
        self._http = http

    def media_ref(self, media: Any, message_id: str) -> MediaRef | None:
        """Describe a webhook's media without downloading it; None when nothing can be retrieved."""
        if not isinstance(media, dict):
            return None
        mime_type = first(media, "mimeType", "mimetype") or "application/octet-stream"
        filename = first(media, "fileName", "filename") or "attachment"
        url = first(media, "url", "mediaUrl")
        if url:
            return WAHAMediaRef(
                mime_type=mime_type, filename=filename, url=str(url), message_id=message_id, fetcher=self
            )
        inline = _decode_inline(media.get("data"))
        return Attachment(mime_type, filename, inline) if inline else None

    async def download(self, media_url: str, message_id: str, max_bytes: int) -> bytes | None:
        resolved = self._resolve(media_url, message_id)
        if resolved is None:
            return None
        logger.info("Downloading WAHA media for message id=%s", message_id or "-")
        try:
            async with self._http.stream("GET", str(resolved), timeout=MEDIA_DOWNLOAD_TIMEOUT) as response:
                response.raise_for_status()
                data = await _read_at_most(response, max_bytes)
        except httpx.HTTPError as exc:
            if _may_succeed_later(exc):
                logger.warning("WAHA media for message id=%s is temporarily unavailable: %s", message_id, exc)
                raise TransientMediaError(f"WAHA media for message id={message_id} is temporarily unavailable") from exc
            logger.warning(
                "Failed to download WAHA media for message id=%s from %s; forwarding text only",
                message_id,
                resolved,
                exc_info=True,
            )
            return None
        if data is None:
            logger.warning("Skipping WAHA media for message id=%s larger than %d bytes", message_id, max_bytes)
            return None
        if not data:
            logger.warning("WAHA returned empty media for message id=%s; forwarding text only", message_id)
            return None
        logger.info("Downloaded WAHA media for message id=%s bytes=%d", message_id or "-", len(data))
        return data

    def _resolve(self, media_url: str, message_id: str) -> httpx.URL | None:
        """Resolve a media URL against WAHA, refusing any origin other than WAHA's own."""
        base_url = self._base_url
        try:
            resolved = base_url.join(media_url)
        except TypeError, ValueError:
            logger.warning("Skipping invalid WAHA media URL for message id=%s", message_id)
            return None
        if resolved.host in LOOPBACK_HOSTS:
            resolved = resolved.copy_with(scheme=base_url.scheme, host=base_url.host, port=base_url.port)
        if (
            resolved.username
            or resolved.password
            or resolved.scheme != base_url.scheme
            or resolved.host != base_url.host
            or resolved.port != base_url.port
        ):
            logger.warning("Skipping WAHA media download from a non-WAHA origin for message id=%s", message_id)
            return None
        return resolved


@dataclass(frozen=True)
class WAHAMediaRef:
    """Media hosted by WAHA, downloaded only when the forwarder asks for it."""

    mime_type: str
    filename: str | None
    url: str
    message_id: str
    fetcher: WAHAMediaFetcher = field(repr=False, compare=False)

    async def fetch(self, *, max_bytes: int) -> bytes | None:
        return await self.fetcher.download(self.url, self.message_id, max_bytes)


def is_retryable_status(status_code: int) -> bool:
    """Whether a WAHA HTTP error status may clear up on its own: a server error or a request timeout."""
    return status_code >= HTTPStatus.INTERNAL_SERVER_ERROR or status_code == HTTPStatus.REQUEST_TIMEOUT


def _may_succeed_later(error: httpx.HTTPError) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return is_retryable_status(error.response.status_code)
    return isinstance(error, httpx.TransportError)


def _decode_inline(data: Any) -> bytes:
    if not data:
        return b""
    try:
        return base64.b64decode(data, validate=False)
    except TypeError, ValueError:
        return b""


async def _read_at_most(response: httpx.Response, max_bytes: int) -> bytes | None:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > max_bytes:
            return None
    return bytes(body)
