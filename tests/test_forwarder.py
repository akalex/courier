import asyncio

import pytest

from courier.core.domain import Attachment, Message
from courier.core.filters import AllOf, KeywordFilter, SenderFilter
from courier.core.forwarder import Forwarder


class FakeTransport:
    def __init__(self):
        self.sent = []

    async def start(self, on_message):
        self.on_message = on_message

    async def send(self, chat_id, text, attachments=()):
        self.sent.append({"chat_id": chat_id, "text": text, "attachments": attachments})

    async def stop(self):
        pass


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
