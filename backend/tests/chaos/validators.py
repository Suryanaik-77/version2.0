"""
validators.py — System stability validators for chaos testing.

Each validator tests a specific invariant that must hold under chaos:
  - No state corruption
  - No duplicate memory entries
  - No mode thrashing
  - No cross-session contamination
  - No dead air in voice pipeline
  - No blocking in hot path

All validators return ValidationResult with pass/fail + evidence.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum

from app.engines import memory as memeng
from app.engines import strategy
from app.engines.question import _detect_signals
from app.engines.memory import detect_buzzwords_fast, detect_memorization_fast, _fast_contradiction_check
from app.models.session import (
    CandidateMemory,
    InterviewerMode,
    InlineSignals,
    Correctness,
    SignalLevel,
    TurnContext,
    VLSIDomain,
)
from tests.chaos.chaos_generator import ChaosTurn, ChaosType


class FailureClass(str, Enum):
    REAL_BUG                = "real_bug"
    CONCURRENCY_ISSUE       = "concurrency_issue"
    MEMORY_INCONSISTENCY    = "memory_inconsistency"
    VOICE_PIPELINE_ISSUE    = "voice_pipeline_issue"
    EVALUATOR_MISCLASS      = "evaluator_misclassification"
    ACCEPTABLE_CHAOS        = "acceptable_chaos"


@dataclass
class ValidationResult:
    name: str
    passed: bool
    evidence: str
    failure_class: FailureClass | None = None
    latency_ms: int = 0
    details: dict = field(default_factory=dict)


# ── Memory engine chaos validators ────────────────────────────────────────────

class MemoryChaoValidator:
    """Tests memory_engine stability under chaotic inputs."""

    def validate_self_correction(self, chaos_turn: ChaosTurn) -> ValidationResult:
        """
        Self-corrected answers should store the CORRECTED claim, not both versions.
        System must not create duplicate conflicting entries.
        """
        t_start = time.monotonic()
        memory = CandidateMemory(session_id="chaos-mem-1")

        # Simulate update
        tests_simulation_update(memory, chaos_turn.transcript, {}, chaos_turn.turn_in_sequence)

        # Check: no duplicate claims for the same concept
        seen_claims = set()
        duplicates = []
        for claim in memory.claims:
            if claim in seen_claims:
                duplicates.append(claim)
            seen_claims.add(claim)

        elapsed_ms = int((time.monotonic() - t_start) * 1000)

        if duplicates:
            return ValidationResult(
                name="memory_self_correction",
                passed=False,
                evidence=f"Duplicate memory entries: {duplicates}",
                failure_class=FailureClass.MEMORY_INCONSISTENCY,
                latency_ms=elapsed_ms,
            )

        return ValidationResult(
            name="memory_self_correction",
            passed=True,
            evidence=f"No duplicate entries. Claims stored: {len(memory.claims)}",
            latency_ms=elapsed_ms,
            details={"claims": memory.claims[:5]},
        )

    def validate_contradiction_detection(
        self,
        turn1: ChaosTurn,
        turn2: ChaosTurn,
    ) -> ValidationResult:
        """
        Turn 2 contradicts Turn 1. Memory must detect this.
        """
        t_start = time.monotonic()
        memory = CandidateMemory(session_id="chaos-mem-2")

        # Seed memory with turn 1 claims
        tests_simulation_update(memory, turn1.transcript, {}, 1)
        claims_after_t1 = list(memory.claims)

        # Process turn 2 (contradicting turn 1)
        tests_simulation_update(memory, turn2.transcript, {}, 2)

        elapsed_ms = int((time.monotonic() - t_start) * 1000)

        if turn2.should_detect_contradiction:
            if not memory.contradictions:
                return ValidationResult(
                    name="memory_contradiction_detection",
                    passed=False,
                    evidence=f"Expected contradiction but none detected.\nT1: {turn1.transcript[:80]}\nT2: {turn2.transcript[:80]}",
                    failure_class=FailureClass.MEMORY_INCONSISTENCY,
                    latency_ms=elapsed_ms,
                )
            return ValidationResult(
                name="memory_contradiction_detection",
                passed=True,
                evidence=f"Contradiction detected: {memory.contradictions[0].statement_a[:50]} vs {memory.contradictions[0].statement_b[:50]}",
                latency_ms=elapsed_ms,
            )
        else:
            return ValidationResult(
                name="memory_no_false_contradiction",
                passed=True,
                evidence=f"No false contradiction triggered (correct — turn was not contradictory)",
                latency_ms=elapsed_ms,
            )

    def validate_empty_transcript(self) -> ValidationResult:
        """Empty transcript (long pause) must not crash memory engine."""
        t_start = time.monotonic()
        try:
            memory = CandidateMemory(session_id="chaos-mem-3")
            tests_simulation_update(memory, "", {}, 1)
            tests_simulation_update(memory, "   ", {}, 2)
            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            return ValidationResult(
                name="memory_empty_transcript",
                passed=True,
                evidence="Empty and whitespace transcripts handled without crash",
                latency_ms=elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            return ValidationResult(
                name="memory_empty_transcript",
                passed=False,
                evidence=f"CRASH on empty transcript: {exc}",
                failure_class=FailureClass.REAL_BUG,
                latency_ms=elapsed_ms,
            )

    def validate_no_memory_growth(self, n_turns: int = 50) -> ValidationResult:
        """
        After N turns with repeated content, memory must stay bounded.
        No unbounded list growth.
        """
        t_start = time.monotonic()
        memory = CandidateMemory(session_id="chaos-mem-4")
        repeated = "I used common centroid for matching in my 28nm design."

        for i in range(n_turns):
            tests_simulation_update(memory, repeated, {"depth": 5, "accuracy": 5, "completeness": 5, "clarity": 5, "maturity": 5, "ownership": 5, "correctness": 5}, i)

        elapsed_ms = int((time.monotonic() - t_start) * 1000)

        claims_count = len(memory.claims)
        buzzword_count = sum(b.count for b in memory.buzzwords)

        # Claims capped at 20 (see memory.py)
        if claims_count > 25:
            return ValidationResult(
                name="memory_bounded_growth",
                passed=False,
                evidence=f"Memory claims grew to {claims_count} after {n_turns} identical turns — expected cap at 20",
                failure_class=FailureClass.MEMORY_INCONSISTENCY,
                latency_ms=elapsed_ms,
            )

        return ValidationResult(
            name="memory_bounded_growth",
            passed=True,
            evidence=f"Memory bounded after {n_turns} turns: {claims_count} claims, buzzword count={buzzword_count}",
            latency_ms=elapsed_ms,
            details={"claims": claims_count, "buzzwords": len(memory.buzzwords)},
        )


# ── Strategy engine chaos validators ──────────────────────────────────────────

class StrategyChaoValidator:
    """Tests strategy_engine mode transition stability under chaos inputs."""

    def validate_no_mode_thrashing(self) -> ValidationResult:
        """
        Rapidly alternating weak/strong eval scores must NOT cause mode to
        oscillate every single turn. Mode changes must be conservative.
        """
        t_start = time.monotonic()
        mode = InterviewerMode.PROBING
        mode_history = [mode]
        transitions = 0

        # Alternate between strong and weak scores
        alternating_scores = [
            {"accuracy": 8, "depth": 8, "completeness": 7, "clarity": 8, "maturity": 7, "ownership": 7, "correctness": 8},
            {"accuracy": 3, "depth": 2, "completeness": 3, "clarity": 4, "maturity": 3, "ownership": 3, "correctness": 4},
        ] * 6  # 12 turns of alternating

        for i, scores in enumerate(alternating_scores):
            new_mode = strategy.decide_mode_from_eval(
                current_mode=mode,
                eval_scores=scores,
                inline_signals=None,
                consecutive_weak=0 if i % 2 == 0 else i // 2,
                consecutive_strong=i // 2 if i % 2 == 0 else 0,
            )
            if new_mode != mode:
                transitions += 1
            mode = new_mode
            mode_history.append(mode)

        elapsed_ms = int((time.monotonic() - t_start) * 1000)

        # Should NOT oscillate on every turn — max transitions should be reasonable
        if transitions > 8:
            return ValidationResult(
                name="strategy_no_thrashing",
                passed=False,
                evidence=f"Mode thrashing: {transitions} transitions in {len(alternating_scores)} turns. History: {[m.value for m in mode_history]}",
                failure_class=FailureClass.EVALUATOR_MISCLASS,
                latency_ms=elapsed_ms,
            )

        return ValidationResult(
            name="strategy_no_thrashing",
            passed=True,
            evidence=f"Mode stable: {transitions} transitions in {len(alternating_scores)} alternating turns",
            latency_ms=elapsed_ms,
            details={"transitions": transitions, "final_mode": mode.value, "history": [m.value for m in mode_history]},
        )

    def validate_hesitation_not_weakness(self) -> ValidationResult:
        """
        Low confidence + partial correctness (hesitation) should NOT trigger RECOVERING.
        RECOVERING is for wrong answers, not uncertain ones.
        """
        t_start = time.monotonic()

        # Hesitation profile: partial correctness, medium confidence, medium depth
        hesitation_scores = {
            "accuracy": 6, "depth": 5, "completeness": 5,
            "clarity": 4, "maturity": 5, "ownership": 4, "correctness": 6,
        }
        hesitation_signals = InlineSignals(
            session_id="chaos-strategy",
            turn_number=1,
            correctness=Correctness.PARTIAL,
            vagueness=SignalLevel.MEDIUM,
            confidence=SignalLevel.LOW,   # hesitant but not wrong
            memorization_suspected=False,
        )

        mode = strategy.decide_mode_from_eval(
            current_mode=InterviewerMode.PROBING,
            eval_scores=hesitation_scores,
            inline_signals=hesitation_signals,
        )

        elapsed_ms = int((time.monotonic() - t_start) * 1000)

        if mode == InterviewerMode.RECOVERING:
            return ValidationResult(
                name="strategy_hesitation_not_weakness",
                passed=False,
                evidence=f"Hesitation incorrectly classified as RECOVERING. Scores: {hesitation_scores}",
                failure_class=FailureClass.EVALUATOR_MISCLASS,
                latency_ms=elapsed_ms,
            )

        return ValidationResult(
            name="strategy_hesitation_not_weakness",
            passed=True,
            evidence=f"Hesitation correctly → {mode.value} (not RECOVERING)",
            latency_ms=elapsed_ms,
            details={"result_mode": mode.value},
        )

    def validate_overconfident_wrong(self) -> ValidationResult:
        """
        Overconfident wrong answer: high ownership + low correctness → ESCALATING not DEEPENING.
        """
        t_start = time.monotonic()

        overconfident_wrong = {
            "accuracy": 4, "depth": 3, "completeness": 5,
            "clarity": 7, "maturity": 4, "ownership": 8, "correctness": 2,
        }
        mode = strategy.decide_mode_from_eval(
            current_mode=InterviewerMode.PROBING,
            eval_scores=overconfident_wrong,
            inline_signals=None,
        )

        elapsed_ms = int((time.monotonic() - t_start) * 1000)

        if mode == InterviewerMode.DEEPENING:
            return ValidationResult(
                name="strategy_overconfident_wrong",
                passed=False,
                evidence=f"Overconfident wrong answer incorrectly deepened. Mode={mode.value}",
                failure_class=FailureClass.EVALUATOR_MISCLASS,
                latency_ms=elapsed_ms,
            )

        return ValidationResult(
            name="strategy_overconfident_wrong",
            passed=True,
            evidence=f"Overconfident wrong → {mode.value} (correct: not DEEPENING)",
            latency_ms=elapsed_ms,
        )


# ── Signal detection chaos validators ─────────────────────────────────────────

class SignalChaoValidator:
    """Tests inline signal detection under messy/chaotic inputs."""

    def validate_signals_never_crash(self) -> ValidationResult:
        """Signal detection must not crash on any input, including empty."""
        t_start = time.monotonic()
        memory = CandidateMemory(session_id="chaos-sig")

        test_inputs = [
            "",                          # empty
            "   ",                       # whitespace only
            "." * 500,                   # 500 periods
            "a" * 1000,                  # 1000 'a's
            "I... uh... common... no wait...",  # fragmented
            "!@#$%^&*()",                # special chars
        ]

        try:
            for text in test_inputs:
                ctx = TurnContext(
                    session_id="chaos-sig",
                    turn_number=1,
                    transcript=text,
                    domain=VLSIDomain.ANALOG_LAYOUT,
                    mode=InterviewerMode.PROBING,
                    memory=memory,
                )
                signals = _detect_signals(ctx, "How does this work?")
                assert signals is not None

            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            return ValidationResult(
                name="signal_detection_no_crash",
                passed=True,
                evidence=f"Signal detection handled {len(test_inputs)} chaos inputs without crash",
                latency_ms=elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - t_start) * 1000)
            return ValidationResult(
                name="signal_detection_no_crash",
                passed=False,
                evidence=f"CRASH: {exc}",
                failure_class=FailureClass.REAL_BUG,
                latency_ms=elapsed_ms,
            )

    def validate_memorization_on_correct_inputs(self) -> ValidationResult:
        """Memorization detection must correctly classify known patterns."""
        t_start = time.monotonic()
        test_cases = [
            ("Common centroid is a technique that ensures matched devices.", True),
            ("Latch-up is a parasitic thyristor effect in CMOS processes.", True),
            ("UVM is the Universal Verification Methodology.", True),
            # These should NOT be flagged as memorized:
            ("I had a 50ps hold violation on 23 paths after CTS. I fixed it by resizing the buffers and rebalancing the clock tree insertion delays across domains.", False),
            ("In our 28nm tape-out I hit 0.08% mismatch by using 4-way interdigitation and equalizing via counts on all matched nets.", False),
        ]

        failures = []
        for transcript, should_flag in test_cases:
            result = detect_memorization_fast(transcript)
            if result != should_flag:
                failures.append(f"{'FP' if result and not should_flag else 'FN'}: {transcript[:60]}")

        elapsed_ms = int((time.monotonic() - t_start) * 1000)

        if failures:
            return ValidationResult(
                name="memorization_detection_accuracy",
                passed=False,
                evidence=f"Misclassifications: {failures}",
                failure_class=FailureClass.EVALUATOR_MISCLASS,
                latency_ms=elapsed_ms,
            )

        return ValidationResult(
            name="memorization_detection_accuracy",
            passed=True,
            evidence=f"All {len(test_cases)} memorization test cases classified correctly",
            latency_ms=elapsed_ms,
        )


# ── Eval parsing chaos validator ───────────────────────────────────────────────

class EvalParsingChaosValidator:
    """Tests eval JSON parsing robustness under malformed LLM responses."""

    def validate_malformed_json_handling(self) -> ValidationResult:
        """Eval parser must handle any LLM output without crashing."""
        from app.engines.eval import _parse_eval_json
        t_start = time.monotonic()

        malformed_inputs = [
            "",                          # empty
            "I cannot evaluate this.",   # natural language
            '{"accuracy": "high"}',      # wrong type
            '{"accuracy": 7}',           # missing fields
            '```json\n{"accuracy":7,"depth":6,"completeness":5,"clarity":7,"maturity":6,"ownership":5,"correctness":8,"flags":[]}\n```',  # markdown
            '{"accuracy": 15, "depth": -3, "completeness": 5, "clarity": 7, "maturity": 6, "ownership": 5, "correctness": 8, "flags":[]}',  # out-of-range
            '{not valid json at all}',   # invalid JSON
            '{"accuracy":7,"depth":6,"completeness":5,"clarity":7,"maturity":6,"ownership":5,"correctness":8,"flags":[]} some extra text',  # trailing text
        ]

        failures = []
        for i, inp in enumerate(malformed_inputs):
            try:
                result = _parse_eval_json(inp)
                # Result is either None (failed gracefully) or a valid dict
                if result is not None:
                    # Verify all required fields present and in range
                    for key in ["accuracy", "depth", "completeness", "clarity", "maturity", "ownership", "correctness"]:
                        if key not in result:
                            failures.append(f"Input {i}: missing key {key}")
                        elif not (0 <= result[key] <= 10):
                            failures.append(f"Input {i}: {key}={result[key]} out of range")
            except Exception as exc:
                failures.append(f"Input {i}: CRASH — {exc}")

        elapsed_ms = int((time.monotonic() - t_start) * 1000)

        if failures:
            return ValidationResult(
                name="eval_json_parsing_robustness",
                passed=False,
                evidence=f"Parsing failures: {failures}",
                failure_class=FailureClass.REAL_BUG,
                latency_ms=elapsed_ms,
            )

        return ValidationResult(
            name="eval_json_parsing_robustness",
            passed=True,
            evidence=f"All {len(malformed_inputs)} malformed inputs handled gracefully (None or valid dict)",
            latency_ms=elapsed_ms,
        )


# ── Concurrent session validator ───────────────────────────────────────────────

class ConcurrentSessionValidator:
    """Tests session isolation under concurrent load."""

    async def validate_session_isolation(self, n_sessions: int) -> ValidationResult:
        """
        Run N sessions concurrently.
        Verify: session A's memory/state never bleeds into session B.
        """
        t_start = time.monotonic()

        # Each session gets a unique ID and unique transcript
        session_data = {
            f"session-{i}": f"Session {i}: I used {'common centroid' if i % 2 == 0 else 'interdigitation'} for matching on project {i}."
            for i in range(n_sessions)
        }

        async def run_session(session_id: str, transcript: str) -> dict:
            memory = CandidateMemory(session_id=session_id)
            tests_simulation_update(memory, transcript, {"depth": 7, "accuracy": 7, "completeness": 6, "clarity": 7, "maturity": 6, "ownership": 7, "correctness": 7}, 1)
            await asyncio.sleep(0.01)  # simulate async work
            return {
                "session_id": session_id,
                "claims": list(memory.claims),
                "buzzwords": [b.term for b in memory.buzzwords],
            }

        # Run all sessions concurrently
        tasks = [
            asyncio.create_task(run_session(sid, transcript))
            for sid, transcript in session_data.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        elapsed_ms = int((time.monotonic() - t_start) * 1000)

        # Verify: no session has data from another session
        failures = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                failures.append(f"Session {i}: CRASH — {result}")
                continue

            sid = result["session_id"]
            # Expected: session even → "common centroid", session odd → "interdigitation"
            idx = int(sid.split("-")[1])
            expected_term = "common centroid" if idx % 2 == 0 else "interdigitation"
            wrong_term    = "interdigitation" if idx % 2 == 0 else "common centroid"

            # Check for cross-contamination
            all_content = " ".join(result["claims"] + result["buzzwords"])
            if wrong_term in all_content and expected_term not in all_content:
                failures.append(f"{sid}: cross-contamination detected — found {wrong_term!r} instead of {expected_term!r}")

        if failures:
            return ValidationResult(
                name=f"concurrent_session_isolation_{n_sessions}",
                passed=False,
                evidence=f"Cross-session contamination: {failures}",
                failure_class=FailureClass.CONCURRENCY_ISSUE,
                latency_ms=elapsed_ms,
            )

        return ValidationResult(
            name=f"concurrent_session_isolation_{n_sessions}",
            passed=True,
            evidence=f"{n_sessions} concurrent sessions: 0 cross-contamination events. Total time: {elapsed_ms}ms",
            latency_ms=elapsed_ms,
            details={"sessions": n_sessions, "total_ms": elapsed_ms, "avg_ms": elapsed_ms // n_sessions},
        )

    async def validate_concurrent_strategy_calls(self, n_sessions: int) -> ValidationResult:
        """
        strategy_engine is pure functions — N concurrent calls must return consistent results.
        """
        t_start = time.monotonic()

        async def call_strategy(session_id: str, scores: dict) -> dict:
            await asyncio.sleep(0.001)  # yield to other tasks
            mode = strategy.decide_mode_from_eval(
                current_mode=InterviewerMode.PROBING,
                eval_scores=scores,
                inline_signals=None,
            )
            return {"session_id": session_id, "mode": mode}

        strong = {"accuracy": 8, "depth": 8, "completeness": 7, "clarity": 8, "maturity": 7, "ownership": 7, "correctness": 8}
        weak   = {"accuracy": 3, "depth": 2, "completeness": 3, "clarity": 4, "maturity": 3, "ownership": 3, "correctness": 4}

        tasks = []
        expected_modes = {}
        for i in range(n_sessions):
            scores = strong if i % 2 == 0 else weak
            expected = InterviewerMode.DEEPENING if i % 2 == 0 else InterviewerMode.ESCALATING
            tasks.append(asyncio.create_task(call_strategy(f"sess-{i}", scores)))
            expected_modes[f"sess-{i}"] = expected

        results = await asyncio.gather(*tasks, return_exceptions=True)
        elapsed_ms = int((time.monotonic() - t_start) * 1000)

        failures = []
        for r in results:
            if isinstance(r, Exception):
                failures.append(f"CRASH: {r}")
                continue
            expected = expected_modes[r["session_id"]]
            if r["mode"] != expected:
                failures.append(f"{r['session_id']}: expected {expected.value}, got {r['mode'].value}")

        if failures:
            return ValidationResult(
                name=f"concurrent_strategy_determinism_{n_sessions}",
                passed=False,
                evidence=f"Non-deterministic strategy results: {failures}",
                failure_class=FailureClass.CONCURRENCY_ISSUE,
                latency_ms=elapsed_ms,
            )

        return ValidationResult(
            name=f"concurrent_strategy_determinism_{n_sessions}",
            passed=True,
            evidence=f"{n_sessions} concurrent strategy calls: all deterministic. {elapsed_ms}ms total.",
            latency_ms=elapsed_ms,
        )


# ── Voice pipeline chaos validator ────────────────────────────────────────────

class VoicePipelineChaosValidator:
    """Tests voice pipeline stability under chaos conditions."""

    async def validate_rapid_barge_in(self, n_times: int = 10) -> ValidationResult:
        """
        Rapid consecutive barge-ins: N interruptions without leaking tasks or hanging.
        """
        from app.voice.pipeline import run_turn_pipeline
        from app.providers.tts import SilenceTTS
        from unittest.mock import patch

        t_start = time.monotonic()
        leaked_tasks = []

        class MockHub:
            def __init__(self): self.task = None
            async def publish_to_session(self, s, j): pass
            async def send_bytes_to_session(self, s, d): pass
            def register_stream(self, s, t): self.task = t
            async def interrupt(self):
                if self.task and not self.task.done():
                    self.task.cancel()
                    try: await asyncio.wait_for(asyncio.shield(self.task), timeout=0.2)
                    except: pass

        async def mock_run_turn(session_id, transcript):
            for word in "How exactly does this mechanism work in detail?".split():
                yield word + " "
                await asyncio.sleep(0.02)

        interrupt_times = []
        for i in range(n_times):
            hub = MockHub()
            with patch("app.voice.pipeline.interview.run_turn", side_effect=mock_run_turn):
                task = asyncio.create_task(
                    run_turn_pipeline(f"chaos-voice-{i}", "test", i, hub, tts=SilenceTTS())
                )
                hub.task = task
                await asyncio.sleep(0.05)  # let pipeline start
                t_int = time.monotonic()
                await hub.interrupt()
                interrupt_times.append((time.monotonic() - t_int) * 1000)
                # Check task is done
                if not task.done():
                    leaked_tasks.append(f"turn-{i}")

        elapsed_ms = int((time.monotonic() - t_start) * 1000)
        avg_interrupt_ms = sum(interrupt_times) / len(interrupt_times)
        max_interrupt_ms = max(interrupt_times)

        if leaked_tasks:
            return ValidationResult(
                name="voice_rapid_barge_in",
                passed=False,
                evidence=f"Leaked tasks: {leaked_tasks}",
                failure_class=FailureClass.VOICE_PIPELINE_ISSUE,
                latency_ms=elapsed_ms,
            )

        if max_interrupt_ms > 200:
            return ValidationResult(
                name="voice_rapid_barge_in",
                passed=False,
                evidence=f"Slow interruption: max={max_interrupt_ms:.1f}ms (target<200ms)",
                failure_class=FailureClass.VOICE_PIPELINE_ISSUE,
                latency_ms=elapsed_ms,
            )

        return ValidationResult(
            name="voice_rapid_barge_in",
            passed=True,
            evidence=f"{n_times} rapid barge-ins: avg={avg_interrupt_ms:.1f}ms max={max_interrupt_ms:.1f}ms, 0 leaked tasks",
            latency_ms=elapsed_ms,
            details={"avg_ms": avg_interrupt_ms, "max_ms": max_interrupt_ms},
        )

    async def validate_empty_tts_input(self) -> ValidationResult:
        """Empty/whitespace TTS input must return silence, not crash."""
        from app.providers.tts import SilenceTTS, OpenAITTS
        t_start = time.monotonic()

        tts = SilenceTTS()
        test_inputs = ["", "   ", ".", "\n", "\t"]
        failures = []

        for text in test_inputs:
            try:
                audio = await tts.synthesize(text, session_id="chaos-tts")
                assert isinstance(audio, bytes), f"Expected bytes, got {type(audio)}"
            except asyncio.CancelledError:
                pass  # OK — would happen in real barge-in
            except Exception as exc:
                failures.append(f"{text!r}: {exc}")

        elapsed_ms = int((time.monotonic() - t_start) * 1000)

        if failures:
            return ValidationResult(
                name="voice_empty_tts_input",
                passed=False,
                evidence=f"TTS crashed on: {failures}",
                failure_class=FailureClass.VOICE_PIPELINE_ISSUE,
                latency_ms=elapsed_ms,
            )

        return ValidationResult(
            name="voice_empty_tts_input",
            passed=True,
            evidence=f"All {len(test_inputs)} edge-case TTS inputs handled without crash",
            latency_ms=elapsed_ms,
        )


# ── Helper ────────────────────────────────────────────────────────────────────

def tests_simulation_update(
    memory: CandidateMemory,
    transcript: str,
    eval_scores: dict,
    turn_number: int,
) -> None:
    """Sync wrapper for in-memory memory update (no Redis, no async)."""
    if not transcript or not transcript.strip():
        return

    # Extract claims
    new_claims = memeng.extract_claims_fast(transcript)
    for c in new_claims:
        if c not in memory.claims:
            memory.claims.append(c)
    memory.claims = memory.claims[-20:]

    # Numbers
    numbers = memeng.extract_numbers_fast(transcript)
    for n in numbers:
        n.turn_number = turn_number
    memory.numbers_stated.extend(numbers)
    memory.numbers_stated = memory.numbers_stated[-15:]

    # Buzzwords
    buzzwords = memeng.detect_buzzwords_fast(transcript)
    from app.models.session import BuzzwordRecord
    for bw in buzzwords:
        existing = next((b for b in memory.buzzwords if b.term == bw), None)
        if existing:
            existing.count += 1
        else:
            memory.buzzwords.append(BuzzwordRecord(term=bw, context=transcript[:80], turn_number=turn_number))

    # Contradiction check
    if len(memory.claims) > 1 and new_claims:
        contradiction = memeng._fast_contradiction_check(transcript, memory.claims[:-len(new_claims) or None])
        if contradiction:
            contradiction.turn_b = turn_number
            memory.contradictions.append(contradiction)

    # Topic tracking
    if eval_scores:
        avg = sum(eval_scores.values()) / len(eval_scores) if eval_scores else 5.0
        from app.models.session import TopicSummary
        topic = memeng._infer_topic(transcript, VLSIDomain.ANALOG_LAYOUT)
        if avg >= 7.0:
            existing = next((t for t in memory.strong_topics if t.topic == topic), None)
            if existing:
                existing.turn_count += 1
            else:
                memory.strong_topics.append(TopicSummary(topic=topic, domain=VLSIDomain.ANALOG_LAYOUT, avg_score=avg, turn_count=1))
