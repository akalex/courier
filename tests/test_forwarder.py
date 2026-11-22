import asyncio

import pytest

from courier.core.domain import Attachment, Message, TransientMediaError
from courier.core.filters import AllOf, KeywordFilter, SenderFilter
from courier.core.forwarder import Forwarder


class FakeTransport:
    def __init__(self):
        self.sent = []

    async def send(self, chat_id, text, attachments=()):
        self.sent.append({"chat_id": chat_id, "text": text, "attachments": attachments})

    async def stop(self):
        pass


class RecordingMedia:
    """Lazy media whose fetch yields to the event loop like a real download."""

    def __init__(self, mime_type, filename, data):
        self.mime_type = mime_type
        self.filename = filename
        self._data = data
        self.fetch_limits = []

    async def fetch(self, *, max_bytes):
        self.fetch_limits.append(max_bytes)
        await asyncio.sleep(0)
        if self._data is None or len(self._data) > max_bytes:
            return None
        return self._data


class FlakyMedia:
    """Lazy media whose download fails temporarily before it succeeds."""

    def __init__(self, mime_type, filename, data, *, failures):
        self.mime_type = mime_type
        self.filename = filename
        self._data = data
        self._failures = failures
        self.fetches = 0

    async def fetch(self, *, max_bytes):
        self.fetches += 1
        if self.fetches <= self._failures:
            raise TransientMediaError("WAHA returned HTTP 503")
        return self._data


def _message(message_id, *, chat_id="source", text="", attachments=()):
    return Message(
        id=message_id,
        chat_id=chat_id,
        sender_id="123",
        sender_name="Alice",
        text=text,
        attachments=attachments,
    )


@pytest.mark.asyncio
async def test_forwarder_preserves_message_text_and_forwards_attachment():
    transport = FakeTransport()
    forwarder = Forwarder(
        source_chat_id="source",
        target_chat_id="target",
        destination=transport,
        filter_policy=AllOf(KeywordFilter(["urgent"]), SenderFilter({"alice"})),
        recent_ids=100,
    )

    message = Message(
        id="msg-1",
        chat_id="source",
        sender_id="123",
        sender_name="Alice",
        text="urgent update",
        attachments=(Attachment("image/png", "file.png", b"abc"),),
    )

    await forwarder.handle_message(message)
    assert len(transport.sent) == 1
    assert transport.sent[0]["chat_id"] == "target"
    assert transport.sent[0]["text"] == "urgent update"
    assert transport.sent[0]["attachments"][0].filename == "file.png"


@pytest.mark.asyncio
async def test_forwarder_drops_duplicate_or_other_chat_messages():
    transport = FakeTransport()
    forwarder = Forwarder(
        source_chat_id="source",
        target_chat_id="target",
        destination=transport,
        filter_policy=AllOf(KeywordFilter(["urgent"]), SenderFilter({"alice"})),
        recent_ids=10,
    )

    first = Message(
        id="dup",
        chat_id="source",
        sender_id="123",
        sender_name="Alice",
        text="urgent update",
        attachments=(),
    )
    second = Message(
        id="dup",
        chat_id="source",
        sender_id="123",
        sender_name="Alice",
        text="urgent update",
        attachments=(),
    )
    other = Message(
        id="2",
        chat_id="other",
        sender_id="123",
        sender_name="Alice",
        text="urgent update",
        attachments=(),
    )

    await forwarder.handle_message(first)
    await forwarder.handle_message(second)
    await forwarder.handle_message(other)

    assert len(transport.sent) == 1


@pytest.mark.asyncio
async def test_forwarder_retries_failed_send_and_deduplicates_after_success():
    class FailOnceTransport(FakeTransport):
        def __init__(self):
            super().__init__()
            self.fail = True

        async def send(self, chat_id, text, attachments=()):
            if self.fail:
                self.fail = False
                raise RuntimeError("temporary delivery failure")
            await super().send(chat_id, text, attachments)

    transport = FailOnceTransport()
    forwarder = Forwarder(source_chat_id="source", target_chat_id="target", destination=transport)
    message = Message(
        id="retry-me",
        chat_id="source",
        sender_id="123",
        sender_name="Alice",
        text="message",
        attachments=(),
    )

    with pytest.raises(RuntimeError, match="temporary delivery failure"):
        await forwarder.handle_message(message)
    await forwarder.handle_message(message)
    await forwarder.handle_message(message)

    assert len(transport.sent) == 1


@pytest.mark.asyncio
async def test_forwarder_drops_concurrent_duplicate_without_serializing_network_sends():
    class BlockingTransport(FakeTransport):
        def __init__(self):
            super().__init__()
            self.first_started = asyncio.Event()
            self.release_first = asyncio.Event()

        async def send(self, chat_id, text, attachments=()):
            if text == "first":
                self.first_started.set()
                await self.release_first.wait()
            await super().send(chat_id, text, attachments)

    transport = BlockingTransport()
    forwarder = Forwarder(source_chat_id="source", target_chat_id="target", destination=transport)

    def message(message_id, text):
        return Message(
            id=message_id,
            chat_id="source",
            sender_id="123",
            sender_name="Alice",
            text=text,
        )

    first_task = asyncio.create_task(forwarder.handle_message(message("shared", "first")))
    await transport.first_started.wait()
    second_task = asyncio.create_task(forwarder.handle_message(message("shared", "duplicate")))
    third_task = asyncio.create_task(forwarder.handle_message(message("third", "second")))

    await asyncio.wait_for(third_task, timeout=1)
    transport.release_first.set()
    await asyncio.wait_for(first_task, timeout=1)
    await asyncio.wait_for(second_task, timeout=1)

    assert {item["text"] for item in transport.sent} == {"first", "second"}
    assert len(transport.sent) == 2


@pytest.mark.asyncio
async def test_forwarder_bounds_successful_message_deduplication_cache():
    transport = FakeTransport()
    forwarder = Forwarder(source_chat_id="source", target_chat_id="target", destination=transport, recent_ids=1)

    def message(message_id):
        return Message(
            id=message_id,
            chat_id="source",
            sender_id="123",
            sender_name="Alice",
            text=message_id,
            attachments=(),
        )

    await forwarder.handle_message(message("first"))
    await forwarder.handle_message(message("second"))
    await forwarder.handle_message(message("first"))

    assert [item["text"] for item in transport.sent] == ["first", "second", "first"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mime_type", "filename"),
    [("video/mp4", "clip.mp4"), ("application/octet-stream", "clip.mp4")],
)
async def test_forwarder_can_send_only_video_attachments_and_drops_other_messages(mime_type, filename):
    transport = FakeTransport()
    forwarder = Forwarder(
        source_chat_id="source",
        target_chat_id="target",
        destination=transport,
        forward_content=("media",),
        media_types=("video",),
    )
    video_message = Message(
        id="video",
        chat_id="source",
        sender_id="123",
        sender_name="Alice",
        text="caption must not be forwarded",
        attachments=(Attachment(mime_type, filename, b"video"),),
    )
    image_message = Message(
        id="image",
        chat_id="source",
        sender_id="123",
        sender_name="Alice",
        text="not without a video",
        attachments=(Attachment("image/jpeg", "photo.jpg", b"image"),),
    )
    text_message = Message(
        id="text",
        chat_id="source",
        sender_id="123",
        sender_name="Alice",
        text="text only",
    )

    await forwarder.handle_message(video_message)
    await forwarder.handle_message(image_message)
    await forwarder.handle_message(text_message)

    assert len(transport.sent) == 1
    assert transport.sent[0]["text"] == ""
    assert transport.sent[0]["attachments"][0].filename == "clip.mp4"


@pytest.mark.asyncio
async def test_forwarder_recognizes_mp4_content_with_generic_mime_and_filename():
    transport = FakeTransport()
    forwarder = Forwarder(
        source_chat_id="source",
        target_chat_id="target",
        destination=transport,
        forward_content=("media",),
        media_types=("video",),
    )
    mp4_data = b"\x00\x00\x00\x18ftypisom"
    mp4_message = Message(
        id="video-generic",
        chat_id="source",
        sender_id="123",
        sender_name="Alice",
        attachments=(Attachment("application/octet-stream", "attachment", mp4_data),),
    )

    await forwarder.handle_message(mp4_message)

    assert len(transport.sent) == 1
    assert transport.sent[0]["attachments"][0].data == mp4_data


@pytest.mark.asyncio
async def test_forwarder_can_select_text_without_attachments():
    transport = FakeTransport()
    forwarder = Forwarder(
        source_chat_id="source",
        target_chat_id="target",
        destination=transport,
        forward_content=("text",),
    )
    message = Message(
        id="text-only",
        chat_id="source",
        sender_id="123",
        sender_name="Alice",
        text="hello",
        attachments=(Attachment("video/mp4", "clip.mp4", b"video"),),
    )

    await forwarder.handle_message(message)

    assert transport.sent[0]["text"] == "hello"
    assert transport.sent[0]["attachments"] == ()


@pytest.mark.asyncio
async def test_forwarder_does_not_fetch_media_for_other_chats_or_filtered_messages():
    transport = FakeTransport()
    forwarder = Forwarder(
        source_chat_id="source",
        target_chat_id="target",
        destination=transport,
        filter_policy=KeywordFilter(["urgent"]),
    )
    other_chat_media = RecordingMedia("image/jpeg", "a.jpg", b"jpeg")
    filtered_media = RecordingMedia("image/jpeg", "b.jpg", b"jpeg")

    await forwarder.handle_message(
        _message("other", chat_id="other", text="urgent", attachments=(other_chat_media,)),
    )
    await forwarder.handle_message(_message("boring", text="boring", attachments=(filtered_media,)))

    assert other_chat_media.fetch_limits == []
    assert filtered_media.fetch_limits == []
    assert transport.sent == []


@pytest.mark.asyncio
async def test_forwarder_fetches_media_once_for_concurrent_duplicate_deliveries():
    transport = FakeTransport()
    forwarder = Forwarder(source_chat_id="source", target_chat_id="target", destination=transport)
    media = RecordingMedia("image/jpeg", "photo.jpg", b"jpeg")
    message = _message("twin", text="caption", attachments=(media,))

    await asyncio.gather(forwarder.handle_message(message), forwarder.handle_message(message))

    assert len(media.fetch_limits) == 1
    assert transport.sent == [
        {"chat_id": "target", "text": "caption", "attachments": (Attachment("image/jpeg", "photo.jpg", b"jpeg"),)},
    ]


@pytest.mark.asyncio
async def test_forwarder_fetches_media_again_when_retrying_a_failed_send():
    class FailOnceTransport(FakeTransport):
        def __init__(self):
            super().__init__()
            self.failed = False

        async def send(self, chat_id, text, attachments=()):
            if not self.failed:
                self.failed = True
                raise RuntimeError("temporary delivery failure")
            await super().send(chat_id, text, attachments)

    transport = FailOnceTransport()
    forwarder = Forwarder(source_chat_id="source", target_chat_id="target", destination=transport)
    media = RecordingMedia("image/jpeg", "photo.jpg", b"jpeg")
    message = _message("retry", attachments=(media,))

    with pytest.raises(RuntimeError):
        await forwarder.handle_message(message)
    await forwarder.handle_message(message)
    await forwarder.handle_message(message)

    assert len(media.fetch_limits) == 2
    assert len(transport.sent) == 1


@pytest.mark.asyncio
async def test_forwarder_limits_media_fetch_to_max_attachment_size():
    transport = FakeTransport()
    forwarder = Forwarder(
        source_chat_id="source",
        target_chat_id="target",
        destination=transport,
        max_attachment_size=4,
    )
    media = RecordingMedia("image/jpeg", "photo.jpg", b"jpeg")

    await forwarder.handle_message(_message("limited", text="caption", attachments=(media,)))

    assert media.fetch_limits == [4]
    assert transport.sent[0]["attachments"] == (Attachment("image/jpeg", "photo.jpg", b"jpeg"),)


@pytest.mark.asyncio
async def test_forwarder_skips_in_memory_attachment_larger_than_limit():
    transport = FakeTransport()
    forwarder = Forwarder(
        source_chat_id="source",
        target_chat_id="target",
        destination=transport,
        max_attachment_size=4,
    )

    await forwarder.handle_message(
        _message("big", text="caption", attachments=(Attachment("image/jpeg", "big.jpg", b"12345"),)),
    )

    assert transport.sent == [{"chat_id": "target", "text": "caption", "attachments": ()}]


@pytest.mark.asyncio
async def test_forwarder_forwards_text_when_media_is_unavailable():
    transport = FakeTransport()
    forwarder = Forwarder(source_chat_id="source", target_chat_id="target", destination=transport)

    await forwarder.handle_message(
        _message("no-media", text="caption", attachments=(RecordingMedia("image/jpeg", "gone.jpg", None),)),
    )

    assert transport.sent == [{"chat_id": "target", "text": "caption", "attachments": ()}]


@pytest.mark.asyncio
async def test_forwarder_asks_for_redelivery_when_media_only_message_cannot_be_downloaded_yet():
    transport = FakeTransport()
    forwarder = Forwarder(
        source_chat_id="source",
        target_chat_id="target",
        destination=transport,
        forward_content=("media",),
        media_types=("video",),
    )
    media = FlakyMedia("video/mp4", "clip.mp4", b"video", failures=1)
    message = _message("video", attachments=(media,))

    with pytest.raises(TransientMediaError):
        await forwarder.handle_message(message)
    assert transport.sent == []

    await forwarder.handle_message(message)
    await forwarder.handle_message(message)

    assert media.fetches == 2
    assert transport.sent == [
        {"chat_id": "target", "text": "", "attachments": (Attachment("video/mp4", "clip.mp4", b"video"),)},
    ]


@pytest.mark.asyncio
async def test_forwarder_sends_text_when_media_is_temporarily_unavailable():
    transport = FakeTransport()
    forwarder = Forwarder(source_chat_id="source", target_chat_id="target", destination=transport)
    media = FlakyMedia("image/jpeg", "photo.jpg", b"jpeg", failures=1)

    await forwarder.handle_message(_message("captioned", text="caption", attachments=(media,)))

    assert transport.sent == [{"chat_id": "target", "text": "caption", "attachments": ()}]


@pytest.mark.asyncio
async def test_forwarder_does_not_refetch_media_only_message_whose_media_was_unavailable():
    transport = FakeTransport()
    forwarder = Forwarder(
        source_chat_id="source",
        target_chat_id="target",
        destination=transport,
        forward_content=("media",),
    )
    media = RecordingMedia("image/jpeg", "gone.jpg", None)
    message = _message("media-only", text="caption", attachments=(media,))

    await forwarder.handle_message(message)
    await forwarder.handle_message(message)

    assert len(media.fetch_limits) == 1
    assert transport.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mime_type", "filename"),
    [("video/mp4", "clip.mp4"), ("application/octet-stream", "attachment"), ("application/pdf", "doc.pdf")],
)
async def test_forwarder_does_not_fetch_media_that_cannot_match_selected_types(mime_type, filename):
    transport = FakeTransport()
    forwarder = Forwarder(
        source_chat_id="source",
        target_chat_id="target",
        destination=transport,
        forward_content=("media",),
        media_types=("image",),
    )
    media = RecordingMedia(mime_type, filename, b"\x00\x00\x00\x18ftypisom")

    await forwarder.handle_message(_message("wrong-type", attachments=(media,)))

    assert media.fetch_limits == []
    assert transport.sent == []


@pytest.mark.asyncio
async def test_forwarder_drops_generic_binary_without_mp4_signature_when_only_video_is_selected():
    transport = FakeTransport()
    forwarder = Forwarder(
        source_chat_id="source",
        target_chat_id="target",
        destination=transport,
        forward_content=("media",),
        media_types=("video",),
    )
    media = RecordingMedia("application/octet-stream", "attachment", b"not-a-video")

    await forwarder.handle_message(_message("binary", attachments=(media,)))

    assert len(media.fetch_limits) == 1
    assert transport.sent == []


@pytest.mark.asyncio
async def test_forwarder_does_not_deduplicate_messages_without_an_id():
    transport = FakeTransport()
    forwarder = Forwarder(source_chat_id="source", target_chat_id="target", destination=transport)

    await forwarder.handle_message(_message("", text="first"))
    await forwarder.handle_message(_message("", text="second"))

    assert [item["text"] for item in transport.sent] == ["first", "second"]


@pytest.mark.asyncio
async def test_forwarder_filtered_messages_do_not_evict_forwarded_ids_from_dedupe_cache():
    transport = FakeTransport()
    forwarder = Forwarder(
        source_chat_id="source",
        target_chat_id="target",
        destination=transport,
        filter_policy=KeywordFilter(["urgent"]),
        recent_ids=1,
    )

    await forwarder.handle_message(_message("forwarded", text="urgent"))
    await forwarder.handle_message(_message("filtered", text="boring"))
    await forwarder.handle_message(_message("forwarded", text="urgent"))

    assert [item["text"] for item in transport.sent] == ["urgent"]
