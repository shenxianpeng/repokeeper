"""Tests for LLM cost estimation helpers."""

from __future__ import annotations

import pytest

from repokeeper.llm_client import _estimate_cost, _pricing_env_key


def test_pricing_env_key_normalizes_model_name():
    assert _pricing_env_key("claude-sonnet-4-20250514") == "CLAUDE_SONNET_4_20250514"


def test_estimate_cost_uses_builtin_pricing(monkeypatch):
    monkeypatch.delenv("RKP_LLM_PRICE_DEEPSEEK_CHAT_INPUT", raising=False)
    monkeypatch.delenv("RKP_LLM_PRICE_DEEPSEEK_CHAT_OUTPUT", raising=False)

    assert _estimate_cost("deepseek-chat", 1_000_000, 1_000_000) == pytest.approx(0.42)


def test_estimate_cost_uses_env_override(monkeypatch):
    monkeypatch.setenv("RKP_LLM_PRICE_CUSTOM_MODEL_INPUT", "1.5")
    monkeypatch.setenv("RKP_LLM_PRICE_CUSTOM_MODEL_OUTPUT", "2.5")

    assert _estimate_cost("custom-model", 1_000_000, 2_000_000) == pytest.approx(6.5)


def test_estimate_cost_ignores_invalid_env_override(monkeypatch):
    monkeypatch.setenv("RKP_LLM_PRICE_DEEPSEEK_CHAT_INPUT", "bad")
    monkeypatch.setenv("RKP_LLM_PRICE_DEEPSEEK_CHAT_OUTPUT", "0.28")

    assert _estimate_cost("deepseek-chat", 1_000_000, 1_000_000) == pytest.approx(0.42)
