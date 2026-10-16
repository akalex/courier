from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class FilterSettings:
    keywords: tuple[str, ...]
    senders: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AppConfig:
    transport: str
    source_chat_id: str
    target_chat_id: str
    listen_port: int
    log_level: str
    waha_url: str
    waha_api_key: str
    waha_session: str
    webhook_url: str
    include_from_me: bool
    filters: FilterSettings
    max_attachment_size: int = 20 * 1024 * 1024


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load and validate a YAML config file into a dictionary.

    Args:
        path: Filesystem path to the YAML config to read.

    Returns:
        A dictionary containing the parsed top-level YAML mapping.

    Raises:
        ValueError: If the file is missing, malformed, or not a top-level mapping.
    """
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except FileNotFoundError as exc:  # pragma: no cover - validated in load_config
        raise ValueError(f"Config file not found: {path}") from exc
    except yaml.YAMLError as exc:  # pragma: no cover - validated in load_config
        raise ValueError(f"Malformed YAML in {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} must contain a mapping at the top level")
    return data


def _coerce_list(value: Any, *, name: str) -> list[str]:
    """Normalize filter values from YAML or environment configuration.

    Args:
        value: Raw configured value as a string or list.
        name: Field name used in validation errors.

    Returns:
        A normalized list of trimmed strings.

    Raises:
        ValueError: If the value is neither a string nor a list of strings.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ValueError(f"Config field '{name}' must be a string or list of strings")


def _coerce_bool(value: Any, *, name: str) -> bool:
    """Normalize boolean settings from YAML or environment configuration."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError(f"Config field '{name}' must be a boolean")


def _coerce_int(value: Any, *, name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Config field '{name}' must be an integer") from exc
    if result <= 0:
        raise ValueError(f"Config field '{name}' must be greater than zero")
    return result


def _coerce_string(value: Any, *, name: str, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, (str, int, float)):
        raise ValueError(f"Config field '{name}' must be a string")
    return str(value).strip()


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    """Load the project configuration from YAML defaults and environment overrides.

    Args:
        path: Optional path to a YAML file. If omitted, the value of
            CONFIG_PATH is used or the default config.yaml file is loaded.

    Returns:
        A validated immutable application settings object.

    Raises:
        ValueError: If the configuration is missing required keys or has an invalid format.
    """
    config_path = Path(path) if path is not None else Path(os.getenv("CONFIG_PATH", "config.yaml"))
    config_path = config_path.expanduser()

    raw = _load_yaml(config_path)
    waha_config = raw.get("waha") or {}
    if not isinstance(waha_config, dict):
        raise ValueError("Config field 'waha' must be a mapping")
    filters_cfg = raw.get("filters") or {}
    if not isinstance(filters_cfg, dict):
        raise ValueError("Config field 'filters' must be a mapping")

    transport = _coerce_string(os.getenv("TRANSPORT", raw.get("transport", "waha")), name="transport").lower()
    if not transport:
        raise ValueError("Config requires a transport name (for example: 'waha')")

    source_chat_id = _coerce_string(os.getenv("SOURCE_CHAT_ID", raw.get("source_chat_id")), name="source_chat_id")
    target_chat_id = _coerce_string(os.getenv("TARGET_CHAT_ID", raw.get("target_chat_id")), name="target_chat_id")
    listen_port = _coerce_int(os.getenv("LISTEN_PORT", raw.get("listen_port", 8000)), name="listen_port")
    max_attachment_size = _coerce_int(
        raw.get("max_attachment_size", 20 * 1024 * 1024),
        name="max_attachment_size",
    )
    log_level = _coerce_string(os.getenv("LOG_LEVEL", raw.get("log_level", "INFO")), name="log_level").upper()
    if log_level not in logging.getLevelNamesMapping():
        raise ValueError(f"Config field 'log_level' has unsupported value: {log_level}")

    waha_url = _coerce_string(
        os.getenv("WAHA_URL", raw.get("waha_url") or waha_config.get("url") or "http://localhost:3000"),
        name="waha_url",
    )
    waha_api_key = _coerce_string(
        os.getenv("WAHA_API_KEY", raw.get("waha_api_key") or waha_config.get("api_key") or ""),
        name="waha_api_key",
    )
    waha_session = _coerce_string(
        os.getenv("WAHA_SESSION", raw.get("waha_session") or waha_config.get("session") or "default"),
        name="waha_session",
    )
    webhook_url = _coerce_string(
        os.getenv("WHATSAPP_HOOK_URL", raw.get("webhook_url") or "http://localhost:8000/webhook"),
        name="webhook_url",
    )
    include_from_me_value: Any = os.getenv("INCLUDE_FROM_ME")
    if include_from_me_value is None or not include_from_me_value.strip():
        include_from_me_value = raw.get("include_from_me", False)
    include_from_me = _coerce_bool(include_from_me_value, name="include_from_me")
    filters = FilterSettings(
        keywords=tuple(_coerce_list(filters_cfg.get("keywords"), name="filters.keywords")),
        senders=tuple(_coerce_list(filters_cfg.get("senders"), name="filters.senders")),
    )

    if not source_chat_id:
        raise ValueError("Missing source_chat_id in config.yaml or SOURCE_CHAT_ID environment variable")
    if not target_chat_id:
        raise ValueError("Missing target_chat_id in config.yaml or TARGET_CHAT_ID environment variable")
    if include_from_me and source_chat_id == target_chat_id:
        raise ValueError("SOURCE_CHAT_ID and TARGET_CHAT_ID must differ when INCLUDE_FROM_ME is enabled")

    return AppConfig(
        transport=transport,
        source_chat_id=source_chat_id,
        target_chat_id=target_chat_id,
        listen_port=listen_port,
        log_level=log_level,
        waha_url=waha_url,
        waha_api_key=waha_api_key,
        waha_session=waha_session,
        webhook_url=webhook_url,
        include_from_me=include_from_me,
        filters=filters,
        max_attachment_size=max_attachment_size,
    )
