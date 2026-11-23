import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml

from courier.config import ForwarderConfig, load_config
from courier.config.schema import config_json_schema


EXAMPLES = sorted(Path("examples").rglob("*.yaml"))


def iter_properties(schema: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for section in (schema, *schema.get("$defs", {}).values()):
        for name, prop in section.get("properties", {}).items():
            yield f"{section.get('title', 'root')}.{name}", prop


def test_examples_exist_for_both_roles_and_every_compose_service():
    names = {str(path) for path in EXAMPLES}

    assert {
        "examples/config-forwarder.yaml",
        "examples/config-health-monitor.yaml",
        "examples/compose/config.yaml",
        "examples/compose/config-tfl.yaml",
        "examples/compose/config-health.yaml",
    } <= names


@pytest.mark.parametrize("path", EXAMPLES, ids=str)
def test_every_example_config_loads(path):
    load_config(path, environ={})


def test_compose_example_configs_belong_to_their_services():
    services = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))["services"]

    for name, service in services.items():
        if name == "waha":
            continue
        host_file = service["volumes"][0].split(":", 1)[0].removeprefix("./")
        config = load_config(Path("examples/compose") / host_file, environ={}).config
        assert urlsplit(config.webhook_url).hostname == name
        assert config.waha.url == "http://waha:3000"

    assert load_config(Path("examples/compose/config-health.yaml"), environ={}).config.role == "health-monitor"
    tfl = load_config(Path("examples/compose/config-tfl.yaml"), environ={}).config
    assert isinstance(tfl, ForwarderConfig)
    assert tfl.forward_content == ("media",)
    assert tfl.media_types == ("video",)


def test_committed_schema_matches_the_config_model():
    committed = json.loads(Path("courier-config.schema.json").read_text(encoding="utf-8"))

    assert committed == config_json_schema()


def test_every_schema_property_is_documented():
    undocumented = [name for name, prop in iter_properties(config_json_schema()) if "description" not in prop]

    assert undocumented == []


def test_schema_accepts_comma_separated_strings_for_list_keys():
    filters = config_json_schema()["$defs"]["FilterSettings"]["properties"]["keywords"]

    assert {"type": "string"} in filters["anyOf"]
