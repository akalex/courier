import pytest

from courier.config import ConfigError, ForwarderConfig, HealthMonitorConfig, load_config
from courier.config.migration import RETIRED_ENV_VARS


FORWARDER_YAML = """\
role: forwarder
webhook_url: http://courier-tfl:8000/webhook
source_chat_id: src@g.us
target_chat_id: dst@g.us
"""
HEALTH_YAML = """\
role: health-monitor
webhook_url: http://courier-health:8000/webhook
alert_chat_id: 15551234567@c.us
"""


def write(tmp_path, text, name="config.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def problems_for(path, environ=None):
    with pytest.raises(ConfigError) as exc:
        load_config(path, environ=environ or {})
    return exc.value.problems


def test_loads_forwarder_and_takes_waha_api_key_from_environment(tmp_path):
    loaded = load_config(write(tmp_path, FORWARDER_YAML), environ={"WAHA_API_KEY": "secret"})

    assert isinstance(loaded.config, ForwarderConfig)
    assert loaded.waha_api_key == "secret"


def test_waha_api_key_defaults_to_empty(tmp_path):
    loaded = load_config(write(tmp_path, HEALTH_YAML), environ={})

    assert isinstance(loaded.config, HealthMonitorConfig)
    assert loaded.waha_api_key == ""


def test_config_path_comes_from_config_path_variable(tmp_path):
    path = write(tmp_path, HEALTH_YAML, name="config-health.yaml")

    loaded = load_config(environ={"CONFIG_PATH": str(path)})

    assert loaded.path == path
    assert loaded.config.role == "health-monitor"


def test_every_problem_is_reported_at_once(tmp_path):
    path = write(
        tmp_path,
        "role: forwarder\nwebhook_url: http://c:8000/webhook\ntarget_chat_id: dst@g.us\ncolour: red\n",
    )

    problems = problems_for(path, environ={"LOG_LEVEL": "DEBUG"})

    assert len(problems) == 3
    assert all(line.startswith(f"{path}: ") for line in problems)
    assert any("unknown key `colour`" in line for line in problems)
    assert any("`source_chat_id`" in line and "required" in line for line in problems)
    assert any("LOG_LEVEL" in line and "`log_level`" in line for line in problems)


def test_nested_unknown_key_is_named_by_its_path(tmp_path):
    problems = problems_for(write(tmp_path, FORWARDER_YAML + "filters:\n  keyword: [urgent]\n"))

    assert problems == (f"{tmp_path / 'config.yaml'}: unknown key `filters.keyword`",)


def test_key_of_the_other_role_does_not_apply(tmp_path):
    problems = problems_for(write(tmp_path, HEALTH_YAML + "source_chat_id: src@g.us\n"))

    assert len(problems) == 1
    assert "`source_chat_id` does not apply to role `health-monitor`" in problems[0]


@pytest.mark.parametrize("role_line", ["", "role: forwarders\n"])
def test_missing_or_unknown_role_is_explained(tmp_path, role_line):
    problems = problems_for(write(tmp_path, role_line + "webhook_url: http://c:8000/webhook\n"))

    assert len(problems) == 1
    assert "`role` is required: `forwarder` or `health-monitor`" in problems[0]


def test_empty_file_reports_missing_role(tmp_path):
    problems = problems_for(write(tmp_path, "# nothing configured yet\n"))

    assert len(problems) == 1
    assert "`role` is required" in problems[0]


@pytest.mark.parametrize("text", ["[1, 2]\n", "role: [unclosed\n"])
def test_file_that_is_not_a_yaml_mapping_is_one_problem(tmp_path, text):
    path = write(tmp_path, text)

    problems = problems_for(path)

    assert len(problems) == 1
    assert problems[0].startswith(f"{path}: ")


def test_missing_file_is_one_problem(tmp_path):
    path = tmp_path / "missing.yaml"

    problems = problems_for(path)

    assert len(problems) == 1
    assert problems[0].startswith(f"{path}: ")
    assert "not found" in problems[0]


@pytest.mark.parametrize(
    ("yaml_line", "retired_key", "hint"),
    [
        ("transport: waha", "transport", "destination_transport"),
        ("health_monitor_only: true", "health_monitor_only", "role"),
        ("health_alerts_enabled: true", "health_alerts_enabled", "role"),
        ("waha_url: http://waha:3000", "waha_url", "waha.url"),
        ("waha_session: default", "waha_session", "waha.session"),
        ("waha_api_key: secret", "waha_api_key", "WAHA_API_KEY"),
        ("matrix_homeserver_url: https://m.org", "matrix_homeserver_url", "matrix.homeserver_url"),
        ("matrix_access_token: token", "matrix_access_token", "matrix.access_token"),
    ],
)
def test_retired_keys_point_to_their_replacement(tmp_path, yaml_line, retired_key, hint):
    problems = problems_for(write(tmp_path, FORWARDER_YAML + yaml_line + "\n"))

    assert len(problems) == 1
    assert f"`{retired_key}`" in problems[0]
    assert hint in problems[0]


def test_waha_api_key_under_waha_points_to_the_environment(tmp_path):
    problems = problems_for(write(tmp_path, FORWARDER_YAML + "waha:\n  api_key: secret\n"))

    assert len(problems) == 1
    assert "`waha.api_key`" in problems[0]
    assert "WAHA_API_KEY" in problems[0]


@pytest.mark.parametrize(("variable", "yaml_key"), list(RETIRED_ENV_VARS.items()))
def test_retired_environment_variables_are_rejected(tmp_path, variable, yaml_key):
    path = write(tmp_path, FORWARDER_YAML)

    problems = problems_for(path, environ={variable: "value"})

    assert len(problems) == 1
    assert problems[0].startswith(f"{path}: ")
    assert variable in problems[0]
    assert f"`{yaml_key}`" in problems[0]


def test_all_sixteen_retired_environment_variables_are_covered():
    assert set(RETIRED_ENV_VARS) == {
        "TRANSPORT",
        "SOURCE_TRANSPORT",
        "DESTINATION_TRANSPORT",
        "SOURCE_CHAT_ID",
        "TARGET_CHAT_ID",
        "ALERT_CHAT_ID",
        "HEALTH_ALERTS_ENABLED",
        "HEALTH_MONITOR_ONLY",
        "LISTEN_PORT",
        "LOG_LEVEL",
        "WAHA_URL",
        "WAHA_SESSION",
        "WHATSAPP_HOOK_URL",
        "MATRIX_HOMESERVER_URL",
        "MATRIX_ACCESS_TOKEN",
        "INCLUDE_FROM_ME",
    }


def test_blank_retired_environment_variable_is_ignored(tmp_path):
    loaded = load_config(write(tmp_path, FORWARDER_YAML), environ={"LOG_LEVEL": "", "INCLUDE_FROM_ME": "  "})

    assert loaded.config.log_level == "INFO"


def test_half_migrated_file_reports_only_the_retired_key(tmp_path):
    problems = problems_for(write(tmp_path, HEALTH_YAML + "health_monitor_only: true\n"))

    assert len(problems) == 1
    assert "health_monitor_only" in problems[0]


def test_cross_field_rule_is_reported_in_plain_words(tmp_path):
    problems = problems_for(write(tmp_path, FORWARDER_YAML + "health_alerts: true\n"))

    assert problems == (f"{tmp_path / 'config.yaml'}: alert_chat_id is required when health_alerts is true",)


def test_missing_role_still_reports_the_other_problems(tmp_path):
    problems = problems_for(write(tmp_path, "source_chat_id: src@g.us\ntarget_chat_id: dst@g.us\ncolour: red\n"))

    assert any("`role` is required" in line for line in problems)
    assert any("unknown key `colour`" in line for line in problems)
    assert any("`webhook_url` is required" in line and "`forwarder`" in line for line in problems)


def test_missing_role_without_role_specific_keys_still_reports_unknown_keys(tmp_path):
    problems = problems_for(write(tmp_path, "webhook_url: http://c:8000/webhook\ncolour: red\n"))

    assert len(problems) == 2
    assert any("unknown key `colour`" in line for line in problems)


def test_duplicate_keys_are_reported_instead_of_silently_overridden(tmp_path):
    text = FORWARDER_YAML + "log_level: INFO\nlog_level: DEBUG\nfilters:\n  keywords: [a]\n  keywords: [b]\n"

    problems = problems_for(write(tmp_path, text))

    assert any("`log_level` appears more than once" in line and "line 6" in line for line in problems)
    assert any("`keywords` appears more than once" in line and "line 9" in line for line in problems)


def test_yaml_syntax_error_names_the_line_without_echoing_file_contents(tmp_path):
    problems = problems_for(write(tmp_path, "role: forwarder\nmatrix:\n  access_token: syt_SECRET: oops\n"))

    assert len(problems) == 1
    assert "syt_SECRET" not in problems[0]
    assert "line 3" in problems[0]
