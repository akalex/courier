from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


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


def load_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Load the project configuration from YAML defaults and environment overrides.

    Args:
        path: Optional path to a YAML file. If omitted, the value of
            CONFIG_PATH is used or the default config.yaml file is loaded.

    Returns:
        A validated configuration dictionary with normalized transport and filter values.

    Raises:
        ValueError: If the configuration is missing required keys or has an invalid format.
    """
    config_path = Path(path) if path is not None else Path(os.getenv("CONFIG_PATH", "config.yaml"))
    config_path = config_path.expanduser()

    raw = _load_yaml(config_path)
    config: dict[str, Any] = dict(raw)

    config["transport"] = str(
        os.getenv("TRANSPORT", config.get("transport", "waha"))
    ).strip().lower()
    if not config["transport"]:
        raise ValueError("Config requires a transport name (for example: 'waha')")

    config["source_chat_id"] = os.getenv("SOURCE_CHAT_ID", config.get("source_chat_id"))
    config["target_chat_id"] = os.getenv("TARGET_CHAT_ID", config.get("target_chat_id"))
    config["listen_port"] = int(os.getenv("LISTEN_PORT", str(config.get("listen_port", 8000))))
    config["log_level"] = os.getenv("LOG_LEVEL", config.get("log_level", "INFO"))
    config["waha_url"] = os.getenv(
        "WAHA_URL",
        config.get("waha_url") or config.get("waha", {}).get("url") or "http://localhost:3000",
    )
    config["waha_api_key"] = os.getenv(
        "WAHA_API_KEY",
        config.get("waha_api_key") or config.get("waha", {}).get("api_key") or "",
    )
    config["waha_session"] = os.getenv(
        "WAHA_SESSION",
        config.get("waha_session") or config.get("waha", {}).get("session") or "default",
    )

    filters_cfg = config.get("filters") or {}
    if not isinstance(filters_cfg, dict):
        raise ValueError("Config field 'filters' must be a mapping")
    config["filters"] = {
        "keywords": _coerce_list(filters_cfg.get("keywords"), name="filters.keywords"),
        "senders": _coerce_list(filters_cfg.get("senders"), name="filters.senders"),
    }

    if not config["source_chat_id"]:
        raise ValueError("Missing source_chat_id in config.yaml or SOURCE_CHAT_ID environment variable")
    if not config["target_chat_id"]:
        raise ValueError("Missing target_chat_id in config.yaml or TARGET_CHAT_ID environment variable")

    return config
