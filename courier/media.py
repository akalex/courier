from __future__ import annotations

from pathlib import PurePath

from .domain import Attachment


def is_mp4_attachment(attachment: Attachment) -> bool:
    if attachment.mime_type.lower() != "application/octet-stream":
        return False
    has_mp4_suffix = PurePath(attachment.filename or "").suffix.lower() == ".mp4"
    has_mp4_signature = len(attachment.data) >= 8 and attachment.data[4:8] == b"ftyp"
    return has_mp4_suffix or has_mp4_signature
