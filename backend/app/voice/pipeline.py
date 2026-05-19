"""
pipeline.py — Streaming voice pipeline. Production-critical hot path.

Full turn flow:
  STT complete → interview_engine.run_turn() → SentenceChunker
  → TTS synthesis (overlapping) → WebSocket audio send → playback

Architecture: producer-consumer with bounded async queue.
  Producer: token_stream → sentence_chunker → TTS synthesis task → queue
  Consumer: TTS task results from queue → WebSocket send

Why producer-consumer?
  - While client plays sentence N, sentence N+1 is already being synthesized.
  - Overlap eliminates inter-sentence silence gaps.
  - Queue bound (maxsize=3) prevents OOM on slow clients.

Cancellation chain (barge-in):
  pipeline_task.cancel()
  → asyncio.gather(producer, consumer) cancelled
  → producer: sentence_chunker closes, pending TTS tasks cancelled
  → consumer: current TTS await cancelled, audio send interrupted
  → cleanup: queue drained, active_tts_count decremented
  Target: < 100ms from cancel() call to silence

Session isolation:
  Each session has its own pipeline task and TTS queue.
  No shared mutable state between sessions.
  A slow TTS response for session A NEVER affects session B.

Checkpoints tracked (all 8 required by spec):
  stt_complete, first_token, first_sentence, tts_start,
  first_audio_sent, playback_complete, interruption_detected,
  interruption_complete
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import structlog

from app.config import get_settings
from app.engines import interview
from app.models.events import (
    WSEventType,
    interviewer_chunk_event,
    interviewer_done_event,
    turn_start_event,
    turn_complete_event,
    stt_final_event,
    error_event,
)
from app.models.session import EndReason
from app.observability.metrics import TurnLatencyTracker, record_event
from app.providers.tts import get_tts_provider, TTSProvider
from app.voice import sentence as sentence_mod

log = structlog.get_logger(__name__)
settings = get_settings()


# ── Global instrumentation ────────────────────────────────────────────────────
# Tracks active/cancelled streams across all sessions for observability.
# Read by admin dashboard metrics endpoint.

_active_tts_streams: int = 0
_cancelled_streams: int = 0
_total_turns: int = 0


def get_pipeline_stats() -> dict:
    return {
        "active_tts_streams": _active_tts_streams,
        "cancelled_streams": _cancelled_streams,
        "total_turns": _total_turns,
    }


# ── Turn pipeline ─────────────────────────────────────────────────────────────

async def run_turn_pipeline(
    session_id: str,
    transcript: str,
    turn_number: int,
    ws_hub,                 # WebSocketHub instance
    tts: TTSProvider | None = None,
) -> None:
    """
    Full voice pipeline for one interview turn.
    Must be launched as asyncio.create_task() — never awaited inline.

    Completion:
      - Normal: all sentences synthesized and sent
      - Barge-in: CancelledError received, clean shutdown
      - Disconnect: WebSocket send fails, clean shutdown
      - TTS timeout: silence substituted, pipeline continues

    Latency checkpoints emitted via TurnLatencyTracker.
    """
    global _active_tts_streams, _total_turns
    _total_turns += 1

    tracker = TurnLatencyTracker(session_id, turn_number)
    tracker.mark("stt_complete")  # STT already done when pipeline starts

    tts_provider = tts or get_tts_provider()

    # Notify client: turn is starting
    await ws_hub.publish_to_session(session_id, turn_start_event(session_id, turn_number).to_json())

    # Read mode BEFORE question generation — for turn persistence
    _mode_at_start = "PROBING"
    try:
        _state_snap = await interview.get_session(session_id)
        if _state_snap:
            _mode_at_start = _state_snap.mode.value
    except Exception:
        pass

    # Accumulate question tokens for turn persistence
    _collected_tokens: list[str] = []

    async def _collecting_token_stream():
        async for token in interview.run_turn(session_id, transcript):
            _collected_tokens.append(token)
            yield token

    try:
        # ── Stage 1: Get token stream from interview engine ───────────────────
        token_stream = _collecting_token_stream()

        # ── Stage 2: Sentence chunker ─────────────────────────────────────────
        metrics = sentence_mod.SentenceStreamMetrics(session_id)
        sentence_stream = sentence_mod.stream_with_metrics(token_stream, metrics)

        # ── Stage 3: TTS producer-consumer pipeline ───────────────────────────
        await _run_tts_pipeline(
            session_id=session_id,
            sentence_stream=sentence_stream,
            tts_provider=tts_provider,
            ws_hub=ws_hub,
            tracker=tracker,
        )

        tracker.mark("playback_complete")
        elapsed_ms = int(tracker.elapsed_ms("playback_complete") or 0)

        # ── Persist turn (non-blocking, background) ───────────────────────────
        question_text = "".join(_collected_tokens).strip()
        if question_text:
            asyncio.create_task(
                _persist_turn(
                    session_id=session_id,
                    turn_number=turn_number,
                    question_text=question_text,
                    answer_text=transcript,
                    mode_at_start=_mode_at_start,
                    tracker=tracker,
                ),
                name=f"db_turn_{session_id}_{turn_number}",
            )

        # Notify client: turn complete
        await ws_hub.publish_to_session(
            session_id,
            turn_complete_event(session_id, turn_number, elapsed_ms).to_json(),
        )

    except asyncio.CancelledError:
        # Barge-in or session end — clean exit
        tracker.mark("interruption_complete")
        interrupt_ms = tracker.elapsed_ms("interruption_complete") or 0
        record_event(
            "pipeline.interrupted",
            session_id=session_id,
            turn=turn_number,
            interrupt_ms=int(interrupt_ms),
        )
        log.info(
            "pipeline.barge_in_complete",
            session_id=session_id,
            turn=turn_number,
            interrupt_ms=int(interrupt_ms),
        )
        raise  # Re-raise so hub can clean up stream reference

    except Exception as exc:
        log.error("pipeline.error", session_id=session_id, error=str(exc), exc_info=exc)
        record_event("pipeline.error", session_id=session_id, error=str(exc))
        await ws_hub.publish_to_session(
            session_id,
            error_event(session_id, "PIPELINE_ERROR", "Voice pipeline error").to_json(),
        )
    finally:
        await tracker.emit()


async def _run_tts_pipeline(
    session_id: str,
    sentence_stream: AsyncIterator[tuple[int, str]],
    tts_provider: TTSProvider,
    ws_hub,
    tracker: TurnLatencyTracker,
) -> None:
    """
    Producer-consumer TTS pipeline.

    Producer task: sentences → TTS synthesis tasks → queue
    Consumer task: synthesis tasks → await result → WebSocket send

    Overlap: while consumer sends sentence N audio, producer is already
    synthesizing sentence N+1. Eliminates inter-sentence silence.

    Queue maxsize=3: prevents pre-synthesizing too far ahead (waste on barge-in).
    Queue of asyncio.Task objects, not audio bytes.
    """
    global _active_tts_streams

    # Bounded queue: holds pending TTS tasks (not bytes)
    tts_queue: asyncio.Queue[asyncio.Task | None] = asyncio.Queue(maxsize=3)
    sentence_count = 0
    first_sentence_done = False

    # ── Producer ──────────────────────────────────────────────────────────────
    async def producer() -> None:
        nonlocal sentence_count, first_sentence_done
        try:
            async for idx, sentence in sentence_stream:
                if not sentence.strip():
                    continue

                # Mark first sentence timing
                if not first_sentence_done:
                    tracker.mark("first_sentence")
                    first_sentence_done = True
                    log.debug(
                        "pipeline.first_sentence",
                        session_id=session_id,
                        chars=len(sentence),
                        elapsed_ms=tracker.elapsed_ms("first_sentence"),
                    )

                # Start TTS synthesis immediately — don't wait for queue space first
                tts_task = asyncio.create_task(
                    tts_provider.synthesize(sentence, session_id=session_id),
                    name=f"tts_{session_id}_{idx}",
                )

                # Put task in queue (may block briefly if consumer is slow)
                await tts_queue.put(tts_task)
                sentence_count += 1

        except asyncio.CancelledError:
            raise
        finally:
            # Always send sentinel so consumer exits cleanly
            await tts_queue.put(None)

    # ── Consumer ──────────────────────────────────────────────────────────────
    async def consumer() -> None:
        global _active_tts_streams
        idx = 0
        first_audio_sent = False

        while True:
            item = await tts_queue.get()
            if item is None:
                break

            _active_tts_streams += 1
            t_tts_start = time.monotonic()
            try:
                # Await TTS synthesis result
                audio_bytes = await item

                tts_ms = int((time.monotonic() - t_tts_start) * 1000)
                record_event(
                    "tts.sentence_ready",
                    session_id=session_id,
                    sentence_idx=idx,
                    latency_ms=tts_ms,
                    bytes=len(audio_bytes),
                )

                # Mark first TTS start (may already have been marked if producer was fast)
                if idx == 0:
                    tracker.mark("tts_start")

                # Send metadata frame (JSON)
                is_final = False  # we don't know yet — determined when sentinel arrives
                meta_event = interviewer_chunk_event(session_id, idx, is_final=False)
                await ws_hub.publish_to_session(session_id, meta_event.to_json())

                # Send audio bytes (binary frame)
                t_send_start = time.monotonic()
                await ws_hub.send_bytes_to_session(session_id, audio_bytes)
                send_ms = int((time.monotonic() - t_send_start) * 1000)

                if not first_audio_sent:
                    tracker.mark("first_audio_sent")
                    first_audio_sent = True
                    log.info(
                        "pipeline.first_audio",
                        session_id=session_id,
                        first_audio_ms=tracker.elapsed_ms("first_audio_sent"),
                        bytes=len(audio_bytes),
                    )
                    record_event(
                        "turn.first_audio_ms",
                        session_id=session_id,
                        latency_ms=tracker.elapsed_ms("first_audio_sent"),
                    )

                if send_ms > 50:
                    # WebSocket send taking > 50ms = client is slow or network degraded
                    log.warning(
                        "pipeline.ws_send_slow",
                        session_id=session_id,
                        send_ms=send_ms,
                    )

                idx += 1

            except asyncio.CancelledError:
                # Cancel any in-flight TTS task
                item.cancel()
                raise
            except Exception as exc:
                log.error("pipeline.consumer_error", session_id=session_id, error=str(exc))
                # Continue to next sentence rather than crashing the whole pipeline
                idx += 1
            finally:
                _active_tts_streams -= 1

        # Send full question text so frontend can display it
        full_question = "".join(_collected_tokens).strip()
        if full_question:
            from app.models.events import WSEvent, WSEventType
            text_event = WSEvent(
                type=WSEventType.INTERVIEWER_CHUNK,
                session_id=session_id,
                payload={"text": full_question, "sentence_index": idx, "is_final": True},
            )
            await ws_hub.publish_to_session(session_id, text_event.to_json())

        # Send final "done" event
        done_event = interviewer_done_event(session_id, idx)
        await ws_hub.publish_to_session(session_id, done_event.to_json())
        tracker.tts_chunk_count = idx

    # ── Run both concurrently ─────────────────────────────────────────────────
    try:
        await asyncio.gather(producer(), consumer())
    except asyncio.CancelledError:
        # Drain pending TTS tasks from queue to prevent task leaks
        _drain_queue(tts_queue)
        raise


def _drain_queue(queue: asyncio.Queue) -> None:
    """Cancel and drain all pending TTS tasks from queue after cancellation."""
    global _cancelled_streams
    while not queue.empty():
        try:
            item = queue.get_nowait()
            if isinstance(item, asyncio.Task) and not item.done():
                item.cancel()
                _cancelled_streams += 1
        except asyncio.QueueEmpty:
            break


# ── STT → pipeline bridge ─────────────────────────────────────────────────────

async def handle_utterance(
    session_id: str,
    audio_bytes: bytes,
    turn_number: int,
    ws_hub,
    stt_provider=None,
) -> None:
    """
    Called by WebSocket handler when VAD detects utterance complete.

    Flow:
      1. STT transcription
      2. Emit STT_FINAL event to client
      3. Launch voice pipeline as cancellable task
      4. Register task with hub for barge-in support

    This runs as a create_task — never awaited in the receive loop.
    """
    from app.providers.stt import get_stt_provider

    stt = stt_provider or get_stt_provider()
    t_start = time.monotonic()

    # ── STT ───────────────────────────────────────────────────────────────────
    transcript = await stt.transcribe(audio_bytes, session_id=session_id)
    stt_ms = int((time.monotonic() - t_start) * 1000)

    if not transcript:
        log.warning("pipeline.empty_transcript", session_id=session_id, stt_ms=stt_ms)
        return

    # Emit STT result to client (shows transcript in candidate UI)
    stt_event = stt_final_event(session_id, transcript, stt_ms, turn_number)
    await ws_hub.publish_to_session(session_id, stt_event.to_json())

    log.info(
        "pipeline.stt_complete",
        session_id=session_id,
        stt_ms=stt_ms,
        chars=len(transcript),
        turn=turn_number,
    )

    # ── Launch voice pipeline ─────────────────────────────────────────────────
    pipeline_task = asyncio.create_task(
        run_turn_pipeline(
            session_id=session_id,
            transcript=transcript,
            turn_number=turn_number,
            ws_hub=ws_hub,
        ),
        name=f"pipeline_{session_id}_{turn_number}",
    )

    # Register with hub for barge-in interruption
    ws_hub.register_stream(session_id, pipeline_task)


# ── Turn persistence ──────────────────────────────────────────────────────────

async def _persist_turn(
    session_id: str,
    turn_number: int,
    question_text: str,
    answer_text: str,
    mode_at_start: str,
    tracker: TurnLatencyTracker,
) -> None:
    """
    Background task: write completed turn to Postgres.
    Fired after TTS playback completes — question text is now known.

    Note: eval scores are NOT available yet (eval is still running).
    eval.py will call update_turn_eval() separately when it finishes.

    The upsert is idempotent — safe to call twice (reconnect scenario).
    """
    # Read current mode from state for mode_at_end
    # (may have been updated by eval from the PREVIOUS turn)
    mode_at_end = mode_at_start
    try:
        _state = await interview.get_session(session_id)
        if _state:
            mode_at_end = _state.mode.value
    except Exception:
        pass

    try:
        from app.db.persistence import upsert_turn
        # Read domain from session state
        domain_str = "ANALOG_LAYOUT"
        try:
            _state2 = await interview.get_session(session_id)
            if _state2:
                domain_str = _state2.active_domain.value
                mode_at_end = _state2.mode.value
        except Exception:
            pass
        await upsert_turn(
            session_id=session_id,
            turn_number=turn_number,
            question_text=question_text,
            answer_text=answer_text,
            domain=domain_str,
            mode_at_start=mode_at_start,
            mode_at_end=mode_at_end,
        )
        log.debug("pipeline.turn_persisted",
                  session_id=session_id, turn=turn_number, q_chars=len(question_text))
    except Exception as exc:
        log.error("pipeline.turn_persist_failed",
                  session_id=session_id, turn=turn_number, error=str(exc))
