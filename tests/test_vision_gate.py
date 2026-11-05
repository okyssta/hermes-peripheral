"""The vision gate: which config keys may put a screen in front of the model.

``screen_show``/``screen_capture`` attach the stored image as a ``_multimodal``
envelope, which a model that cannot see will reject at the provider. The gate is
therefore deliberately narrow and reads only documented ``config.yaml`` keys — no
private Hermes core import (catalog rule 9), so a Hermes release cannot break it.

The escape hatch is the tool schema itself: ``image: true`` on one call, which a
vision model can decide for itself. These tests cover the config paths.
"""

from __future__ import annotations

import sys
import types

from peripheral import tools


def _config(monkeypatch, config: dict) -> None:
    module = types.ModuleType("hermes_cli.config")
    setattr(module, "load_config_readonly", lambda: config)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", module)


def test_explicit_model_flag_true(monkeypatch):
    _config(monkeypatch, {"model": {"default": "gpt-4o", "supports_vision": True}})
    assert tools.vision_attached() is True


def test_explicit_model_flag_false_vetoes(monkeypatch):
    _config(monkeypatch, {"model": {"default": "gpt-4o", "supports_vision": False}})
    assert tools.vision_attached() is False


def test_vision_alias_is_read_too(monkeypatch):
    _config(monkeypatch, {"model": {"default": "gpt-4o", "vision": True}})
    assert tools.vision_attached() is True


def test_image_input_mode_text_vetoes(monkeypatch):
    _config(
        monkeypatch,
        {"model": {"default": "gpt-4o"}, "agent": {"image_input_mode": "text"}},
    )
    assert tools.vision_attached() is False


def test_image_input_mode_native_opts_in(monkeypatch):
    _config(
        monkeypatch,
        {"model": {"default": "some-custom-model"}, "agent": {"image_input_mode": "native"}},
    )
    assert tools.vision_attached() is True


def test_per_model_provider_override(monkeypatch):
    _config(
        monkeypatch,
        {
            "model": {"default": "gpt-4o", "provider": "openai"},
            "providers": {"openai": {"models": {"gpt-4o": {"supports_vision": True}}}},
        },
    )
    assert tools.vision_attached() is True


def test_per_model_override_can_veto(monkeypatch):
    _config(
        monkeypatch,
        {
            "model": {"default": "gpt-4o", "provider": "openai"},
            "providers": {"openai": {"models": {"gpt-4o": {"vision": False}}}},
        },
    )
    assert tools.vision_attached() is False


def test_catalog_id_without_a_provider_resolves_to_its_provider_block(monkeypatch):
    """``model.default: anthropic/claude-opus-4.6`` with no provider key is the
    stock shape of a default config.yaml; the override lives under ``providers.anthropic``."""

    _config(
        monkeypatch,
        {
            "model": {"default": "anthropic/claude-opus-4.6"},
            "providers": {
                "anthropic": {"models": {"claude-opus-4.6": {"supports_vision": True}}}
            },
        },
    )
    assert tools.vision_attached() is True


def test_nothing_declared_is_text_only(monkeypatch):
    _config(monkeypatch, {"model": {"default": "gpt-4o", "provider": "openai"}})
    assert tools.vision_attached() is False


def test_no_model_block_is_text_only(monkeypatch):
    _config(monkeypatch, {})
    assert tools.vision_attached() is False


def test_unreadable_config_is_text_only(monkeypatch):
    module = types.ModuleType("hermes_cli.config")

    def boom():
        raise OSError("config.yaml unreadable")

    setattr(module, "load_config_readonly", boom)
    monkeypatch.setitem(sys.modules, "hermes_cli.config", module)
    assert tools.vision_attached() is False


def test_non_bool_capability_values_are_ignored(monkeypatch):
    """``bool("false")`` is True, so a quoted string must not sneak past the gate."""
    _config(monkeypatch, {"model": {"default": "gpt-4o", "supports_vision": "false"}})
    assert tools.vision_attached() is False
