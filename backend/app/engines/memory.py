"""
memory_engine.py — Per-session candidate memory.

Ownership: this module exclusively reads/writes session memory in Redis.
No other module writes to CandidateMemory.

Performance contract:
- getSnapshot(): O(1) — single Redis GET, ~2ms
- compress_for_injection(): O(n) where n is small (< 20 items per category), < 1ms
- detectContradiction(): keyword-level scan, < 5ms — NO LLM in hot path
- update(): async, off hot path — may call LLM for deep contradiction check

Memory injection budget: < 150 tokens per turn.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime

import structlog

from app.config import get_settings
from app.core import redis as r
from app.models.session import (
    CandidateMemory,
    Contradiction,
    TopicSummary,
    BuzzwordRecord,
    ConfidenceShift,
    NumberRecord,
    VLSIDomain,
    InlineSignals,
    SignalLevel,
)
from app.observability.metrics import record_event

log = structlog.get_logger(__name__)
settings = get_settings()

# ── Known VLSI buzzwords (detect without mechanism = flag) ────────────────────
_VLSI_BUZZWORDS = frozenset([
    "common centroid", "interdigitation", "matching", "latch-up", "esd",
    "guard ring", "parasitics", "extraction", "lvs", "drc",
    "timing closure", "cts", "clock tree", "ir drop", "electromigration",
    "congestion", "eco", "floorplan", "place and route", "pnr",
    "uvm", "scoreboard", "assertion", "coverage", "constrained random",
    "functional coverage", "code coverage",
])

# Patterns that suggest memorized definitions
_MEMORIZED_PATTERNS = [
    r"^[a-z ]+\s+is\s+(a|an|the)\s+\w+\s+that\s",
    r"^in\s+(digital|analog|vlsi|ic)\s+design,?\s+\w+\s+refers?\s+to",
    r"stands for ",
]


# ── Public interface ──────────────────────────────────────────────────────────

async def get_snapshot(session_id: str) -> CandidateMemory:
    """
    Hot-path read. Single Redis GET. Returns empty memory if not found.
    Called at start of every turn — must be fast.
    Latency budget: < 5ms.
    """
    memory = await r.get_memory(session_id)
    if memory is None:
        return CandidateMemory(session_id=session_id)
    return memory


def compress_for_injection(memory: CandidateMemory) -> str:
    """
    Compress memory into a short text block for LLM prompt injection.

    Budget: < 150 tokens (enforced by what we include).
    Priority order:
      1. Unresolved contradictions  (critical — must surface)
      2. Weak topics                (drives follow-up focus)
      3. Strong claims              (drives pressure/deepening)
      4. Recent specific claims     (drives reference questions)

    Returns empty string if memory is empty — no injection needed on turn 1.
    """
    parts: list[str] = []

    # 1. Unresolved contradictions — max 2
    unresolved = [c for c in memory.contradictions if not c.resolved][:2]
    if unresolved:
        items = "; ".join(
            f'said "{_truncate(c.statement_a, 40)}" then "{_truncate(c.statement_b, 40)}"'
            for c in unresolved
        )
        parts.append(f"UNRESOLVED CONTRADICTIONS: {items}")

    # 2. Weak topics — max 2, sorted by lowest score first
    weak = sorted(memory.weak_topics, key=lambda t: t.avg_score)[:2]
    if weak:
        parts.append("WEAK AREAS: " + ", ".join(
            f"{t.topic} (avg {t.avg_score:.1f}/10)" for t in weak
        ))

    # 3. Strong topics — max 2 (for deepening/pressure targets)
    strong = sorted(memory.strong_topics, key=lambda t: t.avg_score, reverse=True)[:2]
    if strong:
        parts.append("STRONG AREAS: " + ", ".join(t.topic for t in strong))

    # 4. Recent specific claims — last 3 only
    recent_claims = memory.claims[-3:]
    if recent_claims:
        parts.append("CANDIDATE CLAIMED: " + "; ".join(f'"{_truncate(c, 60)}"' for c in recent_claims))

    # 5. Repeated buzzwords (for pressure targeting)
    repeated = [b for b in memory.buzzwords if b.count >= 2][:2]
    if repeated:
        parts.append("REPEATED WITHOUT MECHANISM: " + ", ".join(b.term for b in repeated))

    return "\n".join(parts)


def detect_buzzwords_fast(transcript: str) -> list[str]:
    """
    Fast keyword scan for known VLSI buzzwords in transcript.
    Used during inline signal detection — no LLM needed.
    O(n) where n = number of known buzzwords (fixed, ~30).
    """
    transcript_lower = transcript.lower()
    return [bw for bw in _VLSI_BUZZWORDS if bw in transcript_lower]


def detect_memorization_fast(transcript: str) -> bool:
    """
    Heuristic memorization detection — no LLM.
    Checks: very short answers, definition-style phrasing.
    """
    words = transcript.split()
    if len(words) < 25:
        return True  # Very short answer
    text_lower = transcript.lower().strip()
    return any(re.search(p, text_lower) for p in _MEMORIZED_PATTERNS)


def extract_claims_fast(transcript: str) -> list[str]:
    """
    Extract first-person technical claims from transcript.
    Simple heuristic: sentences with "I" + technical verb.
    Used for memory updates — off hot path.
    """
    claim_patterns = [
        r"I (designed|built|implemented|taped out|owned|led|debugged|wrote|ran|created|worked on) .+",
        r"I (have|had) .+ (experience|years?) .+",
        r"In my .+ project.+",
        r"We (used|implemented|designed|built) .+",
    ]
    claims = []
    for sentence in transcript.replace(".", ". ").split(". "):
        for pattern in claim_patterns:
            if re.search(pattern, sentence, re.IGNORECASE):
                claims.append(sentence.strip())
                break
    return claims[:3]  # cap at 3 per turn


def extract_numbers_fast(transcript: str) -> list[NumberRecord]:
    """Extract stated metrics/numbers for memory tracking."""
    # Match: "<number><unit> <context_word>"
    pattern = r'(\d+(?:\.\d+)?)\s*(ps|ns|us|ms|mv|v|mhz|ghz|nm|um|%|k|m|gb|mb)\b'
    results = []
    for match in re.finditer(pattern, transcript.lower()):
        # Get surrounding context (10 words before/after)
        start = max(0, match.start() - 50)
        end = min(len(transcript), match.end() + 50)
        context = transcript[start:end].strip()
        results.append(NumberRecord(
            value=f"{match.group(1)}{match.group(2)}",
            context=context,
            turn_number=0,  # caller sets this
        ))
    return results[:4]  # cap at 4 per turn


# ── Async update (off hot path) ───────────────────────────────────────────────

async def update_from_eval(
    session_id: str,
    transcript: str,
    domain: VLSIDomain,
    eval_scores: dict,
    inline_signals: InlineSignals,
    turn_number: int,
    last_question: str = "",
) -> None:
    """
    Full memory update after eval completes.
    NEVER called from hot path. Runs ~2s after turn starts.

    Updates:
    - Weak/strong topic tracking from eval scores
    - Claims extraction
    - Buzzword count update
    - Number extraction
    - Inline signal integration
    """
    memory = await get_snapshot(session_id)

    avg_score = sum(eval_scores.values()) / len(eval_scores) if eval_scores else 5.0
    topic_key = _infer_topic(transcript, domain)

    # Update topic strength tracking
    _update_topic(memory, topic_key, domain, avg_score)

    # Extract and store new claims
    new_claims = extract_claims_fast(transcript)
    for claim in new_claims:
        if claim not in memory.claims:
            memory.claims.append(claim)
    memory.claims = memory.claims[-20:]  # cap at 20 total claims

    # Buzzword tracking
    buzzwords_found = detect_buzzwords_fast(transcript)
    for bw in buzzwords_found:
        existing = next((b for b in memory.buzzwords if b.term == bw), None)
        if existing:
            existing.count += 1
        else:
            memory.buzzwords.append(BuzzwordRecord(
                term=bw,
                context=_truncate(transcript, 80),
                turn_number=turn_number,
            ))

    # Number extraction
    numbers = extract_numbers_fast(transcript)
    for n in numbers:
        n.turn_number = turn_number
    memory.numbers_stated.extend(numbers)
    memory.numbers_stated = memory.numbers_stated[-15:]  # cap

    # Contradiction check (fast, no LLM)
    contradiction = _fast_contradiction_check(transcript, memory.claims[:-len(new_claims) or None])
    if contradiction:
        memory.contradictions.append(contradiction)

    memory.last_updated = datetime.utcnow()
    await r.set_memory(memory)
    record_event("memory.updated", session_id=session_id, turn_number=turn_number)


async def store_inline_signals(session_id: str, signals: InlineSignals) -> None:
    """Called non-blocking during question generation. Stores for strategy_engine."""
    await r.set_inline_signals(signals)


async def mark_contradiction_resolved(session_id: str, turn_a: int, turn_b: int) -> None:
    """Called by interview_engine when a contradiction is surfaced in a question."""
    memory = await get_snapshot(session_id)
    for c in memory.contradictions:
        if c.turn_a == turn_a and c.turn_b == turn_b:
            c.resolved = True
    await r.set_memory(memory)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _update_topic(
    memory: CandidateMemory,
    topic: str,
    domain: VLSIDomain,
    score: float,
) -> None:
    is_strong = score >= 7.0
    target_list = memory.strong_topics if is_strong else memory.weak_topics

    existing = next((t for t in target_list if t.topic == topic), None)
    if existing:
        # Exponential moving average
        existing.avg_score = (existing.avg_score * 0.7) + (score * 0.3)
        existing.turn_count += 1
    else:
        target_list.append(TopicSummary(
            topic=topic,
            domain=domain,
            avg_score=score,
            turn_count=1,
        ))

    # Reclassify if needed
    _reclassify_topics(memory)


def _reclassify_topics(memory: CandidateMemory) -> None:
    """Move topics between weak/strong lists if their score changed enough."""
    newly_strong = [t for t in memory.weak_topics if t.avg_score >= 7.0]
    newly_weak   = [t for t in memory.strong_topics if t.avg_score < 6.0]

    for t in newly_strong:
        memory.weak_topics.remove(t)
        memory.strong_topics.append(t)

    for t in newly_weak:
        memory.strong_topics.remove(t)
        memory.weak_topics.append(t)

    # Cap list sizes
    memory.weak_topics   = sorted(memory.weak_topics,   key=lambda t: t.avg_score)[:10]
    memory.strong_topics = sorted(memory.strong_topics, key=lambda t: t.avg_score, reverse=True)[:10]


def _fast_contradiction_check(
    new_statement: str,
    prior_claims: list[str],
) -> Contradiction | None:
    """
    Fast keyword-based contradiction detection. No LLM.
    Catches explicit negation patterns: "not", "never", "doesn't",
    combined with a topic that appears in a prior claim.

    Limitations: won't catch semantic contradictions. LLM-based deep
    check can be added as async enrichment in Phase 5.
    """
    if not prior_claims:
        return None

    new_lower = new_statement.lower()
    negation_words = ["not ", "never ", "doesn't ", "don't ", "cannot ", "can't ", "no "]

    for claim in prior_claims[-8:]:  # check against last 8 claims only
        claim_lower = claim.lower()
        # Extract key nouns from prior claim (simple: words > 4 chars)
        claim_keywords = {w for w in claim_lower.split() if len(w) > 4}
        # Check if new statement negates something from prior claim
        overlap = any(kw in new_lower for kw in claim_keywords)
        has_negation = any(neg in new_lower for neg in negation_words)

        if overlap and has_negation:
            return Contradiction(
                statement_a=_truncate(claim, 100),
                statement_b=_truncate(new_statement, 100),
                turn_a=0,   # caller should set actual turn numbers
                turn_b=0,
            )
    return None


def _infer_topic(transcript: str, domain: VLSIDomain) -> str:
    """Infer primary topic from transcript for memory tracking."""
    transcript_lower = transcript.lower()

    topic_keywords = {
        # Analog Layout
        "matching":        ["match", "mismatch", "common centroid", "interdigitation"],
        "parasitics":      ["parasitic", "capacitance", "resistance", "coupling"],
        "latch-up":        ["latch-up", "latchup", "substrate", "well"],
        "esd":             ["esd", "electrostatic", "protection"],
        "guard rings":     ["guard ring", "isolation"],
        # Physical Design
        "clock tree":      ["cts", "clock tree", "clock skew", "clock latency"],
        "timing":          ["timing", "setup", "hold", "slack", "violation"],
        "ir drop":         ["ir drop", "voltage drop", "power grid"],
        "congestion":      ["congestion", "routing", "via", "density"],
        "floorplanning":   ["floorplan", "floorplanning", "placement"],
        # Design Verification
        "uvm":             ["uvm", "agent", "driver", "monitor", "sequencer"],
        "coverage":        ["coverage", "functional coverage", "code coverage"],
        "assertions":      ["assertion", "sva", "property", "sequence"],
        "debugging":       ["debug", "waveform", "simulation fail"],
    }

    for topic, keywords in topic_keywords.items():
        if any(kw in transcript_lower for kw in keywords):
            return topic

    return f"{domain.value.lower().replace('_', ' ')} general"


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 3] + "..."
