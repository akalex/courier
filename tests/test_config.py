import pytest

from courier.config import load_config


def test_valid_config_loads(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
source_chat_id: source
target_chat_id: target
filters:
  keywords: [urgent]
  senders: [alice]
transport: waha
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(path)
    assert cfg["source_chat_id"] == "source"
    assert cfg["filters"]["keywords"] == ["urgent"]


def test_invalid_config_fails_fast(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("source_chat_id: source\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_config(path)
