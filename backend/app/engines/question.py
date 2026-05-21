"""
question_engine.py — Single-pass streaming question generation with inline signal detection.

THE HOT PATH. Every microsecond here is perceived by the candidate.

Execution model (mandatory):
  stream(ctx) -> AsyncIterator[str]
    - Starts yielding tokens within FIRST_TOKEN_DEADLINE_MS (400ms target).
    - Detects signals INLINE as tokens accumulate — no second LLM pass.
    - Emits InlineSignals as a background task AFTER first sentence.
    - Supports clean CancelledError for barge-in.

What is forbidden here:
  - Multi-stage reasoning chains
  - Eval-before-question pipelines
  - JSON intermediate objects
  - Post-processing passes
  - Buffering full response before yielding

Signal detection runs on accumulated text, not on a separate LLM call.
Signals are heuristic — intentionally imprecise. Precision comes from eval_engine.
"""
from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator

import structlog

from app.config import get_settings
from app.core import redis as r
from app.engines import memory as mem
from app.engines.prompts import QUESTION_SYSTEM, build_question_prompt, get_system_prompt
from app.models.session import (
    InlineSignals,
    TurnContext,
    InterviewerMode,
    Correctness,
    SignalLevel,
)
from app.observability.metrics import TurnLatencyTracker, record_event
from app.providers.llm import stream_generate, LLMTimeoutError, LLMCircuitOpenError

log = structlog.get_logger(__name__)
settings = get_settings()

# Sentence boundary detection — used to know when first sentence is complete
_SENTENCE_END = re.compile(r'[.?!](\s|$)')


async def stream(
    ctx: TurnContext,
    tracker: TurnLatencyTracker | None = None,
    cognition: "CognitionResult | None" = None,
) -> AsyncIterator[str]:
    """
    Core hot-path function.

    Given a TurnContext (state + memory + transcript), streams interviewer tokens.

    Responsibilities:
      1. Build compressed prompt (< 500 tokens total)
      2. Start LLM stream
      3. Yield tokens to caller (TTS pipeline / WebSocket relay)
      4. After first sentence: detect inline signals, emit as background task
      5. Clean up on CancelledError (barge-in)

    Latency targets:
      - First token yield: < 400ms from this function being called
      - First sentence complete: < 800ms
    """
    # Build prompt — no forced topics, let the interviewer decide naturally
    memory_context = mem.compress_for_injection(ctx.memory)
    recent_qs = await _get_recent_questions_from_redis(ctx.session_id)
    if not recent_qs:
        recent_qs = _get_recent_questions(ctx)

    prompt = build_question_prompt(
        transcript=ctx.transcript,
        domain=ctx.domain,
        resume=ctx.resume.model_dump() if ctx.resume else None,
        memory_context=memory_context,
        recent_questions=recent_qs,
        turn_number=ctx.turn_number,
        cognition=cognition,
    )

    # Debug: log what the LLM actually sees
    log.info("question.prompt_built",
             session_id=ctx.session_id,
             turn=ctx.turn_number,
             mode=ctx.mode.value,
             transcript_len=len(ctx.transcript),
             transcript_preview=ctx.transcript[:100] if ctx.transcript else "(empty)",
             recent_qs_count=len(recent_qs),
             prompt_len=len(prompt))

    accumulated: list[str] = []
    first_sentence_done = False
    signals_emitted = False
    t_start = time.monotonic()

    try:
        # Per-session interviewer personality (deterministic from session_id)
        # Admin override takes priority if set.
        from app.core.prompt_cache import get_live_system_prompt
        active_system = await get_live_system_prompt("question_system") or get_system_prompt(ctx.session_id)

        async for token in stream_generate(
            system=active_system,
            prompt=prompt,
            max_tokens=150,        # Interviewer questions must be short — 1-2 sentences
            temperature=0.72,
            session_id=ctx.session_id,
            turn_number=ctx.turn_number,
        ):
            accumulated.append(token)
            yield token

            # Record first token timing
            if tracker and len(accumulated) == 1:
                tracker.mark("first_token")

            # Detect end of first sentence — emit signals once
            if not first_sentence_done and not signals_emitted:
                joined = "".join(accumulated)
                if _SENTENCE_END.search(joined):
                    first_sentence_done = True
                    # Emit signals as non-blocking background task
                    # This must NOT delay the token stream
                    asyncio.create_task(
                        _emit_inline_signals(ctx, joined),
                        name=f"signals_{ctx.session_id}_{ctx.turn_number}",
                    )
                    signals_emitted = True

    except asyncio.CancelledError:
        # Barge-in — clean exit
        if accumulated:
            log.debug(
                "question.cancelled_mid_stream",
                session_id=ctx.session_id,
                tokens_generated=len(accumulated),
            )
        raise  # Re-raise — caller handles this

    except (LLMTimeoutError, LLMCircuitOpenError) as exc:
        log.error("question.llm_error", session_id=ctx.session_id, error=str(exc))
        # Yield a brief fallback question rather than silence
        fallback = _fallback_question(ctx)
        yield fallback
        asyncio.create_task(
            _emit_inline_signals(ctx, fallback),
            name=f"signals_fallback_{ctx.session_id}",
        )

    finally:
        # Store question IMMEDIATELY (not background) so next turn sees it
        if accumulated:
            question_text = "".join(accumulated).strip()
            try:
                await _store_generated_question(ctx.session_id, ctx.turn_number, question_text)
            except Exception as e:
                log.warning("question.store_failed", error=str(e))
            # Context update can be background
            asyncio.create_task(
                r.set_session_context(
                    (await r.get_session_context(ctx.session_id) or _dummy_context(ctx))
                ),
                name=f"ctx_update_{ctx.session_id}",
            )


# ── Inline signal detection ───────────────────────────────────────────────────

async def _emit_inline_signals(ctx: TurnContext, question_so_far: str) -> None:
    """
    Detect signals from the generated question text.
    Runs as background task — latency impact: zero.

    These signals are intentionally heuristic:
    - The question CONTENT reveals what was detected in the answer.
    - Asking for mechanism → vagueness was detected.
    - Asking about edge cases → strength was detected.
    - Surfacing contradiction → contradiction was detected.
    """
    signals = _detect_signals(ctx, question_so_far)

    # Store for strategy_engine to consume
    await mem.store_inline_signals(ctx.session_id, signals)

    record_event(
        "signals.emitted",
        session_id=ctx.session_id,
        turn=ctx.turn_number,
        correctness=signals.correctness,
        vagueness=signals.vagueness.value,
        memorization=signals.memorization_suspected,
    )


def _detect_signals(ctx: TurnContext, question_text: str) -> InlineSignals:
    """
    Extract signals from generated question + candidate transcript.
    All keyword-based — no LLM, < 1ms.
    """
    q = question_text.lower()
    t = ctx.transcript.lower()

    # Vagueness detected → question asks for mechanism
    mechanism_phrases = [
        "mechanism", "how does", "how exactly", "how would", "explain how",
        "walk me through", "what causes", "why does", "what happens when",
    ]
    vagueness = SignalLevel.HIGH if any(p in q for p in mechanism_phrases) else SignalLevel.LOW

    # Contradiction detected → question surfaces prior statement
    contradiction_phrases = [
        "earlier you said", "you mentioned", "but earlier", "you also said",
        "that contradicts", "you previously", "before you said",
    ]
    contradiction_detected = any(p in q for p in contradiction_phrases)
    # Find which prior claim it references
    contradiction_ref = None
    if contradiction_detected and ctx.memory.claims:
        contradiction_ref = ctx.memory.claims[-1]  # most recent claim

    # Strength detected → question pushes to edge cases
    pressure_phrases = [
        "edge case", "what if", "failure mode", "worst case", "corner case",
        "what breaks", "what fails", "under what conditions",
    ]
    correctness = Correctness.CORRECT if any(p in q for p in pressure_phrases) else Correctness.UNKNOWN

    # Memorization suspected → short answer + definitional question
    memorization = mem.detect_memorization_fast(ctx.transcript)

    # Missing mechanism in answer
    missing_mechanism = None
    for phrase in mechanism_phrases:
        if phrase in q:
            # Extract what mechanism is being asked for
            match = re.search(rf'{re.escape(phrase)}\s+([a-z\s]{{5,30}})', q)
            if match:
                missing_mechanism = match.group(1).strip()
            break

    return InlineSignals(
        session_id=ctx.session_id,
        turn_number=ctx.turn_number,
        correctness=correctness,
        vagueness=vagueness,
        confidence=SignalLevel.MEDIUM,
        memorization_suspected=memorization,
        missing_mechanism=missing_mechanism,
        contradiction_with=contradiction_ref,
    )


# ── Recent question tracking (anti-repetition) ───────────────────────────────

async def _store_generated_question(session_id: str, turn_number: int, question: str) -> None:
    """Store generated question in turn history for anti-repetition checks."""
    await r.push_turn_summary(session_id, {
        "turn": turn_number,
        "question": question[:200],  # cap length
    })


async def _get_recent_questions_from_redis(session_id: str) -> list[str]:
    """Read recent questions directly from Redis turn history."""
    try:
        turns = await r.get_recent_turns(session_id, n=5)
        return [t.get("question", "") for t in turns if t.get("question")]
    except Exception:
        return []


def _get_recent_questions(ctx: TurnContext) -> list[str]:
    """Extract recent questions from prior answers (fallback)."""
    return [a for a in ctx.prior_answers if a] if ctx.prior_answers else []



# ── Fallback question (LLM failure path) ─────────────────────────────────────

def _fallback_question(ctx: TurnContext) -> str:
    """
    Pre-built fallback for LLM failures. Domain-specific but generic.
    Never uses LLM — instant response.
    """
    from app.models.session import VLSIDomain
    fallbacks = {
        VLSIDomain.ANALOG_LAYOUT:
            "Walk me through the layout challenges you typically see in matched device pairs.",
        VLSIDomain.PHYSICAL_DESIGN:
            "What does your timing closure flow look like when you hit a hold violation after CTS?",
        VLSIDomain.DESIGN_VERIFICATION:
            "How do you approach coverage closure when your directed tests aren't reaching certain corner cases?",
    }
    return fallbacks.get(ctx.domain, "What's the most difficult technical problem you solved in your last tape-out?")


def _dummy_context(ctx: TurnContext):
    """Used as fallback when context read returns None."""
    from app.models.session import SessionContext
    return SessionContext(
        session_id=ctx.session_id,
        mode=ctx.mode,
        active_domain=ctx.domain,
        turn_count=ctx.turn_number,
    )
