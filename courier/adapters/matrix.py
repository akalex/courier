from __future__ import annotations

import logging
import uuid
from urllib.parse import quote

import httpx

from .. import __version__
from ..config import AppConfig
from ..core.domain import Attachment


logger = logging.getLogger(__name__)


class MatrixDestination:
    """Send messages to a Matrix room, including rooms hosted by Element."""

    def __init__(
        self,
        *,
        homeserver_url: str,
        access_token: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.client = client or httpx.AsyncClient(
            base_url=homeserver_url.rstrip("/"),
            headers={"Authorization": f"Bearer {access_token}", "User-Agent": f"courier/{__version__}"},
            timeout=httpx.Timeout(30.0, connect=10.0, read=20.0),
        )

    async def send(self, chat_id: str, text: str, attachments: tuple[Attachment, ...] = ()) -> None:
        """Send a Matrix room message, uploading its first attachment when present."""
        if attachments:
            attachment = attachments[0]
            content_uri = await self._upload_attachment(attachment)
            content = self._attachment_content(text, attachment, content_uri)
        else:
            content = {"msgtype": "m.text", "body": text}

        transaction_id = uuid.uuid4().hex
        room_id = quote(chat_id, safe="")
        response = await self.client.put(
            f"/_matrix/client/v3/rooms/{room_id}/send/m.room.message/{transaction_id}",
            json=content,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            logger.exception("Matrix message send failed for room_id=%s", chat_id)
            raise

    async def _upload_attachment(self, attachment: Attachment) -> str:
        response = await self.client.post(
            "/_matrix/client/v3/upload",
            params={"filename": attachment.filename or "attachment"},
            content=attachment.data,
            headers={"Content-Type": attachment.mime_type},
        )
        response.raise_for_status()
        try:
            upload_data = response.json()
        except ValueError as exc:
            raise ValueError("Matrix returned an invalid media upload response") from exc
        content_uri = upload_data.get("content_uri") if isinstance(upload_data, dict) else None
        if not isinstance(content_uri, str) or not content_uri.startswith("mxc://"):
            raise ValueError("Matrix media upload response is missing a valid content_uri")
        return content_uri

    @staticmethod
    def _attachment_content(text: str, attachment: Attachment, content_uri: str) -> dict[str, object]:
        media_type = next(
            (
                matrix_type
                for mime_prefix, matrix_type in (
                    ("image/", "m.image"),
                    ("video/", "m.video"),
                    ("audio/", "m.audio"),
                )
                if attachment.mime_type.startswith(mime_prefix)
            ),
            "m.file",
        )
        filename = attachment.filename or "attachment"
        return {
            "msgtype": media_type,
            "body": text or filename,
            "filename": filename,
            "url": content_uri,
            "info": {"mimetype": attachment.mime_type, "size": len(attachment.data)},
        }

    async def stop(self) -> None:
        """Close the Matrix HTTP client."""
        await self.client.aclose()


def build_matrix_destination(config: AppConfig) -> MatrixDestination:
    """Construct a Matrix destination from application configuration."""
    return MatrixDestination(
        homeserver_url=config.matrix_homeserver_url,
        access_token=config.matrix_access_token,
    )
