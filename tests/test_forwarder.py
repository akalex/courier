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
async def test_forwarder_filter_and_format_message():
    transport = FakeTransport()
    forwarder = Forwarder(
        source_chat_id="source",
        target_chat_id="target",
        transport=transport,
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
    assert transport.sent[0]["text"].startswith("Alice:")
    assert transport.sent[0]["attachments"][0].filename == "file.png"


@pytest.mark.asyncio
async def test_forwarder_drops_duplicate_or_other_chat_messages():
    transport = FakeTransport()
    forwarder = Forwarder(
        source_chat_id="source",
        target_chat_id="target",
        transport=transport,
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
