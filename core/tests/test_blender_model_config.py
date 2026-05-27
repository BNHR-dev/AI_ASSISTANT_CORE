"""
H.6.1 — Tests unitaires de la configuration centralisée du modèle LLM Blender.
"""
from __future__ import annotations

import importlib
import os

import pytest

from app.engine import blender_model_config as cfg


def test_default_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cfg.BLENDER_LLM_MODEL_ENV, raising=False)
    assert cfg.get_blender_llm_model() == cfg.DEFAULT_BLENDER_LLM_MODEL


def test_default_is_qwen_coder_7b() -> None:
    # Invariant historique H.5.x : ne pas changer le défaut sans intention.
    assert cfg.DEFAULT_BLENDER_LLM_MODEL == "qwen2.5-coder:7b"


def test_env_override_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(cfg.BLENDER_LLM_MODEL_ENV, "qwen2.5:14b")
    assert cfg.get_blender_llm_model() == "qwen2.5:14b"


def test_env_empty_string_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(cfg.BLENDER_LLM_MODEL_ENV, "")
    assert cfg.get_blender_llm_model() == cfg.DEFAULT_BLENDER_LLM_MODEL


def test_env_whitespace_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(cfg.BLENDER_LLM_MODEL_ENV, "  deepseek-coder-v2  ")
    assert cfg.get_blender_llm_model() == "deepseek-coder-v2"


def test_env_only_whitespace_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(cfg.BLENDER_LLM_MODEL_ENV, "   ")
    assert cfg.get_blender_llm_model() == cfg.DEFAULT_BLENDER_LLM_MODEL
