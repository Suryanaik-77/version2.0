"""
interview_engine.py — Session state authority and turn coordinator.

This module owns:
  - Session state reads (via Redis)
  - Turn orchestration
  - Hot-path timing

This module does NOT own:
  - Question content (question_engine)
  - Scoring (eval_engine)
  - Mode decisions (strategy_engine)
  - Memory content (memory_engine)

The hot path for a single turn:
  1. Parallel read: state + memory from Redis         ~5ms
  2. Increment turn counter                           ~3ms
  3. Build TurnContext                                ~0ms
  4. Start eval as background task                   ~0ms (fired immediately)
  5. Delegate to question_engine.stream()            → yields tokens to caller
  6. question_engine emits signals internally        (background, non-blocking)

Total overhead before first token: ~8ms
First token target: 400ms (LLM-bound)
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import structlog

from app.config import get_settings
from app.core import redis as r
from app.core.session import (
    get_session,
    increment_turn,
    SessionNotFoundError,
    SessionEndedError,
)
from app.engines import memory as mem
from app.engines import question as qeng
from app.engines import eval as eeng
from app.models.session import (
    TurnContext,
    CandidateMemory,
    InterviewerMode,
    VLSIDomain,
)
from app.observability.metrics import TurnLatencyTracker, record_event

log = structlog.get_logger(__name__)
settings = get_settings()


async def run_turn(
    session_id: str,
    transcript: str,
) -> AsyncIterator[str]:
    """
    Hot path entry point. Called by WebSocket turn handler with finalized transcript.

    Returns an async generator of tokens.
    The caller (WebSocket handler) pipes these to:
      sentence_chunker → TTS → audio playback

    Execution order:
      1. Parallel Redis reads (state + memory)
      2. Build context
      3. Fire eval as background task (runs in parallel with question gen)
      4. Stream tokens from question_engine
      5. (question_engine emits inline signals during streaming)
    """
    tracker = TurnLatencyTracker(session_id, turn_number=0)
    t_start = time.monotonic()

    # ── Step 1: Parallel reads from Redis ────────────────────────────────────
    # Both are independent — run concurrently.
    state, memory = await asyncio.gather(
        get_session(session_id),
        mem.get_snapshot(session_id),
    )

    if not state:
        raise SessionNotFoundError(session_id)
    if not state.is_active:
        raise SessionEndedError(session_id)

    # ── Step 2: Increment turn counter ───────────────────────────────────────
    turn_number = await increment_turn(session_id)
    tracker.turn_number = turn_number

    # ── Step 3: Build TurnContext ─────────────────────────────────────────────
    # Prior answers: last 3 turns for reference (questions only)
    recent_turns = await r.get_recent_turns(session_id, n=3)
    prior_answers = [t.get("question", "") for t in recent_turns]

    ctx = TurnContext(
        session_id=session_id,
        turn_number=turn_number,
        transcript=transcript,
        domain=state.active_domain,
        mode=state.mode,
        memory=memory,
        resume=state.resume,
        prior_answers=prior_answers,
    )

    # ── Step 4: Fire eval as background task ──────────────────────────────────
    # Eval starts IMMEDIATELY — runs in parallel with question generation.
    # Never awaited here. Never affects current turn.
    last_question = prior_answers[0] if prior_answers else ""
    asyncio.create_task(
        eeng.run_async_eval(
            session_id=session_id,
            transcript=transcript,
            domain=state.active_domain,
            last_question=last_question,
            turn_number=turn_number,
            inline_signals=None,  # populated by question_engine during streaming
        ),
        name=f"eval_{session_id}_{turn_number}",
    )

    overhead_ms = (time.monotonic() - t_start) * 1000
    record_event(
        "turn.overhead_ms",
        session_id=session_id,
        turn=turn_number,
        overhead_ms=int(overhead_ms),
        mode=state.mode.value,
    )
    log.info(
        "turn.started",
        session_id=session_id,
        turn=turn_number,
        mode=state.mode.value,
        overhead_ms=int(overhead_ms),
    )

    # ── Step 5: Stream tokens from question_engine ────────────────────────────
    # This is where we spend the most time (LLM-bound).
    # The generator yields tokens as the LLM produces them.
    # We pass the tracker so question_engine can mark timing checkpoints.
    try:
        async for token in qeng.stream(ctx, tracker=tracker):
            yield token
    finally:
        tracker.mark("turn_complete")
        await tracker.emit()

        log.info(
            "turn.complete",
            session_id=session_id,
            turn=turn_number,
            first_token_ms=tracker.elapsed_ms("first_token"),
            total_ms=tracker.elapsed_ms("turn_complete"),
        )

        # Periodic memory flush every 5 turns
        if turn_number % 5 == 0:
            from app.core.session import periodic_memory_flush
            asyncio.create_task(
                periodic_memory_flush(session_id),
                name=f"mem_flush_{session_id}",
            )


# ── Turn handler for WebSocket layer ─────────────────────────────────────────

async def handle_turn_for_websocket(
    session_id: str,
    transcript: str,
    emit_text_token: callable,
    emit_turn_complete: callable,
) -> None:
    """
    Adapter for WebSocket integration.
    Consumes run_turn() generator and emits tokens via WebSocket.

    Called as create_task() from WebSocket handler — never awaited inline.

    Phase 3 note: This is where TTS sentence chunking inserts itself.
    For Phase 2, we emit raw text tokens. Phase 3 replaces the text emission
    with voice_pipeline.synthesize() per sentence.
    """
    try:
        async for token in run_turn(session_id, transcript):
            # Phase 2: emit text token via WebSocket
            # Phase 3: pipe to sentence_chunker → TTS → audio bytes
            await emit_text_token(token)

        await emit_turn_complete()

    except SessionNotFoundError as exc:
        log.warning("turn.session_not_found", session_id=session_id)
    except SessionEndedError:
        log.info("turn.session_ended", session_id=session_id)
    except asyncio.CancelledError:
        # Barge-in — clean exit
        log.info("turn.cancelled_barge_in", session_id=session_id)
        raise
    except Exception as exc:
        log.error("turn.unexpected_error", session_id=session_id, error=str(exc), exc_info=exc)
        record_event("turn.error", session_id=session_id, error=str(exc))


# ── Opening question (turn 0) ─────────────────────────────────────────────────

async def generate_opening(session_id: str) -> AsyncIterator[str]:
    """
    Generates the opening interviewer question.
    Not a warm welcome — immediately domain-focused.

    Format: brief intro + first real question.
    Does NOT use run_turn() since there's no transcript yet.
    """
    state = await get_session(session_id)
    if not state:
        raise SessionNotFoundError(session_id)

    # Personalize opening with resume + interviewer archetype
    from app.engines.prompts import _pick_archetype
    _raw_name = (state.resume.candidate_name or "").strip()
    name = _raw_name.split()[0] if _raw_name and _raw_name != "Candidate" else ""
    archetype = _pick_archetype(session_id)
    domain_key = state.active_domain.value

    # Each archetype opens differently
    _OPENINGS = {
        "ranjitha": {
            "ANALOG_LAYOUT": f"{'Hi ' + name + ', ' if name else ''}let's get started. Walk me through the most complex analog layout you've personally designed — matching strategy and why.",
            "PHYSICAL_DESIGN": f"{'Hi ' + name + ', ' if name else ''}let's get into it. Last physical design you owned — what was the hardest timing closure challenge?",
            "DESIGN_VERIFICATION": f"{'Hi ' + name + ', ' if name else ''}let's go. Most complex verification environment you've built — coverage model and how you closed it.",
        },
        "vikram": {
            "ANALOG_LAYOUT": f"{'Hello ' + name + ', ' if name else ''}I'd like to understand your background. Tell me about an analog block you laid out end to end — what were the critical matching requirements?",
            "PHYSICAL_DESIGN": f"{'Hello ' + name + ', ' if name else ''}let's start with your experience. Walk me through a chip you worked on — from floorplan to signoff, what was the most interesting challenge?",
            "DESIGN_VERIFICATION": f"{'Hello ' + name + ', ' if name else ''}take me through a testbench you built from scratch. What was the DUT, and how did you approach coverage?",
        },
        "priya": {
            "ANALOG_LAYOUT": f"{'Hi ' + name + '! ' if name else ''}So, tell me about your analog layout experience — pick a project you're proud of and walk me through the challenges.",
            "PHYSICAL_DESIGN": f"{'Hi ' + name + '! ' if name else ''}I'd love to hear about your PD work. Pick a block or chip and tell me what made it tricky.",
            "DESIGN_VERIFICATION": f"{'Hi ' + name + '! ' if name else ''}Let's dive in. Tell me about a verification challenge that really tested you — what was the bug and how did you find it?",
        },
    }

    archetype_openings = _OPENINGS.get(archetype, _OPENINGS["ranjitha"])
    opening = archetype_openings.get(domain_key, f"{'Hi ' + name + ', ' if name else ''}tell me about the most technically challenging project you've worked on.")

    # Stream character by character for natural feel
    # (In Phase 3 this goes to TTS — for now yield as single token)
    yield opening

    # Record opening turn in Redis
    await r.push_turn_summary(session_id, {
        "turn": 0,
        "question": opening,
    })

    # Persist opening turn to Postgres in background
    asyncio.create_task(
        _persist_opening_turn(session_id, opening, domain_key),
        name=f"db_opening_turn_{session_id}",
    )


async def _persist_opening_turn(session_id: str, question: str, domain: str) -> None:
    """Persist turn 0 (the opening question) to Postgres."""
    try:
        from app.db.persistence import upsert_turn
        await upsert_turn(
            session_id=session_id,
            turn_number=0,
            question_text=question,
            answer_text="",          # no answer yet for turn 0
            domain=domain,
            mode_at_start="PROBING",
            mode_at_end="PROBING",
        )
    except Exception as exc:
        log.warning("interview.opening_turn_persist_failed",
                    session_id=session_id, error=str(exc))
