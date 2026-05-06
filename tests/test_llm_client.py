"""Tests for LLM cost estimation helpers."""

from __future__ import annotations

import pytest

from repokeeper.exceptions import AuthError, LLMParseError
from repokeeper.llm_client import LLMClient, _estimate_cost, _pricing_env_key, parse_llm_json


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


def test_llm_client_missing_api_key(monkeypatch):
    """LLMClient raises AuthError when no API key is set."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AuthError, match="No LLM API key found"):
        LLMClient()


def test_parse_llm_json_raises_llmparse_error():
    """parse_llm_json raises LLMParseError (not ValueError) on bad input."""
    with pytest.raises(LLMParseError, match="Failed to parse LLM JSON"):
        parse_llm_json("not json at all")
