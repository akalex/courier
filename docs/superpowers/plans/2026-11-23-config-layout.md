# Courier Configuration Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every Courier container is configured by exactly one YAML file (plus `WAHA_API_KEY` from WAHA's `.env`), validated strictly by Pydantic v2 models selected by `role`, with tracked examples, an editor schema, and docs that cannot drift.

**Architecture:** `courier/config/model.py` defines Pydantic models (`ForwarderConfig | HealthMonitorConfig`, discriminated on `role`, `extra="forbid"` everywhere). `courier/config/loader.py` reads the file, runs a migration pass (retired env vars and keys, with hints), validates with Pydantic, and turns every problem into one `ConfigError`. `main.py` and the adapter builders consume the typed models; Compose passes only `WAHA_API_KEY`.

**Tech Stack:** Python 3.14, Pydantic v2, PyYAML, httpx, pytest (asyncio_mode=auto), ruff, mypy, uv, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-23-config-layout-design.md` — read it first; this plan argues from it.

## Global Constraints

- Python 3.14. New runtime dependency exactly `pydantic>=2.14,<3`; no `pydantic-settings`. PyYAML still parses files.
- Courier reads exactly two environment variables: `WAHA_API_KEY` and `CONFIG_PATH` (default `config.yaml`).
- Every config problem is reported in one `ConfigError` (a `ValueError`); every problem line starts with the config path Courier read and names the YAML key path as the operator writes it.
- `uv.lock` keeps `revision = 5` (the local uv 0.11.28 rewrites it to 3 — restore it by hand after `uv lock`).
- ruff line length 120, max complexity 10; `ruff format`; mypy clean for both `.venv/bin/mypy courier tests` and `.venv/bin/mypy --no-error-summary --hide-error-codes --follow-imports=skip courier`.
- Release version `0.9.0`.
- Work directly on `main` (maintainer's preference); commit after each task; do not push.
- Run tests with `.venv/bin/python -m pytest -q -p no:cacheprovider` (`uv run` may try the network). Network is only needed once, for `uv lock` / `uv sync` in Task 1.
- Commit messages: plain imperative subject (repo style), ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Numbers where strings are expected** — `waha: {session: 2024}` or an unquoted numeric chat ID loads as the string (`"2024"`), as today, not a type error. Test in Task 1.
2. **Empty values** — `filters:` or `keywords:` left blank (YAML `null`) means "use the default"; an empty file reports that `role` is required instead of crashing. Tests in Tasks 1 and 2.
3. **Value case** — `destination_transport: Matrix`, `media_types: [Video]`, `log_level: debug` keep working case-insensitively, as today. Test in Task 1.
4. **Half-migrated file** — a file with both `health_monitor_only: true` and `role: health-monitor` reports the retired key once, with its hint, and nothing else about it. Test in Task 2.
5. **Single string for a list** — `media_types: video` and `keywords: "a, b"` keep working as comma-separated lists. Test in Task 1.

---

### Task 1: Config models and the Pydantic dependency

**Files:**
- Create: `courier/config/model.py`
- Modify: `pyproject.toml` (dependencies), `uv.lock`, `.pre-commit-config.yaml` (mypy hook `additional_dependencies`)
- Test: `tests/test_config_model.py` (new)

**Interfaces:**
- Produces (all in `courier/config/model.py`):
  - `class WahaSettings(BaseModel)`: `url: str = "http://localhost:3000"`, `session: str = "default"`
  - `class MatrixSettings(BaseModel)`: `homeserver_url: str`, `access_token: str` (both required, non-empty)
  - `class FilterSettings(BaseModel)`: `keywords`, `exclude_keywords`, `senders`: `tuple[str, ...] = ()`
  - `class ForwarderConfig(BaseModel)`: `role: Literal["forwarder"]`, `webhook_url: str`, `listen_port: int = 8000`, `log_level: str = "INFO"`, `waha: WahaSettings = WahaSettings()`, `destination_transport: Literal["waha", "matrix"] = "waha"`, `matrix: MatrixSettings | None = None`, `source_transport: Literal["waha"] = "waha"`, `source_chat_id: str`, `target_chat_id: str`, `include_from_me: bool = False`, `forward_content: tuple[Literal["text", "media"], ...] = ("text", "media")`, `media_types: tuple[Literal["image", "video", "audio", "file"], ...] = ("image", "video", "audio", "file")`, `max_attachment_size: int = 20971520`, `filters: FilterSettings = FilterSettings()`, `health_alerts: bool = False`, `alert_chat_id: str | None = None`
  - `class HealthMonitorConfig(BaseModel)`: `role: Literal["health-monitor"]`, the shared keys (`webhook_url` … `matrix`), and `alert_chat_id: str` (required)
  - `CourierConfig = Annotated[ForwarderConfig | HealthMonitorConfig, Field(discriminator="role")]`
  - `COURIER_CONFIG: TypeAdapter[CourierConfig]`
- Every model: `ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True, coerce_numbers_to_str=True)`; every field has a `description` (used by the editor schema in Task 4). Shared keys live in a common base class.

- [ ] **Step 1: Add the dependency**

Add `"pydantic>=2.14,<3"` to `[project].dependencies` in `pyproject.toml`; add `pydantic>=2.14,<3` to the mypy hook's `additional_dependencies` in `.pre-commit-config.yaml`. Run `uv lock && uv sync` (needs network), then set line 2 of `uv.lock` back to `revision = 5`.
Run: `uv lock --check --offline` → `Resolved … packages`, exit 0. `git diff uv.lock` shows only pydantic-related additions and no `revision` change.

- [ ] **Step 2: Write the failing tests** in `tests/test_config_model.py`, all via `COURIER_CONFIG.validate_python(raw)`:

```python
FORWARDER = {"role": "forwarder", "webhook_url": "http://courier-tfl:8000/webhook",
             "source_chat_id": "src@g.us", "target_chat_id": "dst@g.us"}
HEALTH = {"role": "health-monitor", "webhook_url": "http://courier-health:8000/webhook",
          "alert_chat_id": "15551234567@c.us"}

def test_minimal_forwarder_gets_documented_defaults():
    config = COURIER_CONFIG.validate_python(FORWARDER)
    assert isinstance(config, ForwarderConfig)
    assert (config.listen_port, config.log_level, config.waha.url, config.waha.session) == (
        8000, "INFO", "http://localhost:3000", "default")
    assert (config.destination_transport, config.include_from_me, config.health_alerts) == ("waha", False, False)
    assert config.forward_content == ("text", "media")
    assert config.media_types == ("image", "video", "audio", "file")
    assert config.max_attachment_size == 20971520
    assert config.alert_chat_id is None and config.matrix is None

def test_health_monitor_carries_no_forwarding_settings():
    config = COURIER_CONFIG.validate_python(HEALTH)
    assert isinstance(config, HealthMonitorConfig)
    assert not hasattr(config, "source_chat_id") and not hasattr(config, "filters")

@pytest.mark.parametrize("raw", [{**FORWARDER, "colour": "red"}, {**FORWARDER, "filters": {"keyword": ["a"]}},
                                 {**FORWARDER, "waha": {"api_key": "x"}}, {**HEALTH, "source_chat_id": "src@g.us"}])
def test_unknown_or_foreign_keys_are_rejected(raw):
    with pytest.raises(ValidationError) as exc:
        COURIER_CONFIG.validate_python(raw)
    assert {error["type"] for error in exc.value.errors()} == {"extra_forbidden"}

def test_alert_chat_id_follows_health_alerts():
    assert COURIER_CONFIG.validate_python({**FORWARDER, "health_alerts": True, "alert_chat_id": "a@c.us"}).alert_chat_id == "a@c.us"
    for raw in ({**FORWARDER, "health_alerts": True}, {**FORWARDER, "alert_chat_id": "a@c.us"},
                {k: v for k, v in HEALTH.items() if k != "alert_chat_id"}):
        with pytest.raises(ValidationError):
            COURIER_CONFIG.validate_python(raw)

def test_matrix_settings_follow_destination_transport(): ...
    # matrix destination without `matrix` -> error; `matrix` with waha destination -> error;
    # {"destination_transport": "matrix", "matrix": {"homeserver_url": "https://m.org", "access_token": "t"}} loads

def test_include_from_me_requires_distinct_waha_chats(): ...
    # include_from_me True + equal chats + waha->waha -> error; same with destination matrix -> loads

@pytest.mark.parametrize(("key", "value"), [("listen_port", 0), ("max_attachment_size", -1), ("log_level", "LOUD"),
                                            ("forward_content", []), ("media_types", ["gif"]),
                                            ("destination_transport", "telegram"), ("webhook_url", "")])
def test_invalid_values_are_rejected(key, value): ...

def test_numeric_scalars_are_read_as_strings():            # Review Focus 1
    config = COURIER_CONFIG.validate_python({**HEALTH, "alert_chat_id": 15551234567, "waha": {"session": 2024}})
    assert (config.alert_chat_id, config.waha.session) == ("15551234567", "2024")

def test_empty_sections_mean_defaults():                   # Review Focus 2
    config = COURIER_CONFIG.validate_python({**FORWARDER, "waha": None, "filters": {"keywords": None}})
    assert config.waha.url == "http://localhost:3000" and config.filters.keywords == ()

def test_enum_values_are_case_insensitive():               # Review Focus 3
    config = COURIER_CONFIG.validate_python({**FORWARDER, "media_types": ["Video"], "log_level": "debug",
                                             "forward_content": ["MEDIA"]})
    assert (config.media_types, config.log_level, config.forward_content) == (("video",), "DEBUG", ("media",))

def test_list_keys_accept_a_comma_separated_string():      # Review Focus 5
    config = COURIER_CONFIG.validate_python({**FORWARDER, "media_types": "video, video",
                                             "filters": {"keywords": "a, b", "senders": "+1555"}})
    assert (config.media_types, config.filters.keywords, config.filters.senders) == (("video",), ("a", "b"), ("+1555",))
```

Fill the three `...` tests with the assertions in their comments. As today, `forward_content` / `media_types` values are de-duplicated in order (the `"video, video"` case above).

- [ ] **Step 3: Run to verify they fail**
Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_config_model.py` → collection error `ModuleNotFoundError: courier.config.model`.

- [ ] **Step 4: Implement `courier/config/model.py`** with the Interfaces above. Use `BeforeValidator`s for: `None` → default for nested sections and list keys; comma-split + strip for list keys; lower-case for enum values; upper-case + `logging.getLevelNamesMapping()` check for `log_level`. Use `PositiveInt` for `listen_port` and `max_attachment_size`, non-empty strings (`min_length=1`) for chat IDs, URLs, and Matrix settings. Cross-field rules are `@model_validator(mode="after")` raising `ValueError` with a sentence naming the keys involved (for example `"alert_chat_id is required when health_alerts is true"`). Pydantic runs these only once field-level validation passes; that is acceptable.

- [ ] **Step 5: Run to verify they pass**
Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_config_model.py` → all pass; full suite still passes (nothing imports the new module yet).

- [ ] **Step 6: Commit** — `git add pyproject.toml uv.lock .pre-commit-config.yaml courier/config/model.py tests/test_config_model.py` with subject `Add Pydantic models for role-based Courier config`.

### Task 2: Loader, migration hints, and switching Courier over

**Files:**
- Create: `courier/config/loader.py`, `courier/config/migration.py`
- Replace: `courier/config/__init__.py` (re-exports only; the old hand-written loader is deleted)
- Modify: `courier/main.py` (`build_filter_policy`, `build_route`, `_log_filters`, `main`), `courier/adapters/waha.py` (`build_waha`), `courier/adapters/matrix.py` (`build_matrix_destination`)
- Test: rewrite `tests/test_config.py`; update `tests/test_main.py`, `tests/test_filters.py` (two config-based tests), `tests/test_waha_adapter.py` (`test_build_waha_identifies_courier_and_authenticates_with_api_key`)

**Interfaces:**
- Consumes: Task 1's models and `COURIER_CONFIG`.
- Produces:
  - `class ConfigError(ValueError)` with `problems: tuple[str, ...]`; `str(error)` is the problems joined by newlines.
  - `@dataclass(frozen=True, slots=True) class LoadedConfig`: `path: Path`, `config: CourierConfig`, `waha_api_key: str`.
  - `load_config(path: str | os.PathLike[str] | None = None, *, environ: Mapping[str, str] | None = None) -> LoadedConfig` — `environ` defaults to `os.environ`; `CONFIG_PATH` (empty = unset) picks the file when `path` is `None`.
  - `courier.config` re-exports: `ConfigError`, `LoadedConfig`, `load_config`, `CourierConfig`, `ForwarderConfig`, `HealthMonitorConfig`, `WahaSettings`, `MatrixSettings`, `FilterSettings`.
  - `build_filter_policy(filters: FilterSettings) -> Filter`
  - `build_route(config: CourierConfig, waha: WAHA) -> Route` — forwarder iff `isinstance(config, ForwarderConfig)`; monitor iff the config is a `HealthMonitorConfig` or a forwarder with `health_alerts`; ingress `include_from_me` is `False` for a health monitor.
  - `build_waha(settings: WahaSettings, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> WAHA`
  - `build_matrix_destination(settings: MatrixSettings) -> MatrixDestination`
  - `migration.py`: `RETIRED_ENV_VARS: dict[str, str]` (variable → YAML key, all 16 from the spec), `RETIRED_KEYS: dict[tuple[str, ...], str]` (key path → hint, all rows of the spec's table), `migration_problems(raw: dict[str, Any], environ: Mapping[str, str], path: Path) -> tuple[list[str], dict[str, Any]]` returning problem lines and `raw` with the retired keys removed.

- [ ] **Step 1: Write the failing tests** in `tests/test_config.py` (each writes YAML to `tmp_path` and passes `environ={...}`):

```python
def test_loads_forwarder_and_takes_waha_api_key_from_environment(tmp_path):
    loaded = load_config(write(tmp_path, FORWARDER_YAML), environ={"WAHA_API_KEY": "secret"})
    assert isinstance(loaded.config, ForwarderConfig) and loaded.waha_api_key == "secret"

def test_waha_api_key_defaults_to_empty(tmp_path): ...            # environ={} -> ""
def test_config_path_comes_from_config_path_variable(tmp_path): ...  # load_config(environ={"CONFIG_PATH": str(p)})

def test_every_problem_is_reported_at_once(tmp_path):
    path = write(tmp_path, "role: forwarder\nwebhook_url: http://c:8000/webhook\ntarget_chat_id: dst@g.us\ncolour: red\n")
    with pytest.raises(ConfigError) as exc:
        load_config(path, environ={"LOG_LEVEL": "DEBUG"})
    assert len(exc.value.problems) == 3
    assert all(line.startswith(f"{path}: ") for line in exc.value.problems)
    assert any("unknown key `colour`" in line for line in exc.value.problems)
    assert any("`source_chat_id`" in line and "required" in line for line in exc.value.problems)
    assert any("LOG_LEVEL" in line and "`log_level`" in line for line in exc.value.problems)

def test_nested_unknown_key_is_named_by_its_path(tmp_path): ...     # filters: {keyword: [a]} -> "unknown key `filters.keyword`"
def test_key_of_the_other_role_does_not_apply(tmp_path): ...        # health monitor + source_chat_id -> "`source_chat_id` does not apply to role `health-monitor`"
def test_missing_role_is_explained(tmp_path): ...                   # -> "`role` is required: `forwarder` or `health-monitor`"
def test_empty_file_reports_missing_role(tmp_path): ...             # Review Focus 2: "" -> exactly the role problem
@pytest.mark.parametrize("text", ["[1, 2]", "role: [unclosed"])
def test_file_that_is_not_a_yaml_mapping_is_one_problem(tmp_path, text): ...
def test_missing_file_is_one_problem(tmp_path): ...

@pytest.mark.parametrize(("yaml_key", "hint"), [("transport: waha", "destination_transport"),
    ("health_monitor_only: true", "role"), ("health_alerts_enabled: true", "role"), ("waha_url: x", "waha.url"),
    ("waha_session: x", "waha.session"), ("waha_api_key: x", "WAHA_API_KEY"), ("matrix_homeserver_url: x", "matrix.homeserver_url"),
    ("matrix_access_token: x", "matrix.access_token")])
def test_retired_keys_point_to_their_replacement(tmp_path, yaml_key, hint): ...   # exactly one line mentions the key; it contains hint
def test_waha_api_key_under_waha_points_to_the_environment(tmp_path): ...         # waha: {api_key: x} -> hint mentions WAHA_API_KEY

@pytest.mark.parametrize(("variable", "yaml_key"), list(RETIRED_ENV_VARS.items()))
def test_retired_environment_variables_are_rejected(tmp_path, variable, yaml_key): ...  # line names variable, yaml_key, and path
def test_blank_retired_environment_variable_is_ignored(tmp_path): ...             # {"LOG_LEVEL": ""} -> loads

def test_half_migrated_file_reports_only_the_retired_key(tmp_path):              # Review Focus 4
    path = write(tmp_path, HEALTH_YAML + "health_monitor_only: true\n")
    with pytest.raises(ConfigError) as exc:
        load_config(path, environ={})
    assert len(exc.value.problems) == 1 and "health_monitor_only" in exc.value.problems[0]
```

Update the other tests to the new interfaces: `tests/test_main.py` writes role-based YAML (forwarder with/without `health_alerts`, health monitor, Matrix destination) and calls `build_route(loaded.config, build_waha(loaded.config.waha, loaded.waha_api_key, transport=...))` — keep its four existing assertions (Matrix destination type; forwarder without alerts subscribes to `("message", "message.any")` and has no monitor; health monitor subscribes to `("session.status",)`; a `FAILED` status sends `[Courier Bot] WAHA session default is FAILED.` to `alerts@g.us`). `tests/test_filters.py` passes `load_config(path, environ={}).config.filters` to `build_filter_policy`. `tests/test_waha_adapter.py` builds with `build_waha(WahaSettings(url="http://waha:3000", session="demo"), "secret", transport=...)` and keeps its assertions.

- [ ] **Step 2: Run to verify they fail**
Run: `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_config.py tests/test_main.py` → `ImportError` for `ConfigError` / `LoadedConfig`.

- [ ] **Step 3: Implement `migration.py`, `loader.py`, and the new `__init__.py`.** Loader order: resolve path → read and `yaml.safe_load` (`None` → `{}`; non-mapping or YAML error → one problem, stop) → `migration_problems` → `COURIER_CONFIG.validate_python(cleaned)` → map each Pydantic error to a line: strip the union tag from `loc`, join the rest with `.`; `extra_forbidden` → "does not apply to role `<role>`" when the key is a field of the other role's model, else "unknown key `<path>`"; `union_tag_not_found` / `union_tag_invalid` → the role sentence; `missing` → "`<path>` is required"; model-validator errors → their message without Pydantic's `Value error, ` prefix; anything else → "`<path>`: <msg>". Raise one `ConfigError` with migration problems first, then schema problems.

- [ ] **Step 4: Switch consumers** to the Interfaces above in `main.py`, `adapters/waha.py`, `adapters/matrix.py`. `main()` uses `loaded = load_config()`; `_log_filters` runs only for a forwarder; the startup log line reports `role` instead of `health_monitor_only`. No module outside `courier/config/` reads `os.environ`.

- [ ] **Step 5: Run to verify everything passes**
Run: `.venv/bin/python -m pytest -q -p no:cacheprovider` → all pass. `.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy courier tests` → clean. `git grep -n "os.environ\|os.getenv" courier | grep -v "^courier/config/"` → no output.

- [ ] **Step 6: Commit** — subject `Load Courier config from one YAML file with strict validation`.

### Task 3: Compose and `.env.example`

**Files:**
- Modify: `docker-compose.yml`, `.env.example`
- Test: rewrite `tests/test_compose_config.py`

**Interfaces:**
- Consumes: nothing from code; the deployment contract from the spec ("Compose and `.env.example`").

- [ ] **Step 1: Write the failing tests** (keep `test_waha_does_not_print_qr_codes_to_logs` and `test_waha_defaults_to_noweb_arm` unchanged):

```python
COURIER_SERVICES = {"courier-aircraft-losses": "./config.yaml", "courier-tfl": "./config-tfl.yaml",
                    "courier-health": "./config-health.yaml"}

def test_courier_services_receive_only_the_waha_api_key():
    compose = load_compose()
    assert compose["x-courier-common"]["environment"] == {"WAHA_API_KEY": "${WAHA_API_KEY:?Set WAHA_API_KEY in .env}"}
    for name in COURIER_SERVICES:
        assert "environment" not in compose["services"][name] or \
            compose["services"][name]["environment"] == compose["x-courier-common"]["environment"]

def test_each_courier_service_mounts_its_own_config():
    services = load_compose()["services"]
    for name, host_file in COURIER_SERVICES.items():
        assert services[name]["volumes"] == [f"{host_file}:/app/config.yaml:ro"]

def test_env_example_holds_only_waha_settings_and_courier_tag():
    keys = {line.split("=", 1)[0] for line in Path(".env.example").read_text().splitlines()
            if line and not line.startswith("#")}
    assert keys == {"WAHA_API_KEY", "WAHA_DASHBOARD_USERNAME", "WAHA_DASHBOARD_PASSWORD", "WHATSAPP_SWAGGER_USERNAME",
                    "WHATSAPP_SWAGGER_PASSWORD", "WAHA_IMAGE", "WHATSAPP_DEFAULT_ENGINE", "COURIER_TAG"}
```

(The `environment` clause accepts the YAML-merge form `<<: *courier-environment` if the builder keeps an anchor; otherwise services have no `environment` key.)

- [ ] **Step 2: Run to verify they fail** — `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_compose_config.py` → 3 failures.
- [ ] **Step 3: Edit `docker-compose.yml` and `.env.example`** per the spec. Keep `COURIER_TAG` with a comment that it pins the Courier image.
- [ ] **Step 4: Run** the compose tests and the full suite → pass; `docker compose config --quiet` if Docker is available (warnings about unset variables are fine).
- [ ] **Step 5: Commit** — subject `Pass only WAHA_API_KEY from .env to Courier containers`.

### Task 4: Editor schema, tracked examples, and docs tests

**Files:**
- Create: `courier/config/schema.py`, `courier-config.schema.json`, `examples/config-forwarder.yaml`, `examples/config-health-monitor.yaml`, `examples/compose/config.yaml`, `examples/compose/config-tfl.yaml`, `examples/compose/config-health.yaml`
- Test: `tests/test_docs.py` (new)

**Interfaces:**
- Consumes: `COURIER_CONFIG` (Task 1), `load_config` (Task 2).
- Produces: `config_json_schema() -> dict[str, Any]` (= `COURIER_CONFIG.json_schema()`); `main(argv: Sequence[str] | None = None) -> int` writing `json.dumps(schema, indent=2) + "\n"` to `argv[0]` or `courier-config.schema.json`; `if __name__ == "__main__": raise SystemExit(main())`.

- [ ] **Step 1: Write the failing tests** in `tests/test_docs.py`:

```python
EXAMPLES = sorted(Path("examples").rglob("*.yaml"))

def test_examples_exist_for_both_roles_and_every_compose_service():
    names = {str(path) for path in EXAMPLES}
    assert {"examples/config-forwarder.yaml", "examples/config-health-monitor.yaml",
            "examples/compose/config.yaml", "examples/compose/config-tfl.yaml",
            "examples/compose/config-health.yaml"} <= names

@pytest.mark.parametrize("path", EXAMPLES, ids=str)
def test_every_example_config_loads(path):
    load_config(path, environ={})

def test_compose_example_configs_belong_to_their_services():
    services = yaml.safe_load(Path("docker-compose.yml").read_text())["services"]
    for name, service in services.items():
        if name == "waha":
            continue
        host_file = service["volumes"][0].split(":", 1)[0].removeprefix("./")
        config = load_config(Path("examples/compose") / host_file, environ={}).config
        assert urlsplit(config.webhook_url).hostname == name
        assert config.waha.url == "http://waha:3000"
    assert load_config(Path("examples/compose/config-health.yaml"), environ={}).config.role == "health-monitor"
    assert load_config(Path("examples/compose/config-tfl.yaml"), environ={}).config.media_types == ("video",)

def test_committed_schema_matches_the_config_model():
    assert json.loads(Path("courier-config.schema.json").read_text()) == config_json_schema()

def test_every_schema_property_is_documented():
    undocumented = [name for name, prop in iter_properties(config_json_schema()) if "description" not in prop]
    assert undocumented == []
```

`iter_properties` is a test helper that walks `properties` of the root and every `$defs` entry; a property that is only a `$ref` counts as documented when it has its own `description` alongside the `$ref`.

- [ ] **Step 2: Run to verify they fail** — missing `examples/`, schema module, and schema file.
- [ ] **Step 3: Implement `schema.py`; write the five example files.** Role examples list every optional key with its default (commented out where unset) and a one-line comment per key; compose examples reproduce the current deployment (aircraft-losses: keyword filter `urgent`, `alert`; tfl: `forward_content: [media]`, `media_types: [video]`; health: `alert_chat_id`), each with `waha.url: http://waha:3000` and its own `webhook_url`. Every example's first line is `# yaml-language-server: $schema=<relative path>/courier-config.schema.json`. Generate the schema with `.venv/bin/python -m courier.config.schema`.
- [ ] **Step 4: Run** `tests/test_docs.py` and the full suite → pass.
- [ ] **Step 5: Commit** — subject `Add config examples and an editor schema generated from the model`.

### Task 5: README, CHANGELOG, and version 0.9.0

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `pyproject.toml` (version), `uv.lock` (courier entry only)
- Test: `tests/test_docs.py` (add one test)

**Interfaces:**
- Consumes: `load_config` (Task 2); the example files (Task 4).

- [ ] **Step 1: Write the failing test**

```python
def complete_readme_configs() -> list[str]:
    blocks = re.findall(r"```yaml\n(.*?)```", Path("README.md").read_text(), flags=re.S)
    return [block for block in blocks if re.search(r"^\s*role:", block, flags=re.M)]

def test_every_complete_readme_config_loads(tmp_path):
    blocks = complete_readme_configs()
    assert len(blocks) >= 5   # two routing examples + three deployment configs
    for index, block in enumerate(blocks):
        path = tmp_path / f"readme-{index}.yaml"
        path.write_text(textwrap.dedent(block))
        load_config(path, environ={})
```

- [ ] **Step 2: Run to verify it fails** — `assert 0 >= 5`.
- [ ] **Step 3: Rewrite the README** per the spec's Documentation section: "How it works" step 3 and the health paragraph (no `ALERT_CHAT_ID` sentence); "Routing examples" as complete configs with `role` and `webhook_url`, no `waha.api_key`; "Configuration reference" (where-a-setting-goes rule, one YAML key table with role/required/default, the two environment variables, editor support with the tag-pinned schema URL `https://raw.githubusercontent.com/akalex/courier/0.9.0/courier-config.schema.json`, Compose paragraph without env overrides); "Run locally" and "Docker" (YAML plus `WAHA_API_KEY` only); "VM deployment" steps 4–6 (`.env` = WAHA + `COURIER_TAG`, the three YAML files shown in full and matching `examples/compose/`, `chmod 600` for YAML with a Matrix token, Compose snippet matching `docker-compose.yml`) and the chat-ID lookup step (edit the YAML files); "Operate and troubleshoot" (recreate after YAML changes; errors name `/app/config.yaml` and `docker compose logs` shows which service); new "Migrating from 0.8.x" (old → new table, VM steps, the stale `courier-lupynis` webhook note); "Development" (existing commands unchanged, plus `uv run python -m courier.config.schema` to regenerate the schema after changing the models); "Project structure" (`config/model.py`, `config/loader.py`, `config/migration.py`, `config/schema.py`, `examples/`). Partial snippets must not contain `role:`.
- [ ] **Step 4: CHANGELOG and version.** Add `## [0.9.0] - <release date>` with **Changed** entries: breaking config layout (one YAML per container, `role`, `health_alerts`, `webhook_url` required, `alert_chat_id` never falls back), retired env vars and keys rejected with hints, all problems reported at once, `.env` is WAHA-only, new Pydantic dependency, editor schema and examples. Set `version = "0.9.0"` in `pyproject.toml` and the courier entry in `uv.lock` (edit the line; keep `revision = 5`). Run `uv lock --check --offline` → exit 0.
- [ ] **Step 5: Run everything** — full suite, ruff check, ruff format check, both mypy commands → clean.
- [ ] **Step 6: Commit** — subject `Document the new configuration layout and prepare release 0.9.0`.
