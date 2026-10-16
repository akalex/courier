from __future__ import annotations

import logging
import uuid
from urllib.parse import quote

import httpx

from ..config import AppConfig
from ..domain import Attachment


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
            headers={"Authorization": f"Bearer {access_token}", "User-Agent": "courier/0.1.0"},
            timeout=httpx.Timeout(30.0, connect=10.0, read=20.0),
        )

    async def send(self, chat_id: str, text: str, attachments: tuple[Attachment, ...] = ()) -> None:
        """Send a Matrix room message, uploading its first attachment when present."""
        content: dict[str, object]
        if attachments:
            attachment = attachments[0]
            upload_response = await self.client.post(
                "/_matrix/client/v3/upload",
                params={"filename": attachment.filename or "attachment"},
                content=attachment.data,
                headers={"Content-Type": attachment.mime_type},
            )
            upload_response.raise_for_status()
            try:
                upload_data = upload_response.json()
            except ValueError as exc:
                raise ValueError("Matrix returned an invalid media upload response") from exc
            content_uri = upload_data.get("content_uri") if isinstance(upload_data, dict) else None
            if not isinstance(content_uri, str) or not content_uri.startswith("mxc://"):
                raise ValueError("Matrix media upload response is missing a valid content_uri")

            msgtype = (
                "m.image"
                if attachment.mime_type.startswith("image/")
                else "m.video"
                if attachment.mime_type.startswith("video/")
                else "m.audio"
                if attachment.mime_type.startswith("audio/")
                else "m.file"
            )
            content = {
                "msgtype": msgtype,
                "body": text or attachment.filename or "attachment",
                "filename": attachment.filename or "attachment",
                "url": content_uri,
                "info": {"mimetype": attachment.mime_type, "size": len(attachment.data)},
            }
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

    async def stop(self) -> None:
        """Close the Matrix HTTP client."""
        await self.client.aclose()


def build_matrix_destination(config: AppConfig) -> MatrixDestination:
    """Construct a Matrix destination from application configuration."""
    return MatrixDestination(
        homeserver_url=config.matrix_homeserver_url,
        access_token=config.matrix_access_token,
    )
