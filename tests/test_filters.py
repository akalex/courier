import pytest

from courier.config import load_config
from courier.core.domain import Message
from courier.core.filters import AllOf, AnyOf, ExcludeKeywordFilter, KeywordFilter, SenderFilter
from courier.main import build_filter_policy


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


@pytest.mark.parametrize(
    ("text", "keywords", "expected"),
    [
        ("Meet in New York", ["new york"], False),
        ("CITY centre", ["city"], False),
        ("Town centre", ["city"], True),
        ("", ["city"], True),
        ("New York", [], True),
    ],
)
def test_exclude_keyword_filter_rejects_case_insensitive_substrings(text, keywords, expected):
    subject = ExcludeKeywordFilter(keywords)
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


def test_excluded_keyword_rejects_even_when_an_allow_filter_matches():
    message = Message(id="1", chat_id="chat", sender_id="123", sender_name="Alice", text="city alert", attachments=())

    policy = AllOf(
        AnyOf(KeywordFilter(["alert"]), SenderFilter({"alice"})),
        ExcludeKeywordFilter(["city"]),
    )

    assert not policy.matches(message)


@pytest.mark.parametrize(
    ("text", "sender", "expected"),
    [
        ("city alert", "Alice", False),
        ("suburb alert", "Alice", True),
        ("suburb update", "Bob", False),
        ("suburb update", "Alice", True),
    ],
)
def test_configured_filter_policy_applies_exclusions_as_vetoes(tmp_path, text, sender, expected):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
source_chat_id: source
target_chat_id: target
filters:
  keywords: [alert]
  exclude_keywords: [city]
  senders: [Alice]
""".strip(),
        encoding="utf-8",
    )
    policy = build_filter_policy(load_config(path))
    message = Message(
        id="1",
        chat_id="source",
        sender_id="123",
        sender_name=sender,
        text=text,
        attachments=(),
    )

    assert policy is not None
    assert policy.matches(message) is expected


def test_excluded_keywords_can_be_configured_without_include_filters(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
source_chat_id: source
target_chat_id: target
filters:
  exclude_keywords: [city]
""".strip(),
        encoding="utf-8",
    )
    policy = build_filter_policy(load_config(path))
    allowed_message = Message(
        id="1",
        chat_id="source",
        sender_id="123",
        sender_name="Alice",
        text="suburb update",
        attachments=(),
    )
    excluded_message = Message(
        id="2",
        chat_id="source",
        sender_id="123",
        sender_name="Alice",
        text="city update",
        attachments=(),
    )

    assert policy is not None
    assert policy.matches(allowed_message)
    assert not policy.matches(excluded_message)
