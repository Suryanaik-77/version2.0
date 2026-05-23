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
from dotenv import load_dotenv
load_dotenv()
import asyncio
import time
from typing import AsyncIterator

import structlog
from openai import AsyncOpenAI, APITimeoutError, APIConnectionError

from app.config import get_settings
from app.core import redis as r
from app.observability.metrics import record_event
from app.observability.call_tracker import track_llm_call

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

    messages = [
        {"role": "system", "content": system},
        {"role": "user",   "content": prompt},
    ]

    # Route to Bedrock for AWS model IDs
    if model.startswith("us.") or "anthropic" in model or "amazon" in model or "meta" in model:
        async for token in _bedrock_generate(model, messages, max_tokens, temperature, session_id):
            yield token
        return

    # Route to Grok for xAI model IDs
    if model.startswith("grok-"):
        async for token in _grok_generate(model, messages, max_tokens, temperature, session_id):
            yield token
        return

    # Default: OpenAI
    client = get_client()
    t_start = time.monotonic()
    first_token_received = False
    tokens_out = 0

    try:
        stream = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=messages,
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

        elapsed_ms = int((time.monotonic() - t_start) * 1000)
        await _record_success("openai")
        track_llm_call(
            session_id=session_id,
            step="LLM_question",
            model=model,
            latency_ms=elapsed_ms,
            output_tokens=tokens_out,
            status="success",
        )

    except asyncio.TimeoutError:
        elapsed_ms = int((time.monotonic() - t_start) * 1000)
        await _record_failure("openai")
        track_llm_call(session_id=session_id, step="LLM_question", model=model,
                        latency_ms=elapsed_ms, status="failure", error="timeout")
        raise LLMTimeoutError("LLM did not return first token within deadline")

    except (APITimeoutError, APIConnectionError) as exc:
        elapsed_ms = int((time.monotonic() - t_start) * 1000)
        await _record_failure("openai")
        track_llm_call(session_id=session_id, step="LLM_question", model=model,
                        latency_ms=elapsed_ms, status="failure", error=str(exc))
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


# ── Bedrock Provider (Claude, Llama, Nova, DeepSeek, Mistral) ────────────────

_bedrock_client = None

def _get_bedrock_client():
    global _bedrock_client
    if _bedrock_client is None:
        import boto3, os
        _bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        )
    return _bedrock_client


async def _bedrock_generate(
    model_id: str, messages: list, max_tokens: int, temperature: float, session_id: str
) -> AsyncIterator[str]:
    """Call Bedrock synchronously in executor, yield result as tokens."""
    import json as _json

    def _call():
        client = _get_bedrock_client()
        is_claude = "anthropic" in model_id.lower()
        is_llama = "meta" in model_id.lower() or "llama" in model_id.lower()
        is_nova = "amazon" in model_id.lower() or "nova" in model_id.lower()
        is_deepseek = "deepseek" in model_id.lower()

        system_text = ""
        user_text = ""
        for msg in messages:
            if msg.get("role") == "system":
                system_text += msg.get("content", "") + "\n"
            elif msg.get("role") == "user":
                user_text += msg.get("content", "") + "\n"

        if is_claude:
            filtered = [m for m in messages if m.get("role") != "system"]
            if not filtered:
                filtered = [{"role": "user", "content": system_text.strip()}]
                system_text = ""
            for i, m in enumerate(filtered):
                if isinstance(m.get("content"), str):
                    filtered[i] = {"role": m["role"], "content": [{"type": "text", "text": m["content"]}]}
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": filtered,
            }
            if system_text.strip():
                body["system"] = [{"type": "text", "text": system_text.strip(), "cache_control": {"type": "ephemeral"}}]
        elif is_llama:
            prompt = ""
            if system_text:
                prompt += f"<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n{system_text.strip()}<|eot_id|>"
            prompt += f"<|start_header_id|>user<|end_header_id|>\n\n{user_text.strip()}<|eot_id|><|start_header_id|>assistant<|end_header_id|>\n\n"
            body = {"prompt": prompt, "max_gen_len": max_tokens, "temperature": temperature}
        elif is_nova:
            body = {
                "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
                "messages": [{"role": "user", "content": [{"text": user_text.strip()}]}],
            }
            if system_text.strip():
                body["system"] = [{"text": system_text.strip()}]
        elif is_deepseek:
            formatted = f"<｜begin▁of▁sentence｜>"
            if system_text:
                formatted += f"<｜System｜>{system_text.strip()}"
            formatted += f"<｜User｜>{user_text.strip()}<｜Assistant｜>"
            body = {"prompt": formatted, "max_tokens": min(max_tokens, 8192), "temperature": temperature}
        else:
            body = {"max_tokens": max_tokens, "temperature": temperature, "messages": messages}

        resp = client.invoke_model(
            modelId=model_id, contentType="application/json",
            accept="application/json", body=_json.dumps(body)
        )
        result_body = _json.loads(resp["body"].read())

        if is_claude:
            return result_body["content"][0]["text"].strip()
        elif is_llama:
            return result_body.get("generation", "").strip()
        elif is_nova:
            return result_body.get("output", {}).get("message", {}).get("content", [{}])[0].get("text", "").strip()
        elif is_deepseek:
            choices = result_body.get("choices", [])
            return choices[0].get("text", "").strip() if choices else ""
        elif "content" in result_body:
            return result_body["content"][0]["text"].strip()
        elif "choices" in result_body:
            c = result_body["choices"][0]
            return c.get("message", {}).get("content", c.get("text", "")).strip()
        return _json.dumps(result_body)

    import asyncio
    loop = asyncio.get_event_loop()
    t0 = time.monotonic()
    try:
        result = await asyncio.wait_for(loop.run_in_executor(None, _call), timeout=15.0)
        elapsed = int((time.monotonic() - t0) * 1000)
        log.info("llm.bedrock", model=model_id, latency_ms=elapsed, chars=len(result))
        record_event("llm.bedrock", session_id=session_id, model=model_id, latency_ms=elapsed)
        track_llm_call(session_id=session_id, step="LLM_question", model=model_id,
                        latency_ms=elapsed, output_tokens=len(result.split()), status="success")
        yield result
    except Exception as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        log.error("llm.bedrock_error", model=model_id, error=str(exc))
        track_llm_call(session_id=session_id, step="LLM_question", model=model_id,
                        latency_ms=elapsed, status="failure", error=str(exc))
        raise LLMProviderError(f"Bedrock error: {exc}")


# ── Grok Provider (xAI — OpenAI-compatible API) ─────────────────────────────

_grok_client: AsyncOpenAI | None = None

def _get_grok_client() -> AsyncOpenAI:
    global _grok_client
    if _grok_client is None:
        import os
        _grok_client = AsyncOpenAI(
            api_key=os.getenv("XAI_API_KEY", ""),
            base_url="https://api.x.ai/v1",
            max_retries=0,
        )
    return _grok_client


async def _grok_generate(
    model_id: str, messages: list, max_tokens: int, temperature: float, session_id: str
) -> AsyncIterator[str]:
    """Stream tokens from Grok (xAI). OpenAI-compatible API."""
    client = _get_grok_client()
    t_start = time.monotonic()
    try:
        stream = await asyncio.wait_for(
            client.chat.completions.create(
                model=model_id,
                messages=messages,
                stream=True,
                max_tokens=max_tokens,
                temperature=temperature,
            ),
            timeout=15.0,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
        elapsed = int((time.monotonic() - t_start) * 1000)
        log.info("llm.grok", model=model_id, latency_ms=elapsed)
        record_event("llm.grok", session_id=session_id, model=model_id, latency_ms=elapsed)
        track_llm_call(session_id=session_id, step="LLM_question", model=model_id,
                        latency_ms=elapsed, status="success")
    except Exception as exc:
        elapsed = int((time.monotonic() - t_start) * 1000)
        log.error("llm.grok_error", model=model_id, error=str(exc))
        track_llm_call(session_id=session_id, step="LLM_question", model=model_id,
                        latency_ms=elapsed, status="failure", error=str(exc))
        raise LLMProviderError(f"Grok error: {exc}")
