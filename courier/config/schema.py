"""Generate the JSON Schema that lets editors check and autocomplete Courier config files.

Regenerate after changing the config models: `uv run python -m courier.config.schema`.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .model import COURIER_CONFIG


SCHEMA_FILE = "courier-config.schema.json"


def config_json_schema() -> dict[str, Any]:
    """Return the JSON Schema of a Courier config file, built from the config models."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Courier container config",
        **COURIER_CONFIG.json_schema(),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Write the schema to the given path, or to courier-config.schema.json in the current directory."""
    path = Path(argv[0] if argv else SCHEMA_FILE)
    path.write_text(json.dumps(config_json_schema(), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
