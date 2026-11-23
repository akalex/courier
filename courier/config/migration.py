"""Settings that moved in 0.9.0: retired environment variables and YAML keys, each pointing to its new home."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any


RETIRED_ENV_VARS: dict[str, str] = {
    "TRANSPORT": "destination_transport",
    "SOURCE_TRANSPORT": "source_transport",
    "DESTINATION_TRANSPORT": "destination_transport",
    "SOURCE_CHAT_ID": "source_chat_id",
    "TARGET_CHAT_ID": "target_chat_id",
    "ALERT_CHAT_ID": "alert_chat_id",
    "HEALTH_ALERTS_ENABLED": "role",
    "HEALTH_MONITOR_ONLY": "role",
    "LISTEN_PORT": "listen_port",
    "LOG_LEVEL": "log_level",
    "WAHA_URL": "waha.url",
    "WAHA_SESSION": "waha.session",
    "WHATSAPP_HOOK_URL": "webhook_url",
    "MATRIX_HOMESERVER_URL": "matrix.homeserver_url",
    "MATRIX_ACCESS_TOKEN": "matrix.access_token",
    "INCLUDE_FROM_ME": "include_from_me",
}

_ROLE_HINT = "use `role: health-monitor` for a health-monitor container, or `health_alerts: true` on a forwarder"
_API_KEY_HINT = "the WAHA API key now comes only from WAHA_API_KEY in .env"

RETIRED_KEYS: dict[tuple[str, ...], str] = {
    ("transport",): "use `destination_transport` (and `source_transport` if needed)",
    ("health_alerts_enabled",): _ROLE_HINT,
    ("health_monitor_only",): _ROLE_HINT,
    ("waha_url",): "use `waha.url`",
    ("waha_session",): "use `waha.session`",
    ("waha_api_key",): _API_KEY_HINT,
    ("waha", "api_key"): _API_KEY_HINT,
    ("matrix_homeserver_url",): "use `matrix.homeserver_url`",
    ("matrix_access_token",): "use `matrix.access_token`",
}


def migration_problems(
    raw: dict[str, Any],
    environ: Mapping[str, str],
    path: Path,
) -> tuple[list[str], dict[str, Any]]:
    """Report settings still given the pre-0.9.0 way, and return `raw` without the retired keys.

    The retired keys are removed so schema validation does not report them a second time as unknown keys.
    """
    problems = [
        _environment_problem(path, name, yaml_key)
        for name, yaml_key in RETIRED_ENV_VARS.items()
        if environ.get(name, "").strip()
    ]
    cleaned = dict(raw)
    for key_path, hint in RETIRED_KEYS.items():
        if _pop_key_path(cleaned, key_path):
            problems.append(f"{path}: `{'.'.join(key_path)}` is no longer supported; {hint}")
    return problems, cleaned


def _environment_problem(path: Path, name: str, yaml_key: str) -> str:
    extra = " (or `health_alerts: true` on a forwarder)" if yaml_key == "role" else ""
    return f"{path}: environment variable {name} is no longer read; set `{yaml_key}` in this file instead{extra}"


def _pop_key_path(mapping: dict[str, Any], key_path: tuple[str, ...]) -> bool:
    """Remove a possibly nested key, copying nested mappings so the caller's data is untouched."""
    *parents, leaf = key_path
    node = mapping
    for parent in parents:
        child = node.get(parent)
        if not isinstance(child, dict):
            return False
        copy = dict(child)
        node[parent] = copy
        node = copy
    if leaf not in node:
        return False
    del node[leaf]
    return True
