"""
llm.py — LLM provider adapter (OpenAI, swappable via config).

Contract:
  stream_generate(system, prompt, max_tokens) -> AsyncIterator[str]
  generate(system, prompt, max_tokens) -> str

Swap requirement: changing providers requires ONLY replacing this file.
No consuming module changes permitted.

Runtime guarantees:
  - First token must arrive within FIRST_TOKEN_DEADLINE_MS.
  - Circuit breaker opens after 3 consecutive failures.
  - Token counts tracked per call for observability.
  - Streaming never buffers the full response.
"""
from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator

import structlog
from openai import AsyncOpenAI, APITimeoutError, APIConnectionError

from app.config import get_settings
from app.core import redis as r
from app.observability.metrics import record_event

log = structlog.get_logger(__name__)
settings = get_settings()

# ── Client singleton ──────────────────────────────────────────────────────────

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=settings.FIRST_TOKEN_DEADLINE_MS / 1000 * 6,  # 6x first-token as total
            max_retries=0,  # We handle retries ourselves
        )
    return _client


# ── Failure tracking (in-process, for circuit breaker) ───────────────────────

_consecutive_failures: int = 0
_CIRCUIT_OPEN_THRESHOLD = 3
_CIRCUIT_RECOVERY_TTL = 60  # seconds


async def _check_circuit(provider: str = "openai") -> None:
    state = await r.get_circuit_state(provider)
    if state == r.CircuitState.OPEN:
        raise LLMCircuitOpenError(f"LLM circuit open for {provider}")


async def _record_failure(provider: str = "openai") -> None:
    global _consecutive_failures
    _consecutive_failures += 1
    record_event("provider.llm.error", provider=provider, consecutive=_consecutive_failures)
    if _consecutive_failures >= _CIRCUIT_OPEN_THRESHOLD:
        await r.open_circuit(provider, ttl_seconds=_CIRCUIT_RECOVERY_TTL)
        log.error("llm.circuit_opened", provider=provider)


async def _record_success(provider: str = "openai") -> None:
    global _consecutive_failures
    if _consecutive_failures > 0:
        _consecutive_failures = 0
        await r.close_circuit(provider)


# ── Core streaming function ───────────────────────────────────────────────────

async def stream_generate(
    system: str,
    prompt: str,
    max_tokens: int = 150,
    temperature: float = 0.72,
    session_id: str = "",
    turn_number: int = 0,
    model_override: str = "",
) -> AsyncIterator[str]:
    """
    Stream tokens from LLM. First token must arrive within FIRST_TOKEN_DEADLINE_MS.

    Hot-path contract:
    - Yields tokens as they arrive — no buffering.
    - Raises LLMTimeoutError if first token exceeds deadline.
    - Raises LLMCircuitOpenError if provider is degraded.
    - Caller must handle CancelledError for barge-in (no cleanup required).

    Token accounting emitted non-blocking after stream ends.
    """
    await _check_circuit("openai")

    # Read admin-selected model from runtime config
    from app.core.runtime_config import get as rc_get
    model = model_override or rc_get("qgen_model", "") or settings.OPENAI_MODEL

    client = get_client()
    t_start = time.monotonic()
    first_token_received = False
    tokens_out = 0

    try:
        stream = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": prompt},
                ],
                stream=True,
                max_tokens=max_tokens,
                temperature=temperature,
                stream_options={"include_usage": True},
            ),
            timeout=(settings.FIRST_TOKEN_DEADLINE_MS / 1000) * 4,
        )

        async for chunk in stream:
            # Check for usage data (last chunk)
            if hasattr(chunk, "usage") and chunk.usage:
                _emit_token_cost(chunk.usage.prompt_tokens, chunk.usage.completion_tokens, session_id)
                continue

            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta is None:
                continue

            # First-token deadline check
            if not first_token_received:
                first_token_ms = (time.monotonic() - t_start) * 1000
                if first_token_ms > settings.FIRST_TOKEN_DEADLINE_MS:
                    record_event(
                        "llm.first_token_slow",
                        session_id=session_id,
                        latency_ms=int(first_token_ms),
                        threshold_ms=settings.FIRST_TOKEN_DEADLINE_MS,
                    )
                first_token_received = True

            tokens_out += 1
            yield delta

        await _record_success("openai")

    except asyncio.TimeoutError:
        await _record_failure("openai")
        raise LLMTimeoutError("LLM did not return first token within deadline")

    except (APITimeoutError, APIConnectionError) as exc:
        await _record_failure("openai")
        raise LLMProviderError(f"LLM provider error: {exc}") from exc

    except asyncio.CancelledError:
        # Barge-in — clean exit, no failure recorded
        log.debug("llm.stream_cancelled", session_id=session_id, turn=turn_number)
        raise


async def generate(
    system: str,
    prompt: str,
    max_tokens: int = 512,
    temperature: float = 0.3,
    session_id: str = "",
    use_eval_model: bool = False,
) -> str:
    """
    Non-streaming generation. Used ONLY by eval_engine (async, off hot-path).
    Never called from question generation.
    """
    # Eval uses eval_model from admin config
    model = ""
    if use_eval_model:
        from app.core.runtime_config import get as rc_get
        model = rc_get("eval_model", "") or settings.OPENAI_MODEL

    tokens: list[str] = []
    async for token in stream_generate(
        system=system,
        prompt=prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        session_id=session_id,
        model_override=model,
    ):
        tokens.append(token)
    return "".join(tokens)


def _emit_token_cost(tokens_in: int, tokens_out: int, session_id: str) -> None:
    """Fire-and-forget cost tracking."""
    # GPT-4o-mini pricing: $0.15/1M in, $0.60/1M out
    cost_usd = (tokens_in * 0.00000015) + (tokens_out * 0.00000060)
    record_event(
        "turn.token_cost",
        session_id=session_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=round(cost_usd, 8),
    )


# ── Exceptions ────────────────────────────────────────────────────────────────

class LLMTimeoutError(Exception):
    pass

class LLMCircuitOpenError(Exception):
    pass

class LLMProviderError(Exception):
    pass
