"""Read one Courier config file and report every problem in it at once."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError
from pydantic_core import ErrorDetails

from .migration import migration_problems
from .model import COURIER_CONFIG, CourierConfig, ForwarderConfig, HealthMonitorConfig


DEFAULT_CONFIG_PATH = "config.yaml"
ROLE_MODELS: dict[str, type[BaseModel]] = {"forwarder": ForwarderConfig, "health-monitor": HealthMonitorConfig}


class ConfigError(ValueError):
    """The config file cannot be used; `problems` lists every reason, one line each."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("\n".join(self.problems))


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    path: Path
    config: CourierConfig
    waha_api_key: str


def load_config(
    path: str | os.PathLike[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> LoadedConfig:
    """Load and validate the container's config file.

    Args:
        path: Config file to read; defaults to CONFIG_PATH, then `config.yaml`.
        environ: Environment to read CONFIG_PATH and WAHA_API_KEY from; defaults to the process environment.

    Raises:
        ConfigError: Listing every problem found, each line naming the file and the key.
    """
    env = os.environ if environ is None else environ
    config_path = Path(path if path is not None else env.get("CONFIG_PATH", "").strip() or DEFAULT_CONFIG_PATH)
    config_path = config_path.expanduser()

    problems, cleaned = migration_problems(_read_mapping(config_path), env, config_path)
    try:
        config = COURIER_CONFIG.validate_python(cleaned)
    except ValidationError as exc:
        raise ConfigError([*problems, *(_describe(config_path, error) for error in exc.errors())]) from None
    if problems:
        raise ConfigError(problems)
    return LoadedConfig(path=config_path, config=config, waha_api_key=env.get("WAHA_API_KEY", "").strip())


def _read_mapping(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError([f"{path}: config file not found"]) from None
    except OSError as exc:
        raise ConfigError([f"{path}: cannot read config file ({exc.strerror})"]) from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError([f"{path}: not valid YAML: {' '.join(str(exc).split())}"]) from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError([f"{path}: must be a YAML mapping of keys to values"])
    return data


def _describe(path: Path, error: ErrorDetails) -> str:
    """Turn one Pydantic error into a line in the operator's terms: the YAML key path, not model internals."""
    loc = error["loc"]
    role = loc[0] if loc and loc[0] in ROLE_MODELS else None
    key_path = loc[1:] if role is not None else loc
    key = _key_path(key_path)
    kind = error["type"]
    if kind in {"union_tag_not_found", "union_tag_invalid"}:
        document = error.get("input")
        given = document.get("role") if isinstance(document, dict) else None
        suffix = f" (got '{given}')" if given is not None else ""
        return f"{path}: `role` is required: `forwarder` or `health-monitor`{suffix}"
    if kind == "extra_forbidden":
        if isinstance(role, str) and len(key_path) == 1 and _belongs_to_another_role(role, str(key_path[0])):
            return f"{path}: `{key}` does not apply to role `{role}`"
        return f"{path}: unknown key `{key}`"
    if kind == "missing":
        return f"{path}: `{key}` is required"
    message = error["msg"].removeprefix("Value error, ")
    return f"{path}: {message}" if not key_path else f"{path}: `{key}`: {message}"


def _belongs_to_another_role(role: str, key: str) -> bool:
    return any(key in model.model_fields for name, model in ROLE_MODELS.items() if name != role)


def _key_path(parts: Sequence[int | str]) -> str:
    text = ""
    for part in parts:
        if isinstance(part, int):
            text += f"[{part}]"
        else:
            text += f".{part}" if text else part
    return text
