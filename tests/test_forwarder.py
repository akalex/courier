import pytest

from courier.domain import Attachment, Message
from courier.filters import AllOf, KeywordFilter, SenderFilter
from courier.forwarder import Forwarder


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
