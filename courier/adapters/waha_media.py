from __future__ import annotations

import base64
import logging
from dataclasses import replace
from typing import Any

import httpx

from ..core.domain import Attachment, Message
from .waha_payload import first


logger = logging.getLogger(__name__)


class WAHAMediaHydrator:
    def __init__(self, *, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def resolve_media_url(self, media_url: Any, message_id: str) -> httpx.URL | None:
        base_url = httpx.URL(self.base_url)
        try:
            resolved = base_url.join(str(media_url))
        except TypeError, ValueError:
            logger.warning("Skipping invalid WAHA media URL for message id=%s", message_id)
            return None
        if resolved.host in {"localhost", "127.0.0.1", "::1"}:
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

    async def hydrate(
        self,
        message: Message,
        payload: dict[str, Any],
        session: httpx.AsyncClient | None,
    ) -> Message:
        media = payload.get("media") if isinstance(payload, dict) else None
        if not isinstance(media, dict):
            return message

        media_url = first(media, "url", "mediaUrl")
        if not media_url:
            payload_data = media.get("data")
            if payload_data:
                try:
                    decoded = base64.b64decode(payload_data, validate=False)
                except Exception:
                    decoded = b""
                attachments = tuple(
                    Attachment(
                        mime_type=media.get("mimeType") or media.get("mimetype") or attachment.mime_type,
                        filename=media.get("fileName") or media.get("filename") or attachment.filename,
                        data=decoded if attachment.data == b"" else attachment.data,
                    )
                    for attachment in message.attachments
                )
                return replace(message, attachments=attachments)
            return replace(message, attachments=())

        resolved_url = self.resolve_media_url(media_url, message.id)
        if resolved_url is None or session is None:
            return replace(message, attachments=())

        try:
            logger.info("Downloading WAHA media for message id=%s", message.id or "-")
            response = await session.get(
                str(resolved_url),
                timeout=httpx.Timeout(20.0, connect=10.0, read=20.0),
            )
            response.raise_for_status()
            data = response.content
            if not data:
                logger.warning("WAHA returned empty media for message id=%s; forwarding text only", message.id)
                return replace(message, attachments=())
            logger.info("Downloaded WAHA media for message id=%s bytes=%d", message.id or "-", len(data))
        except httpx.HTTPError:
            logger.warning(
                "Failed to download WAHA media for message id=%s from %s; forwarding text only",
                message.id,
                resolved_url,
                exc_info=True,
            )
            return replace(message, attachments=())

        attachment = (
            message.attachments[0]
            if message.attachments
            else Attachment(
                mime_type=media.get("mimeType") or media.get("mimetype") or "application/octet-stream",
                filename=media.get("fileName") or media.get("filename") or "attachment",
                data=data,
            )
        )
        return replace(message, attachments=(Attachment(attachment.mime_type, attachment.filename, data),))
