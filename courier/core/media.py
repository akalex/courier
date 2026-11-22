from __future__ import annotations

from pathlib import PurePath

from .domain import Attachment


GENERIC_MIME_TYPE = "application/octet-stream"
VIDEO_EXTENSIONS = frozenset({".3gp", ".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm", ".wmv"})


def is_mp4_attachment(attachment: Attachment) -> bool:
    if attachment.mime_type.lower() != GENERIC_MIME_TYPE:
        return False
    has_mp4_suffix = PurePath(attachment.filename or "").suffix.lower() == ".mp4"
    has_mp4_signature = len(attachment.data) >= 8 and attachment.data[4:8] == b"ftyp"
    return has_mp4_suffix or has_mp4_signature


def candidate_media_kinds(mime_type: str, filename: str | None) -> frozenset[str]:
    """Return the media kinds an attachment can turn out to be, judging by metadata alone.

    A generic binary without a video extension may still be an MP4 recognizable only from its bytes.
    """
    mime_type = mime_type.lower()
    if mime_type.startswith(("image/", "video/", "audio/")):
        return frozenset({mime_type.split("/", 1)[0]})
    if mime_type != GENERIC_MIME_TYPE:
        return frozenset({"file"})
    if PurePath(filename or "").suffix.lower() in VIDEO_EXTENSIONS:
        return frozenset({"video"})
    return frozenset({"video", "file"})


def media_kind(attachment: Attachment) -> str:
    """Return the media kind of a downloaded attachment: image, video, audio, or file."""
    kinds = candidate_media_kinds(attachment.mime_type, attachment.filename)
    if len(kinds) == 1:
        return next(iter(kinds))
    return "video" if is_mp4_attachment(attachment) else "file"
