"""
eval_engine.py — Async formal scoring across 7 dimensions.

Execution contract (mandatory):
  - NEVER called before or during question generation.
  - Always launched as asyncio.create_task() from interview_engine.
  - Has NO return value to the current turn — results affect TURN N+1 only.
  - Writes results to Redis (short TTL) and queues Postgres write.

Timeout: 8000ms hard cap. Eval failure is non-fatal.
If eval fails: strategy_engine uses inline signals only for next turn.

Bug fix (v2):
  _parse_eval_json previously returned the full LLM JSON dict, which could
  include extra string-valued keys (e.g. "overall": "good", "comment": "...").
  sum(scores.values()) then raised TypeError: unsupported operand type(s)
  for +: 'int' and 'str'. Fix: return ONLY the 7 expected numeric keys.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime

import structlog

from app.config import get_settings
from app.core import redis as r
from app.engines import memory as mem
from app.engines import strategy
from app.engines.prompts import EVAL_SYSTEM, build_eval_prompt
from app.models.session import (
    InterviewerMode,
    InlineSignals,
    VLSIDomain,
)
from app.observability.metrics import record_event
from app.providers.llm import generate, LLMTimeoutError, LLMCircuitOpenError, LLMProviderError
from app.observability.call_tracker import track_llm_call

log = structlog.get_logger(__name__)
settings = get_settings()

_EVAL_KEY_TTL = 300  # 5 minutes

# The 7 scoring dimensions. Only these keys are valid numeric scores.
_SCORE_KEYS = frozenset({
    "accuracy", "depth", "completeness", "clarity",
    "maturity", "ownership", "correctness",
})


async def run_async_eval(
    session_id: str,
    transcript: str,
    domain: VLSIDomain,
    last_question: str,
    turn_number: int,
    inline_signals: InlineSignals | None,
) -> None:
    """
    Full eval pipeline. Called as a background task — never awaited in hot path.

    Flow:
      1. LLM scoring call (~1000-1800ms, Bedrock)
      2. Parse scores from JSON
      3. Decide new mode via strategy_engine
      4. Update session state in Redis
      5. Update memory_engine
      6. Queue Postgres write (background)
    """
    t_start = time.monotonic()

    try:
        # Step 1: LLM scoring
        prompt = build_eval_prompt(domain, last_question, transcript)
        raw    = await asyncio.wait_for(
            generate(
                system=EVAL_SYSTEM,
                prompt=prompt,
                max_tokens=100,
                temperature=0.1,
                session_id=session_id,
                use_eval_model=True,
            ),
            timeout=settings.EVAL_ASYNC_DEADLINE_MS / 1000,
        )

        eval_latency = int((time.monotonic() - t_start) * 1000)
        from app.core.runtime_config import get as rc_get
        eval_model = rc_get("eval_model", "") or settings.OPENAI_MODEL
        track_llm_call(
            session_id=session_id, step="LLM_evaluation", model=eval_model,
            latency_ms=eval_latency, status="success",
        )

        # Step 2: Parse scores — returns ONLY the 7 numeric keys (TypeError fixed)
        scores = _parse_eval_json(raw)
        if scores is None:
            log.warning("eval.parse_failed", session_id=session_id, raw=raw[:100])
            return

        flags: list[str] = []

        # Step 2.5: Validate and fix contradictions (~0.1ms, no I/O)
        from app.engines.eval_validator import validate_and_fix
        scores, validation_flags = validate_and_fix(scores, transcript)
        flags.extend(validation_flags)
        if validation_flags:
            log.info("eval.validated", session_id=session_id,
                     turn=turn_number, fixes=validation_flags)

        # Step 3: Read current state
        from app.core.session import get_session
        state = await get_session(session_id)
        if not state or not state.is_active:
            return

        # Step 4: Compute average — safe because scores only contains int values
        avg = sum(scores[k] for k in _SCORE_KEYS if k in scores) / len(_SCORE_KEYS)

        consecutive_weak, consecutive_strong = await _get_consecutive_counts(session_id)
        if avg >= 7.0:
            consecutive_strong = min(consecutive_strong + 1, 5)
            consecutive_weak   = 0
        elif avg < 5.0:
            consecutive_weak   = min(consecutive_weak + 1, 5)
            consecutive_strong = 0

        new_mode = strategy.decide_mode_from_eval(
            current_mode=state.mode,
            eval_scores=scores,
            inline_signals=inline_signals,
            consecutive_weak=consecutive_weak,
            consecutive_strong=consecutive_strong,
        )

        # Step 5: Update mode in Redis (takes effect on TURN N+1)
        if new_mode != state.mode:
            from app.core.session import update_mode
            await update_mode(session_id, new_mode)
            log.info(
                "eval.mode_updated",
                session_id=session_id,
                turn=turn_number,
                from_mode=state.mode.value,
                to_mode=new_mode.value,
                avg_score=round(avg, 1),
            )

        # Step 5b: Store scores for cognition layer
        rds = r._get_pool()
        await rds.setex(
            f"session:{session_id}:eval:{turn_number}",
            settings.SESSION_TTL,
            json.dumps(scores),
        )

        await _set_consecutive_counts(session_id, consecutive_weak, consecutive_strong)

        # Step 7: Update memory (background)
        asyncio.create_task(
            mem.update_from_eval(
                session_id=session_id,
                transcript=transcript,
                domain=domain,
                eval_scores=scores,
                inline_signals=inline_signals or InlineSignals(
                    session_id=session_id, turn_number=turn_number
                ),
                turn_number=turn_number,
                last_question=last_question,
            ),
            name=f"mem_update_{session_id}_{turn_number}",
        )

        # Step 8: Persist to Postgres (background)
        asyncio.create_task(
            _persist_turn_eval(
                session_id=session_id,
                turn_number=turn_number,
                eval_scores=scores,
                signals=inline_signals.model_dump() if inline_signals else None,
                elapsed_ms=int((time.monotonic() - t_start) * 1000),
                flags=flags,
                question_text=last_question,
                answer_text=transcript,
            ),
            name=f"db_eval_{session_id}_{turn_number}",
        )

        elapsed_ms = int((time.monotonic() - t_start) * 1000)
        record_event(
            "eval.completed",
            session_id=session_id,
            turn_number=turn_number,
            avg_score=round(avg, 1),
            new_mode=new_mode.value,
            elapsed_ms=elapsed_ms,
            flags=flags,
        )
        log.info(
            "eval.done",
            session_id=session_id,
            turn=turn_number,
            avg=round(avg, 1),
            elapsed_ms=elapsed_ms,
        )

    except asyncio.TimeoutError:
        elapsed_ms = int((time.monotonic() - t_start) * 1000)
        log.warning("eval.timeout", session_id=session_id, turn=turn_number)
        record_event("eval.timeout", session_id=session_id)
        track_llm_call(session_id=session_id, step="LLM_evaluation", model="unknown",
                        latency_ms=elapsed_ms, status="failure", error="timeout")

    except (LLMTimeoutError, LLMCircuitOpenError, LLMProviderError) as exc:
        elapsed_ms = int((time.monotonic() - t_start) * 1000)
        log.warning("eval.llm_error", session_id=session_id, error=str(exc))
        record_event("eval.llm_error", session_id=session_id, error=str(exc))
        track_llm_call(session_id=session_id, step="LLM_evaluation", model="unknown",
                        latency_ms=elapsed_ms, status="failure", error=str(exc))

    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t_start) * 1000)
        log.error("eval.unexpected_error", session_id=session_id,
                  error=str(exc), exc_info=exc)
        record_event("eval.error", session_id=session_id)
        track_llm_call(session_id=session_id, step="LLM_evaluation", model="unknown",
                        latency_ms=elapsed_ms, status="failure", error=str(exc))


def _parse_eval_json(raw: str) -> dict | None:
    """
    Parse LLM eval response into a dict of {dimension: int_score}.

    Returns ONLY the 7 expected numeric keys — no extra LLM keys survive.
    This prevents TypeError when summing scores (LLMs sometimes add string keys
    like "overall": "good" or "comment": "..." alongside numeric scores).

    All scores clamped to [0, 10] and cast to int.
    Returns None if the response cannot be parsed or is missing expected keys.
    """
    raw = raw.strip()

    # Strip markdown fences
    if raw.startswith("```"):
        raw = re.sub(r"```(?:json)?", "", raw).strip()

    match = re.search(r'\{.+\}', raw, re.DOTALL)
    if not match:
        return None

    try:
        data = json.loads(match.group())

        # Require all 7 dimensions to be present
        if not _SCORE_KEYS.issubset(data.keys()):
            return None

        # Return ONLY the 7 expected keys, each cast to int and clamped
        # Extra keys (e.g. "overall", "comment", "flags") are intentionally discarded
        return {
            k: max(0, min(10, int(data[k])))
            for k in _SCORE_KEYS
        }

    except (json.JSONDecodeError, ValueError, TypeError, KeyError):
        return None


# ── Consecutive score tracking ────────────────────────────────────────────────

async def _get_consecutive_counts(session_id: str) -> tuple[int, int]:
    rds  = r._get_pool()
    pipe = rds.pipeline(transaction=False)
    pipe.get(f"session:{session_id}:consecutive_weak")
    pipe.get(f"session:{session_id}:consecutive_strong")
    results = await pipe.execute()
    weak    = int(results[0]) if results[0] else 0
    strong  = int(results[1]) if results[1] else 0
    return weak, strong


async def _set_consecutive_counts(session_id: str, weak: int, strong: int) -> None:
    rds  = r._get_pool()
    pipe = rds.pipeline(transaction=False)
    pipe.setex(f"session:{session_id}:consecutive_weak",   settings.SESSION_TTL, weak)
    pipe.setex(f"session:{session_id}:consecutive_strong", settings.SESSION_TTL, strong)
    await pipe.execute()


async def _persist_turn_eval(
    session_id: str,
    turn_number: int,
    eval_scores: dict,
    signals: dict | None,
    elapsed_ms: int,
    flags: list[str],
    question_text: str = "",
    answer_text: str = "",
) -> None:
    """Background: write eval results to Postgres InterviewTurn row."""
    try:
        from app.db.persistence import update_turn_eval
        await update_turn_eval(
            session_id=session_id,
            turn_number=turn_number,
            eval_scores=eval_scores,
            signals=signals,
            question_text=question_text,
            answer_text=answer_text,
        )
        log.debug("db.eval_persisted", session_id=session_id, turn=turn_number)
    except Exception as exc:
        log.error("db.eval_persist_failed",
                  session_id=session_id, turn=turn_number, error=str(exc))
