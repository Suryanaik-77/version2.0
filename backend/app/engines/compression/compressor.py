"""
compressor.py — Entry point wiring all 5 compression blocks.

Single public function: compress()
Called from question.py in place of the existing
compress_for_injection() + build_question_prompt() sequence.

Integration in question.py:

    # BEFORE (existing):
    memory_context = mem.compress_for_injection(ctx.memory)
    prompt = build_question_prompt(
        mode=ctx.mode, domain=ctx.domain,
        transcript=ctx.transcript,
        memory_context=memory_context,
        recent_questions=recent_qs,
        signals=signals, ...
    )

    # AFTER (with compression system):
    from app.engines.compression.compressor import compress
    compressed = compress(
        memory=ctx.memory,
        eval_scores=last_eval_scores,
        signals=inline_signals,
        transcript=ctx.transcript,
        mode=ctx.mode, domain=ctx.domain,
        recent_questions=recent_qs,
        consecutive_weak=cw, consecutive_strong=cs,
        last_question=last_question,
    )
    prompt_string = compressed.to_prompt_string()
    # Pass QUESTION_SYSTEM and prompt_string to stream_generate()

compress() never raises. Falls back to MINIMAL on any error.
"""
from __future__ import annotations

import time
from app.engines.compression.block1_context_reducer import reduce, ReducedContext
from app.engines.compression.block2_budget import allocate, tokens
from app.engines.compression.block3_strategies import (
    serialize_contradiction,
    serialize_signals,
    serialize_memory,
    serialize_eval,
    serialize_example,
    serialize_trend,
    serialize_avoid,
    serialize_transcript,
)
from app.engines.compression.block4_fallback import (
    CompressionMode, build_fallback, check_hard_cap,
    HARD_CAP_TOKENS,
)
from app.engines.compression.block5_format import CompressedPrompt, assemble
from app.models.session import (
    CandidateMemory, InlineSignals, InterviewerMode, VLSIDomain,
)
from app.engines.prompts import (
    QUESTION_SYSTEM, _domain_label, _MODE_TONE_RULES, _corpus_example_block,
    _eval_trend_note,
)


def compress(
    memory:             CandidateMemory | None,
    eval_scores:        dict | None,
    signals:            InlineSignals | None,
    transcript:         str,
    mode:               InterviewerMode,
    domain:             VLSIDomain,
    recent_questions:   list[str] | None = None,
    consecutive_weak:   int = 0,
    consecutive_strong: int = 0,
    last_question:      str = "",
) -> CompressedPrompt:
    """
    Full compression pipeline. Never raises.

    Returns a CompressedPrompt ready for to_prompt_string() → LLM.
    CompressedPrompt.meta contains diagnostics for observability.
    """
    t_start = time.monotonic()
    recent_questions = recent_questions or []

    try:
        return _compress_full(
            memory, eval_scores, signals, transcript,
            mode, domain, recent_questions,
            consecutive_weak, consecutive_strong, last_question,
            t_start,
        )
    except Exception as exc:
        # Rule F6: any exception → MINIMAL fallback
        import structlog
        structlog.get_logger(__name__).error(
            "compressor.exception_fallback", error=str(exc), exc_info=exc
        )
        fallback = build_fallback(
            transcript=transcript,
            domain=domain,
            last_question=last_question,
            reason=f"exception:{exc}",
            mode=CompressionMode.MINIMAL,
        )
        return _build_minimal(
            fallback.transcript, last_question, domain, mode,
            t_start, reason=fallback.reason,
            transcript_fallback=fallback.transcript_fallback,
        )


def _compress_full(
    memory, eval_scores, signals, transcript,
    mode, domain, recent_questions,
    consecutive_weak, consecutive_strong, last_question,
    t_start,
) -> CompressedPrompt:
    """Full compression path — all 5 blocks applied."""

    # ── Block 1: Reduce ───────────────────────────────────────────────────────
    reduced = reduce(memory, eval_scores, signals, transcript)

    # ── Determine what's present ──────────────────────────────────────────────
    has_memory   = bool(
        reduced.active_contradiction or reduced.weak_topics
        or reduced.strong_topics or reduced.recent_claims
        or reduced.repeated_buzzwords
    )
    has_eval     = reduced.eval_avg is not None
    has_signals  = bool(
        reduced.signal_correctness or reduced.signal_vagueness
        or reduced.signal_missing_mechanism or reduced.signal_memorization
        or reduced.signal_contradiction_ref
    )
    has_trend    = bool(_eval_trend_note(consecutive_weak, consecutive_strong))
    has_avoid    = bool(recent_questions)

    # Corpus example (always attempt for non-RECOVERING modes)
    example_utterance = ""
    if mode != InterviewerMode.RECOVERING:
        ex_entries = _get_example(mode, signals)
        if ex_entries:
            example_utterance = ex_entries
    has_example = bool(example_utterance)

    # ── Block 2: Allocate budget ──────────────────────────────────────────────
    alloc = allocate(
        has_signals=has_signals,
        has_contradiction=bool(reduced.active_contradiction),
        has_memory=has_memory,
        has_eval=has_eval,
        has_example=has_example,
        has_trend=has_trend,
        has_avoid=has_avoid,
    )

    # ── Block 3: Serialize each section ──────────────────────────────────────
    # Contradiction goes into signals block if active (priority merge)
    if reduced.active_contradiction:
        signals_text = serialize_contradiction(reduced, alloc.signals)
    else:
        signals_text = serialize_signals(reduced, alloc.signals)

    memory_text   = serialize_memory(reduced, alloc.memory) if has_memory else ""
    eval_text     = serialize_eval(reduced, alloc.eval_summary) if has_eval else ""
    example_text  = serialize_example(example_utterance, alloc.example)
    trend_text    = serialize_trend(
        _eval_trend_note(consecutive_weak, consecutive_strong), alloc.trend
    )
    avoid_text    = serialize_avoid(recent_questions, alloc.avoid)
    transcript_text = serialize_transcript(reduced, alloc.transcript)

    if not transcript_text.strip():
        # Rule F4: empty transcript → fallback
        from app.engines.compression.block4_fallback import get_fallback_transcript
        transcript_text = get_fallback_transcript(domain)
        transcript_fallback = True
    else:
        transcript_fallback = False

    # ── Block 4: Hard cap check ───────────────────────────────────────────────
    user_parts = [
        signals_text, memory_text, eval_text, example_text,
        trend_text, avoid_text, transcript_text,
    ]
    total_user_tokens = tokens("\n".join(p for p in user_parts if p))
    total_tokens = 492 + total_user_tokens

    if check_hard_cap(total_tokens):                                    # Rule F5
        fallback = build_fallback(
            transcript=transcript,
            domain=domain,
            last_question=last_question,
            reason=f"budget_exceeded:{total_tokens}>{HARD_CAP_TOKENS}",
        )
        return _build_minimal(
            fallback.transcript, last_question, domain, mode,
            t_start, reason=fallback.reason,
            transcript_fallback=fallback.transcript_fallback,
        )

    # ── Block 5: Assemble output ──────────────────────────────────────────────
    elapsed_ms = (time.monotonic() - t_start) * 1000

    return assemble(
        system_context    = QUESTION_SYSTEM,
        memory_text       = memory_text,
        eval_text         = eval_text,
        signals_text      = signals_text,
        transcript_text   = transcript_text,
        last_question     = last_question,
        domain_label      = _domain_label(domain),
        mode_label        = _MODE_TONE_RULES[mode]["label"],
        example_text      = example_text,
        trend_text        = trend_text,
        avoid_text        = avoid_text,
        mode              = CompressionMode.FULL,
        fallback_reason   = "",
        memory_missing    = memory is None,
        eval_missing      = not has_eval,
        signals_empty     = not has_signals,
        transcript_fallback = transcript_fallback,
        elapsed_ms        = elapsed_ms,
    )


def _build_minimal(
    transcript: str,
    last_question: str,
    domain: VLSIDomain,
    mode: InterviewerMode,
    t_start: float,
    reason: str,
    transcript_fallback: bool = False,
) -> CompressedPrompt:
    """Rule F7: MINIMAL mode — transcript + last_question only."""
    elapsed_ms = (time.monotonic() - t_start) * 1000
    return assemble(
        system_context    = QUESTION_SYSTEM,
        memory_text       = "",
        eval_text         = "",
        signals_text      = "",
        transcript_text   = transcript[-200:].strip(),
        last_question     = last_question,
        domain_label      = _domain_label(domain),
        mode_label        = _MODE_TONE_RULES[mode]["label"],
        example_text      = "",
        trend_text        = "",
        avoid_text        = "",
        mode              = CompressionMode.MINIMAL,
        fallback_reason   = reason,
        memory_missing    = True,
        eval_missing      = True,
        signals_empty     = True,
        transcript_fallback = transcript_fallback,
        elapsed_ms        = elapsed_ms,
    )


def _get_example(mode: InterviewerMode, signals: InlineSignals | None) -> str:
    """Get corpus example utterance for the current context."""
    from app.engines.corpus import get_mode_examples, get_signal_examples
    from app.models.session import SignalLevel, Correctness

    if signals:
        from app.models.session import SignalLevel, Correctness
        ex = get_signal_examples(
            vagueness_high=(signals.vagueness == SignalLevel.HIGH),
            wrong_answer=(signals.correctness == Correctness.WRONG),
            memorization_suspected=signals.memorization_suspected,
            contradiction_active=bool(signals.contradiction_with),
            n=1,
        )
        if ex:
            return ex[0].utterance

    ex = get_mode_examples(mode, n=1)
    return ex[0].utterance if ex else ""
