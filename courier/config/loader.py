"""Read one Courier config file and report every problem in it at once."""

from __future__ import annotations

import os
from collections.abc import Hashable, Mapping, Sequence
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
UNION_TAG_ERRORS = frozenset({"union_tag_not_found", "union_tag_invalid"})


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

    raw, duplicate_problems = _read_mapping(config_path)
    migration, cleaned = migration_problems(raw, env, config_path)
    config, schema_problems = _validate(config_path, cleaned)
    problems = [*duplicate_problems, *migration, *schema_problems]
    if problems or config is None:
        raise ConfigError(problems)
    return LoadedConfig(path=config_path, config=config, waha_api_key=env.get("WAHA_API_KEY", "").strip())


class _DuplicateKeyLoader(yaml.SafeLoader):
    """A safe YAML loader that records keys appearing twice in one mapping instead of keeping the last silently."""

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self.duplicates: list[tuple[str, int]] = []

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = self.construct_object(key_node, deep=deep)
            if isinstance(key, Hashable) and key in seen:
                self.duplicates.append((str(key), key_node.start_mark.line + 1))
            elif isinstance(key, Hashable):
                seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _read_mapping(path: Path) -> tuple[dict[str, Any], list[str]]:
    """Parse the file; return its mapping and a problem line for every duplicated key."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError([f"{path}: config file not found"]) from None
    except OSError as exc:
        raise ConfigError([f"{path}: cannot read config file ({exc.strerror})"]) from None
    loader = _DuplicateKeyLoader(text)
    try:
        data = loader.get_single_data()
    except yaml.YAMLError as exc:
        raise ConfigError([f"{path}: {_yaml_error(exc)}"]) from None
    finally:
        loader.dispose()
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError([f"{path}: must be a YAML mapping of keys to values"])
    duplicates = [
        f"{path}: `{key}` appears more than once (line {line}); YAML would silently keep only the last value"
        for key, line in loader.duplicates
    ]
    return data, duplicates


def _yaml_error(exc: yaml.YAMLError) -> str:
    """Describe a YAML syntax error by position only: the offending line may hold a secret such as a token."""
    if isinstance(exc, yaml.MarkedYAMLError) and exc.problem_mark is not None:
        mark = exc.problem_mark
        return f"not valid YAML at line {mark.line + 1}, column {mark.column + 1}: {exc.problem or 'syntax error'}"
    return "not valid YAML"


def _validate(path: Path, cleaned: dict[str, Any]) -> tuple[CourierConfig | None, list[str]]:
    try:
        return COURIER_CONFIG.validate_python(cleaned), []
    except ValidationError as exc:
        errors = exc.errors()
    problems = [_describe(path, error) for error in errors]
    if all(error["type"] in UNION_TAG_ERRORS for error in errors):
        problems.extend(_problems_assuming_a_role(path, cleaned))
    return None, problems


def _problems_assuming_a_role(path: Path, cleaned: dict[str, Any]) -> list[str]:
    """Without a usable `role`, Pydantic checks nothing else; check the role the other keys point to.

    Without this, an operator would fix `role`, restart, and only then see the next problem.
    """
    role = _likely_role(cleaned)
    if role is None:
        known = {key for model in ROLE_MODELS.values() for key in model.model_fields}
        return [f"{path}: unknown key `{key}`" for key in cleaned if key not in known]
    try:
        COURIER_CONFIG.validate_python({**cleaned, "role": role})
    except ValidationError as exc:
        return [f"{_describe(path, error)} (assuming role `{role}`)" for error in exc.errors()]
    return []


def _likely_role(cleaned: dict[str, Any]) -> str | None:
    forwarder_only = set(ForwarderConfig.model_fields) - set(HealthMonitorConfig.model_fields)
    if forwarder_only & cleaned.keys():
        return "forwarder"
    if "alert_chat_id" in cleaned:
        return "health-monitor"
    return None


def _describe(path: Path, error: ErrorDetails) -> str:
    """Turn one Pydantic error into a line in the operator's terms: the YAML key path, not model internals."""
    loc = error["loc"]
    role = loc[0] if loc and loc[0] in ROLE_MODELS else None
    key_path = loc[1:] if role is not None else loc
    key = _key_path(key_path)
    kind = error["type"]
    if kind in UNION_TAG_ERRORS:
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
