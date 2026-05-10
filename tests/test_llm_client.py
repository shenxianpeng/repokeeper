"""Tests for LLM cost estimation helpers, chat providers, and JSON parsing."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from repokeeper.exceptions import AuthError, LLMParseError
from repokeeper.llm_client import (
    LLMClient,
    TokenUsage,
    _estimate_cost,
    _pricing_env_key,
    _repair_truncated_json,
    get_pricing_summary,
    parse_llm_json,
)


# ── Pricing helpers ──────────────────────────────────────────────────────────


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


def test_estimate_cost_falls_back_to_prefix_match(monkeypatch):
    """Unknown model name that matches a known prefix gets pricing from that prefix."""
    monkeypatch.delenv("RKP_LLM_PRICE_CLAUDE_OPUS_4_INPUT", raising=False)
    monkeypatch.delenv("RKP_LLM_PRICE_CLAUDE_OPUS_4_OUTPUT", raising=False)
    cost = _estimate_cost("claude-opus-4", 1_000_000, 1_000_000)
    assert cost == pytest.approx(18.0)


def test_estimate_cost_unknown_model_returns_zero():
    """Completely unknown model returns 0.0 cost."""
    assert _estimate_cost("nonexistent-model-xyz", 1000, 1000) == 0.0


# ── Pricing summary ──────────────────────────────────────────────────────────


def test_get_pricing_summary_returns_all_models():
    summary = get_pricing_summary()
    assert "last_updated" in summary
    assert "stale_days" in summary
    assert isinstance(summary["stale"], bool)
    assert "models" in summary
    assert "deepseek-chat" in summary["models"]
    assert "gpt-4o" in summary["models"]
    assert "claude-sonnet-4-20250514" in summary["models"]
    ds = summary["models"]["deepseek-chat"]
    assert "input" in ds
    assert "output" in ds
    assert "env_override" in ds


# ── Fake module helpers ──────────────────────────────────────────────────────


def _fake_openai_module():
    """Return a MagicMock that behaves like the openai package."""
    mock_openai = MagicMock()
    mock_client = MagicMock()
    mock_openai.OpenAI.return_value = mock_client
    return mock_openai, mock_client


def _fake_anthropic_module():
    """Return a MagicMock that behaves like the anthropic package."""
    mock_anthro = MagicMock()
    mock_client = MagicMock()
    mock_anthro.Anthropic.return_value = mock_client
    return mock_anthro, mock_client


# ── LLMClient construction ───────────────────────────────────────────────────


def test_llm_client_missing_api_key(monkeypatch):
    """LLMClient raises AuthError when no API key is set."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AuthError, match="No LLM API key found"):
        LLMClient()


def test_llm_client_openai_provider(monkeypatch):
    """LLMClient auto-detects OpenAI provider from DEEPSEEK_API_KEY."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    mock_openai, _mock_client = _fake_openai_module()
    with patch.dict(sys.modules, {"openai": mock_openai}):
        client = LLMClient()
        assert client.provider == "openai"


def test_llm_client_anthropic_provider(monkeypatch):
    """LLMClient auto-detects Anthropic provider from key prefix."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    mock_anthro, _mock_client = _fake_anthropic_module()
    with patch.dict(sys.modules, {"anthropic": mock_anthro}):
        client = LLMClient()
        assert client.provider == "anthropic"


def test_llm_client_anthropic_from_base_url(monkeypatch):
    """LLMClient detects Anthropic when base URL contains 'anthropic'."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.anthropic.com")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    mock_anthro, _mock_client = _fake_anthropic_module()
    with patch.dict(sys.modules, {"anthropic": mock_anthro}):
        client = LLMClient()
        assert client.provider == "anthropic"


def test_llm_client_explicit_openai_provider(monkeypatch):
    """LLMClient respects explicit provider='openai'."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-any")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    mock_openai, _mock_client = _fake_openai_module()
    with patch.dict(sys.modules, {"openai": mock_openai}):
        client = LLMClient(provider="openai")
        assert client.provider == "openai"


# ── LLMClient.chat with OpenAI (mocked) ──────────────────────────────────────


def test_chat_openai_non_streaming(monkeypatch):
    """chat() returns LLMResponse for non-streaming OpenAI call."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"answer": 42}'
    mock_response.choices[0].finish_reason = "stop"
    mock_response.usage.prompt_tokens = 100
    mock_response.usage.completion_tokens = 50
    mock_response.usage.total_tokens = 150

    mock_openai, mock_client = _fake_openai_module()
    mock_client.chat.completions.create.return_value = mock_response

    with patch.dict(sys.modules, {"openai": mock_openai}):
        client = LLMClient()
        resp = client.chat(
            system="You are helpful.",
            messages=[{"role": "user", "content": "Hello"}],
            model="deepseek-chat",
            temperature=0.1,
            max_tokens=100,
            stream=False,
        )

    assert resp.content == '{"answer": 42}'
    assert resp.usage.prompt_tokens == 100
    assert resp.usage.completion_tokens == 50
    assert resp.usage.total_tokens == 150
    assert resp.model == "deepseek-chat"
    assert resp.finish_reason == "stop"


def test_chat_openai_streaming(monkeypatch):
    """chat() returns concatenated content for streaming OpenAI call."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    chunk1 = MagicMock()
    chunk1.choices = [MagicMock()]
    chunk1.choices[0].delta.content = "Hello "
    chunk1.choices[0].finish_reason = None
    chunk1.usage = None

    chunk2 = MagicMock()
    chunk2.choices = [MagicMock()]
    chunk2.choices[0].delta.content = "World"
    chunk2.choices[0].finish_reason = None
    chunk2.usage = None

    chunk3 = MagicMock()
    chunk3.choices = [MagicMock()]
    chunk3.choices[0].delta.content = None
    chunk3.choices[0].finish_reason = "stop"
    chunk3.usage = MagicMock()
    chunk3.usage.prompt_tokens = 80
    chunk3.usage.completion_tokens = 20

    mock_openai, mock_client = _fake_openai_module()
    mock_client.chat.completions.create.return_value = [chunk1, chunk2, chunk3]

    with patch.dict(sys.modules, {"openai": mock_openai}):
        client = LLMClient()
        resp = client.chat(
            system="You are helpful.",
            messages=[{"role": "user", "content": "Say hello"}],
            model="deepseek-chat",
            stream=True,
        )

    assert resp.content == "Hello World"
    assert resp.usage.prompt_tokens == 80
    assert resp.usage.completion_tokens == 20
    assert resp.finish_reason == "stop"


def test_chat_openai_with_custom_base_url(monkeypatch):
    """chat() uses LLM_BASE_URL when provided."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_BASE_URL", "https://custom.api.example.com/v1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "OK"
    mock_response.choices[0].finish_reason = "stop"
    mock_response.usage.prompt_tokens = 5
    mock_response.usage.completion_tokens = 1
    mock_response.usage.total_tokens = 6

    mock_openai, mock_client = _fake_openai_module()
    mock_client.chat.completions.create.return_value = mock_response

    with patch.dict(sys.modules, {"openai": mock_openai}):
        client = LLMClient()
        client.chat(stream=False)

    # Verify OpenAI was constructed with the custom base_url
    mock_openai.OpenAI.assert_called_once_with(
        api_key="sk-test",
        base_url="https://custom.api.example.com/v1",
    )


# ── LLMClient.chat with Anthropic (mocked) ───────────────────────────────────


def test_chat_anthropic_non_streaming(monkeypatch):
    """chat() returns LLMResponse for non-streaming Anthropic call."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    mock_content_block = MagicMock()
    mock_content_block.text = "Response from Claude"

    mock_response = MagicMock()
    mock_response.content = [mock_content_block]
    mock_response.stop_reason = "end_turn"
    mock_response.usage.input_tokens = 120
    mock_response.usage.output_tokens = 30

    mock_anthro, mock_client = _fake_anthropic_module()
    mock_client.messages.create.return_value = mock_response

    with patch.dict(sys.modules, {"anthropic": mock_anthro}):
        client = LLMClient()
        resp = client.chat(
            system="You are Claude.",
            messages=[{"role": "user", "content": "Hello"}],
            model="claude-sonnet-4-20250514",
            temperature=0.1,
            max_tokens=200,
            stream=False,
        )

    assert resp.content == "Response from Claude"
    assert resp.usage.prompt_tokens == 120
    assert resp.usage.completion_tokens == 30
    assert resp.finish_reason == "end_turn"
    assert resp.model == "claude-sonnet-4-20250514"


def test_chat_anthropic_streaming(monkeypatch):
    """chat() returns concatenated content for streaming Anthropic call."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    mock_final = MagicMock()
    mock_final.stop_reason = "end_turn"
    mock_final.usage.input_tokens = 200
    mock_final.usage.output_tokens = 50

    mock_anthro, mock_client = _fake_anthropic_module()
    mock_stream = MagicMock()
    mock_stream.text_stream = ["Hello ", "from ", "Claude"]
    mock_stream.get_final_message.return_value = mock_final
    mock_stream.__enter__ = MagicMock(return_value=mock_stream)
    mock_stream.__exit__ = MagicMock(return_value=False)
    mock_client.messages.stream.return_value = mock_stream

    with patch.dict(sys.modules, {"anthropic": mock_anthro}):
        client = LLMClient()
        resp = client.chat(
            system="You are Claude.",
            messages=[{"role": "user", "content": "Hello"}],
            model="claude-sonnet-4-20250514",
            stream=True,
        )

    assert resp.content == "Hello from Claude"
    assert resp.usage.prompt_tokens == 200
    assert resp.usage.completion_tokens == 50
    assert resp.finish_reason == "end_turn"


def test_chat_anthropic_with_assistant_role_message(monkeypatch):
    """Anthropic chat converts assistant role properly."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    mock_content_block = MagicMock()
    mock_content_block.text = "OK"

    mock_response = MagicMock()
    mock_response.content = [mock_content_block]
    mock_response.stop_reason = "end_turn"
    mock_response.usage.input_tokens = 10
    mock_response.usage.output_tokens = 5

    mock_anthro, mock_client = _fake_anthropic_module()
    mock_client.messages.create.return_value = mock_response

    with patch.dict(sys.modules, {"anthropic": mock_anthro}):
        client = LLMClient()
        client.chat(
            messages=[
                {"role": "user", "content": "Q1"},
                {"role": "assistant", "content": "A1"},
                {"role": "user", "content": "Q2"},
            ],
            stream=False,
        )

    call_args = mock_client.messages.create.call_args
    sent_messages = call_args[1]["messages"]
    assert sent_messages[0]["role"] == "user"
    assert sent_messages[1]["role"] == "assistant"
    assert sent_messages[2]["role"] == "user"


def test_chat_anthropic_system_prompt(monkeypatch):
    """Anthropic chat passes system prompt as top-level parameter."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    mock_content_block = MagicMock()
    mock_content_block.text = "OK"

    mock_response = MagicMock()
    mock_response.content = [mock_content_block]
    mock_response.stop_reason = "end_turn"
    mock_response.usage.input_tokens = 5
    mock_response.usage.output_tokens = 2

    mock_anthro, mock_client = _fake_anthropic_module()
    mock_client.messages.create.return_value = mock_response

    with patch.dict(sys.modules, {"anthropic": mock_anthro}):
        client = LLMClient()
        client.chat(system="Be concise.", messages=[{"role": "user", "content": "Hi"}], stream=False)

    call_kwargs = mock_client.messages.create.call_args[1]
    assert call_kwargs["system"] == "Be concise."


# ── TokenUsage ────────────────────────────────────────────────────────────────


def test_token_usage_defaults():
    usage = TokenUsage()
    assert usage.prompt_tokens == 0
    assert usage.completion_tokens == 0
    assert usage.total_tokens == 0
    assert usage.cost_usd == 0.0
    assert usage.model == ""


def test_token_usage_with_values():
    usage = TokenUsage(
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cost_usd=0.0042,
        model="deepseek-chat",
    )
    assert usage.total_tokens == 150
    assert usage.cost_usd == 0.0042


# ── JSON repair ──────────────────────────────────────────────────────────────


def test_repair_truncated_json_simple_object():
    result = _repair_truncated_json('{"key": "val')
    assert result is not None
    assert result.endswith("}")


def test_repair_truncated_json_nested_array():
    result = _repair_truncated_json('{"items": [1, 2')
    assert result is not None
    assert result.endswith("]}")


def test_repair_truncated_json_unclosed_string():
    result = _repair_truncated_json('{"name": "hello')
    assert result is not None
    assert '"' in result


def test_repair_truncated_json_already_valid():
    """Already valid JSON has nothing to repair."""
    result = _repair_truncated_json('{"key": "value"}')
    assert result is None


def test_repair_truncated_json_mixed_brackets():
    result = _repair_truncated_json('{"arr": [{"a": 1}')
    assert result is not None
    assert result.endswith("}]}")


# ── JSON parsing edge cases ──────────────────────────────────────────────────


def test_parse_llm_json_raises_llmparse_error():
    """parse_llm_json raises LLMParseError (not ValueError) on bad input."""
    with pytest.raises(LLMParseError, match="Failed to parse LLM JSON"):
        parse_llm_json("not json at all")


def test_parse_llm_json_strips_markdown_fences():
    """parse_llm_json removes ```json ... ``` fences."""
    result = parse_llm_json('```json\n{"key": "value"}\n```')
    assert result == {"key": "value"}


def test_parse_llm_json_strips_markdown_fences_no_lang():
    """parse_llm_json removes ``` ... ``` fences without language tag."""
    result = parse_llm_json('```\n{"a": 1}\n```')
    assert result == {"a": 1}


def test_parse_llm_json_extracts_outermost_object():
    """parse_llm_json extracts JSON from surrounding text."""
    result = parse_llm_json('Here is the result: {"x": 1}. Done.')
    assert result == {"x": 1}


def test_parse_llm_json_handles_nested_braces():
    """parse_llm_json handles JSON with nested objects."""
    result = parse_llm_json('{"outer": {"inner": [1, 2, 3]}}')
    assert result == {"outer": {"inner": [1, 2, 3]}}


def test_parse_llm_json_repairs_truncated():
    """parse_llm_json attempts repair on truncated JSON."""
    result = parse_llm_json('{"key": "hello')
    assert isinstance(result, dict)
    assert "key" in result


def test_parse_llm_json_complex_nested():
    """parse_llm_json handles the full agent response format."""
    raw = '''```json
{
  "skip": false,
  "reason": "",
  "summary": "Fixed the bug.",
  "branch_name": "repokeeper/issue-42-fix",
  "commit_message": "fix: resolve null pointer",
  "edits": [
    {"path": "src/app.py", "find": "old", "replace": "new"}
  ],
  "patch": "",
  "changes": {},
  "new_files": {}
}
```'''
    result = parse_llm_json(raw)
    assert result["skip"] is False
    assert result["summary"] == "Fixed the bug."
    assert result["branch_name"] == "repokeeper/issue-42-fix"
    assert len(result["edits"]) == 1


def test_parse_llm_json_standalone_code_fence():
    """parse_llm_json handles ```json without closing fence."""
    result = parse_llm_json('```json\n{"a": 1}')
    assert result == {"a": 1}


def test_parse_llm_json_repairs_truncated_complex():
    """parse_llm_json repairs complex truncated JSON."""
    result = parse_llm_json('{"skip": false, "summary": "Fixed')
    assert isinstance(result, dict)
    assert result["skip"] is False
    assert "summary" in result


# ── LLMClient.from_env ───────────────────────────────────────────────────────


def test_llm_client_from_env(monkeypatch):
    """LLMClient.from_env() reads from environment."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    mock_openai, _ = _fake_openai_module()
    with patch.dict(sys.modules, {"openai": mock_openai}):
        client = LLMClient.from_env()
        assert client.provider == "openai"
