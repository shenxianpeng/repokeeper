"""Unified LLM client for RepoKeeper.

Supports OpenAI-compatible APIs (DeepSeek, Ollama, LocalAI, etc.)
and Anthropic Claude via optional ``anthropic`` package.

Provides streaming output, token usage tracking, and cost estimation.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

from repokeeper.exceptions import AuthError, LLMParseError
from repokeeper.logs import get_logger

logger = get_logger("llm")

# ─── Token pricing estimates (USD per 1M tokens) ───────────────────────────

# Built-in estimates are intentionally conservative snapshots, not billing
# authority. Override with RKP_LLM_PRICE_<MODEL>_INPUT and
# RKP_LLM_PRICE_<MODEL>_OUTPUT when provider pricing differs.

# Date when the built-in pricing data was last reviewed by a human.
# Run ``repokeeper pricing`` to see if this is still current.
_PRICING_LAST_UPDATED = _dt.date(2026, 5, 10)

PRICING: dict[str, dict[str, float]] = {
    "deepseek-chat": {"input": 0.14, "output": 0.28},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-3-5-haiku-20241022": {"input": 0.80, "output": 4.00},
}


@dataclass
class TokenUsage:
    """Token usage and cost for a single LLM call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""


@dataclass
class LLMResponse:
    """Unified response from any LLM provider."""

    content: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str = ""
    finish_reason: str = ""


def _pricing_env_key(model: str) -> str:
    """Return the normalized env var key segment for a model name."""
    return re.sub(r"[^A-Z0-9]+", "_", model.upper()).strip("_")


def _pricing_from_env(model: str) -> dict[str, float] | None:
    """Read per-model pricing override from environment variables."""
    key = _pricing_env_key(model)
    input_price = os.environ.get(f"RKP_LLM_PRICE_{key}_INPUT")
    output_price = os.environ.get(f"RKP_LLM_PRICE_{key}_OUTPUT")
    if input_price is None or output_price is None:
        return None
    try:
        return {"input": float(input_price), "output": float(output_price)}
    except ValueError:
        logger.warning("Ignoring invalid LLM pricing override for model %s", model)
        return None


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate cost based on token counts and configurable pricing."""
    pricing = _pricing_from_env(model)
    if pricing is not None:
        return (prompt_tokens * pricing["input"] + completion_tokens * pricing["output"]) / 1_000_000

    pricing = PRICING.get(model)
    if pricing is None:
        # Try to match by prefix (e.g. "claude-" matches any Claude model)
        for prefix, p in PRICING.items():
            if model.startswith(prefix.split("-")[0]):
                pricing = p
                break
    if pricing is None:
        return 0.0
    return (prompt_tokens * pricing["input"] + completion_tokens * pricing["output"]) / 1_000_000


def get_pricing_summary() -> dict[str, Any]:
    """Return the current pricing table with freshness metadata.

    Returns:
        Dict with keys ``last_updated`` (ISO date), ``stale`` (bool),
        ``stale_days`` (int), and ``models`` (dict of model → pricing).
    """
    today = _dt.date.today()
    stale_days = (today - _PRICING_LAST_UPDATED).days
    return {
        "last_updated": _PRICING_LAST_UPDATED.isoformat(),
        "checked_date": today.isoformat(),
        "stale_days": stale_days,
        "stale": stale_days > 90,
        "models": {
            model: {
                "input": prices["input"],
                "output": prices["output"],
                "env_override": (
                    f"RKP_LLM_PRICE_{_pricing_env_key(model)}_INPUT / "
                    f"RKP_LLM_PRICE_{_pricing_env_key(model)}_OUTPUT"
                ),
            }
            for model, prices in PRICING.items()
        },
    }


# ─── OpenAI-compatible client ────────────────────────────────────────────────


def _chat_openai(
    client: Any,
    model: str,
    system: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    stream: bool,
) -> LLMResponse:
    """Call an OpenAI-compatible chat completions API.

    Args:
        client: ``openai.OpenAI`` instance.
        model: Model name.
        system: System prompt.
        messages: Chat messages (role + content dicts).
        temperature: Sampling temperature.
        max_tokens: Maximum completion tokens.
        stream: If True, stream tokens to stdout.

    Returns:
        Unified LLMResponse.
    """
    all_messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        *messages,
    ]

    if not stream:
        response = client.chat.completions.create(  # type: ignore[arg-type]
            model=model,
            messages=all_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        choice = response.choices[0]
        content = choice.message.content or ""
        usage = TokenUsage(
            prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
            completion_tokens=response.usage.completion_tokens if response.usage else 0,
            total_tokens=response.usage.total_tokens if response.usage else 0,
            cost_usd=_estimate_cost(
                model,
                response.usage.prompt_tokens if response.usage else 0,
                response.usage.completion_tokens if response.usage else 0,
            ),
            model=model,
        )
        return LLMResponse(content=content, usage=usage, model=model,
                          finish_reason=choice.finish_reason or "")

    # ── Streaming path ──
    sys.stdout.write("[repokeeper] LLM streaming: ")
    sys.stdout.flush()
    collected: list[str] = []
    finish_reason = ""
    prompt_tokens = 0
    completion_tokens = 0

    stream_response = client.chat.completions.create(  # type: ignore[arg-type]
        model=model,
        messages=all_messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
        stream_options={"include_usage": True},
    )

    for chunk in stream_response:
        if chunk.choices and chunk.choices[0].delta.content:
            token = chunk.choices[0].delta.content
            collected.append(token)
            # Print a dot every 20 tokens for progress indication
            if len(collected) % 20 == 0:
                sys.stdout.write(".")
                sys.stdout.flush()
        if chunk.choices and chunk.choices[0].finish_reason:
            finish_reason = chunk.choices[0].finish_reason
        if chunk.usage:
            prompt_tokens = chunk.usage.prompt_tokens or 0
            completion_tokens = chunk.usage.completion_tokens or 0

    sys.stdout.write(f" done ({len(collected)} chunks, {completion_tokens} tokens)\n")
    sys.stdout.flush()

    content = "".join(collected)
    total_tokens = prompt_tokens + completion_tokens
    cost = _estimate_cost(model, prompt_tokens, completion_tokens)

    usage_info = TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_usd=cost,
        model=model,
    )

    if cost > 0:
        logger.info("Estimated token cost: %d prompt + %d completion = %d tokens · $%.4f",
                     prompt_tokens, completion_tokens, total_tokens, cost)

    return LLMResponse(content=content, usage=usage_info, model=model,
                      finish_reason=finish_reason)


# ─── Anthropic client ───────────────────────────────────────────────────────


def _chat_anthropic(
    client: Any,
    model: str,
    system: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    stream: bool,
) -> LLMResponse:
    """Call Anthropic's Messages API.

    Args:
        client: ``anthropic.Anthropic`` instance.
        model: Model name.
        system: System prompt.
        messages: Chat messages (converted from OpenAI format).
        temperature: Sampling temperature.
        max_tokens: Maximum completion tokens.
        stream: If True, stream tokens to stdout.

    Returns:
        Unified LLMResponse.
    """
    # Anthropic uses a different message format — no "system" role,
    # system prompt is a top-level parameter.
    anthropic_messages = []
    for m in messages:
        role = m["role"]
        if role == "assistant":
            role = "assistant"
        else:
            role = "user"
        anthropic_messages.append({"role": role, "content": m["content"]})

    if not stream:
        response = client.messages.create(
            model=model,
            system=system,
            messages=anthropic_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = ""
        if response.content:
            # Anthropic returns a list of ContentBlock
            parts = []
            for block in response.content:
                if hasattr(block, "text"):
                    parts.append(block.text)
            content = "\n".join(parts)
        usage = TokenUsage(
            prompt_tokens=response.usage.input_tokens if response.usage else 0,
            completion_tokens=response.usage.output_tokens if response.usage else 0,
            total_tokens=(response.usage.input_tokens + response.usage.output_tokens)
            if response.usage else 0,
            cost_usd=_estimate_cost(
                model,
                response.usage.input_tokens if response.usage else 0,
                response.usage.output_tokens if response.usage else 0,
            ),
            model=model,
        )
        return LLMResponse(
            content=content, usage=usage, model=model,
            finish_reason=response.stop_reason or "",
        )

    # ── Streaming path ──
    sys.stdout.write("[repokeeper] LLM streaming: ")
    sys.stdout.flush()
    collected: list[str] = []
    finish_reason = ""
    input_tokens = 0
    output_tokens = 0

    with client.messages.stream(
        model=model,
        system=system,
        messages=anthropic_messages,
        temperature=temperature,
        max_tokens=max_tokens,
    ) as stream_response:
        for text in stream_response.text_stream:
            collected.append(text)
            if len(collected) % 20 == 0:
                sys.stdout.write(".")
                sys.stdout.flush()

        final = stream_response.get_final_message()
        if hasattr(final, "stop_reason"):
            finish_reason = final.stop_reason or ""
        if hasattr(final, "usage") and final.usage:
            input_tokens = final.usage.input_tokens or 0
            output_tokens = final.usage.output_tokens or 0

    sys.stdout.write(f" done ({len(collected)} chunks, {output_tokens} tokens)\n")
    sys.stdout.flush()

    content = "".join(collected)
    total_tokens = input_tokens + output_tokens
    cost = _estimate_cost(model, input_tokens, output_tokens)

    usage_info = TokenUsage(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=total_tokens,
        cost_usd=cost,
        model=model,
    )

    if cost > 0:
        logger.info("Estimated token cost: %d input + %d output = %d tokens · $%.4f",
                     input_tokens, output_tokens, total_tokens, cost)

    return LLMResponse(content=content, usage=usage_info, model=model,
                      finish_reason=finish_reason)


# ─── Public API ─────────────────────────────────────────────────────────────


@dataclass
class LLMClient:
    """Unified LLM client that auto-detects provider from configuration.

    Usage::

        client = LLMClient(api_key="...", base_url="...", provider="auto")
        response = client.chat(
            system="You are helpful.",
            messages=[{"role": "user", "content": "Hello"}],
            model="deepseek-chat",
            temperature=0.1,
            max_tokens=8000,
            stream=True,
        )
        print(response.content)
        print(f"Cost: ${response.usage.cost_usd:.6f}")
    """

    provider: str  # "auto" | "openai" | "anthropic"
    _client: Any

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        provider: str = "auto",
    ):
        api_key = (
            api_key
            or os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
        )
        base_url = base_url or os.environ.get("LLM_BASE_URL")

        if not api_key:
            raise AuthError("No LLM API key found. Set DEEPSEEK_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY.")

        # Auto-detect provider
        if provider == "auto":
            if api_key.startswith("sk-ant-"):
                provider = "anthropic"
            elif base_url and "anthropic" in base_url:
                provider = "anthropic"
            else:
                provider = "openai"

        self.provider = provider

        if provider == "anthropic":
            try:
                from anthropic import Anthropic
            except ImportError as err:
                raise ImportError(
                    "Anthropic client requires `pip install anthropic`. "
                    "Add 'anthropic' to your dependencies or use an OpenAI-compatible provider."
                ) from err
            self._client = Anthropic(api_key=api_key, base_url=base_url)
        else:
            from openai import OpenAI

            effective_base = base_url or "https://api.deepseek.com"
            self._client = OpenAI(api_key=api_key, base_url=effective_base)

    def chat(
        self,
        system: str = "",
        messages: list[dict[str, str]] | None = None,
        model: str = "deepseek-chat",
        temperature: float = 0.1,
        max_tokens: int = 8000,
        stream: bool = False,
    ) -> LLMResponse:
        """Send a chat completion request.

        Args:
            system: System prompt.
            messages: List of message dicts with ``role`` and ``content`` keys.
            model: Model name.
            temperature: Sampling temperature (0.0–2.0).
            max_tokens: Maximum tokens in the completion.
            stream: If True, stream progressive output to stdout.

        Returns:
            ``LLMResponse`` with content, usage, model, and finish_reason.
        """
        if messages is None:
            messages = []

        if self.provider == "anthropic":
            return _chat_anthropic(
                self._client, model, system, messages,
                temperature, max_tokens, stream,
            )
        return _chat_openai(
            self._client, model, system, messages,
            temperature, max_tokens, stream,
        )

    @staticmethod
    def from_env() -> LLMClient:
        """Create a client from standard environment variables.

        Reads ``DEEPSEEK_API_KEY``, ``OPENAI_API_KEY``, or ``ANTHROPIC_API_KEY``
        and ``LLM_BASE_URL``.
        """
        return LLMClient()


# ─── Shared JSON parsing ────────────────────────────────────────────────────


def _repair_truncated_json(text: str) -> str | None:
    """Attempt to repair a truncated/incomplete JSON string.

    Tries to find the last complete key-value pair and close the object.

    Args:
        text: Possibly truncated JSON string.

    Returns:
        Repaired string or ``None`` if repair is not possible.
    """
    bracket_stack: list[str] = []
    in_string = False
    escape_next = False

    for ch in text:
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            bracket_stack.append(ch)
        elif ch == "}":
            if bracket_stack and bracket_stack[-1] == "{":
                bracket_stack.pop()
        elif ch == "]":
            if bracket_stack and bracket_stack[-1] == "[":
                bracket_stack.pop()

    if not bracket_stack and not in_string:
        return None

    repaired = text.rstrip()
    if in_string:
        repaired += '"'

    for open_ch in reversed(bracket_stack):
        if open_ch == "{":
            repaired += "}"
        elif open_ch == "[":
            repaired += "]"

    return repaired if repaired != text else None


def parse_llm_json(raw: str) -> dict[str, Any]:
    """Parse LLM JSON output with resilience to common formatting issues.

    Handles markdown fences, code block markers, and attempts basic repair
    for unterminated strings.  Designed to be the single shared parser for
    all RepoKeeper modules that consume JSON from an LLM response.

    Args:
        raw: Raw text content from LLM response.

    Returns:
        Parsed JSON dict.

    Raises:
        LLMParseError: If the response could not be parsed as JSON.
    """
    import json as _json
    import re as _re

    text = raw.strip()

    # ── Strip markdown code fences ──
    fence_pattern = r"```(?:json)?\s*\n(.*?)\n```"
    m = _re.search(fence_pattern, text, _re.DOTALL)
    if m:
        text = m.group(1).strip()
    elif text.startswith("```"):
        inner = text.split("```", 2)
        if len(inner) >= 2:
            candidate = inner[1]
            if candidate.startswith("json"):
                candidate = candidate[4:]
            text = candidate.strip()

    # ── Try direct parse ──
    try:
        return _json.loads(text)  # type: ignore[no-any-return]
    except _json.JSONDecodeError as err:
        first_error = err

    # ── Try extracting the outermost JSON object ──
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        extracted = text[start : end + 1]
        try:
            return _json.loads(extracted)  # type: ignore[no-any-return]
        except _json.JSONDecodeError:
            pass

    # ── Try fixing common unterminated string ──
    repaired = _repair_truncated_json(text)
    if repaired is not None:
        try:
            return _json.loads(repaired)  # type: ignore[no-any-return]
        except _json.JSONDecodeError:
            pass

    raise LLMParseError(
        f"Failed to parse LLM JSON response. First error: {first_error}\n"
        f"Raw response (last 2000 chars): ...{text[-2000:]}"
    )
