import pytest

from courier.domain import Message
from courier.filters import AllOf, AnyOf, KeywordFilter, SenderFilter


@pytest.mark.parametrize(
    ("text", "keywords", "expected"),
    [
        ("Please help urgently", ["urgent", "help"], True),
        ("hello there", ["urgent"], False),
        ("", ["urgent"], False),
        ("hello", [], True),
    ],
)
def test_keyword_filter_matches_case_insensitive(text, keywords, expected):
    subject = KeywordFilter(keywords)
    message = Message(id="1", chat_id="chat", sender_id="123", sender_name="Alice", text=text, attachments=())

    assert subject.matches(message) is expected


def test_sender_filter_matches_phone_id_and_name_normalized():
    message = Message(id="1", chat_id="chat", sender_id="123@c.us", sender_name="Alice", text="hello", attachments=())

    assert SenderFilter({"123", "alice"}).matches(message)
    assert SenderFilter({"+123"}).matches(message)


def test_combined_filters_support_and_and_or():
    message = Message(id="1", chat_id="chat", sender_id="123", sender_name="Alice", text="urgent help", attachments=())

    assert AllOf(KeywordFilter(["urgent"]), SenderFilter({"alice"})).matches(message)
    assert AnyOf(KeywordFilter(["urgent"]), SenderFilter({"bob"})).matches(message)
    assert not AllOf(KeywordFilter(["urgent"]), SenderFilter({"bob"})).matches(message)
