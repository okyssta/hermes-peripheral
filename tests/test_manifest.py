"""The manifest is a contract: schema keys, declared capabilities and reality agree."""

from __future__ import annotations

from pathlib import Path

import pytest

from peripheral import schemas
from peripheral.settings import SCHEMA_KEYS

PLUGIN_YAML = Path(__file__).resolve().parents[1] / "plugin.yaml"


@pytest.fixture(scope="module")
def manifest():
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(PLUGIN_YAML.read_text())


def test_every_config_key_is_declared_in_both_places(manifest):
    assert set(manifest["config_schema"]) == set(SCHEMA_KEYS)


def test_declared_tools_match_the_schemas_we_register(manifest):
    assert set(manifest["provides_tools"]) == {schema["name"] for schema in schemas.ALL}


def test_declared_hooks_match_the_hooks_we_register(manifest):
    assert set(manifest["provides_hooks"]) == {"post_tool_call"}


def test_no_python_dependencies_are_declared(manifest):
    # The plugin is stdlib + Pillow (a Hermes core dependency). Tesseract is an
    # optional external binary, handled by feature detection rather than a wheel.
    assert not manifest.get("python_dependencies")


def test_requires_hermes_is_a_floor_not_a_newer_release(manifest):
    assert manifest["requires_hermes"].startswith(">=")


def test_version_is_a_quoted_string(manifest):
    assert isinstance(manifest["version"], str)
