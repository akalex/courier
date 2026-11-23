"""Local config files may hold secrets (a Matrix token); they must stay out of git and out of Docker images."""

import subprocess
from fnmatch import fnmatch
from pathlib import Path

import pytest


def ignored_by_git(path: str) -> bool:
    return subprocess.run(["git", "check-ignore", "--quiet", path], check=False).returncode == 0


def excluded_from_docker_context(path: str) -> bool:
    patterns = [
        line.strip().lstrip("/")
        for line in Path(".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    return any(fnmatch(path, pattern) or fnmatch(path.split("/", 1)[0], pattern) for pattern in patterns)


@pytest.mark.parametrize("path", ["config.yaml", "config-tfl.yaml", "config-health.yaml", ".env"])
def test_local_configs_and_env_are_ignored_by_git(path):
    assert ignored_by_git(path)


def test_example_configs_are_not_ignored_by_git():
    assert not ignored_by_git("examples/compose/config.yaml")


@pytest.mark.parametrize("path", [".env", "config.yaml", "config-health.yaml", "waha-data", ".venv", ".git"])
def test_local_secrets_and_state_stay_out_of_the_docker_build_context(path):
    assert excluded_from_docker_context(path)


@pytest.mark.parametrize(
    "path",
    ["pyproject.toml", "uv.lock", "README.md", "courier/main.py", "examples/compose/config.yaml"],
)
def test_docker_build_context_keeps_what_the_image_needs(path):
    assert not excluded_from_docker_context(path)
