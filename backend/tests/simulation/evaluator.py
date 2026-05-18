"""
evaluator.py — Realism evaluation engine.

Evaluates generated interviewer questions against behavioral rules.
Returns PASS/FAIL with evidence for each check.

The interviewer FAILS realism if it:
  - Contains any blacklisted AI phrase
  - Praises a weak answer
  - Accepts vague answers without mechanism probing
  - Asks disconnected questions
  - Repeats a recent question
  - Ignores a surfaced contradiction
  - Fails to verify ownership claims
  - Sounds overly supportive

All checks are deterministic (regex/keyword) for reproducibility.
LLM-as-judge for humanness score is optional (more expensive).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import NamedTuple


# ── AI Phrase Blacklist ────────────────────────────────────────────────────────
# Any of these appearing in an interviewer question = FAIL

AI_PHRASE_BLACKLIST: list[tuple[str, str]] = [
    # (pattern, description)
    (r'\bgreat\s+answer\b',               "praises answer as 'great'"),
    (r'\bgood\s+answer\b',                "praises answer as 'good'"),
    (r'\bexcellent\s+answer\b',           "praises answer as 'excellent'"),
    (r'\bgreat\s+explanation\b',          "praises explanation"),
    (r'\bwonderful\b',                    "uses 'wonderful'"),
    (r'\bcan\s+you\s+elaborate\b',        "generic 'can you elaborate' prompt"),
    (r'\bcan\s+you\s+tell\s+me\s+more\b', "generic 'tell me more' prompt"),
    (r'\bgood\s+point\b',                 "affirms with 'good point'"),
    (r'\bthanks\s+for\s+sharing\b',       "uses 'thanks for sharing'"),
    (r'\bthank\s+you\s+for\s+(sharing|that|your)\b', "thanks candidate"),
    (r'\bthat\'?s?\s+(great|good|excellent|interesting|wonderful)\b', "generic AI praise"),
    (r'\bi\s+see\s*[,\.]',               "filler 'I see'"),
    (r'^i\s+see\b',                       "starts with 'I see'"),
    (r'\bunderstood\b',                   "uses 'understood'"),
    (r'\babsolutely\b',                   "uses 'absolutely'"),
    (r'\bcertainly\b',                    "uses 'certainly'"),
    (r'\bof\s+course\b',                  "uses 'of course'"),
    (r'\bfeel\s+free\b',                  "uses 'feel free'"),
    (r'\bdon\'?t\s+hesitate\b',           "uses 'don't hesitate'"),
    (r'\bgreat\s+job\b',                  "praises with 'great job'"),
    (r'\bwell\s+done\b',                  "praises with 'well done'"),
    (r'\bimpressive\b',                   "uses 'impressive'"),
    (r'\bfantastic\b',                    "uses 'fantastic'"),
    (r'\bperfect\b',                      "uses 'perfect'"),
    (r'\bthat\s+makes\s+sense\b',         "affirms with 'that makes sense'"),
    (r'\bright,\s+so\b',                  "filler 'right, so'"),
    (r'\bexactly\s*[,\.]',               "affirms with 'exactly'"),
    (r'^exactly\b',                       "starts with 'exactly'"),
    (r'\bgreat,\s',                       "starts clause with 'great,'"),
    (r'\byes,\s+',                        "affirms with 'yes,'"),
]

# Compile for speed
_COMPILED_BLACKLIST = [
    (re.compile(pattern, re.IGNORECASE), desc)
    for pattern, desc in AI_PHRASE_BLACKLIST
]

# ── Behavioral check patterns ─────────────────────────────────────────────────

# Mechanism probe: question asks HOW/WHY the thing works
_MECHANISM_WORDS = re.compile(
    r'\b(how\s+exactly|how\s+does|how\s+would|what\s+mechanism|explain\s+how|'
    r'walk\s+me\s+through|what\s+causes|why\s+does|what\s+happens|'
    r'what\s+physically|at\s+the\s+device\s+level|physically\s+speaking)\b',
    re.IGNORECASE,
)

# Ownership probe: question challenges "I did X" claims
_OWNERSHIP_WORDS = re.compile(
    r'\b(you\s+specifically|how\s+did\s+you|what\s+did\s+you|'
    r'your\s+specific|walk\s+me\s+through\s+how\s+you|'
    r'in\s+your\s+(specific\s+)?case|what\s+was\s+your)\b',
    re.IGNORECASE,
)

# Contradiction surface: question references prior inconsistency
_CONTRADICTION_WORDS = re.compile(
    r'\b(earlier\s+you\s+(said|mentioned)|you\s+(mentioned|said)\s+earlier|'
    r'but\s+(earlier|before)\s+you|that\s+contradicts|you\s+previously|'
    r'you\s+also\s+said|before\s+you\s+said)\b',
    re.IGNORECASE,
)

# Pressure/edge case: question pushes into adversarial territory
_PRESSURE_WORDS = re.compile(
    r'\b(edge\s+case|failure\s+mode|worst\s+case|corner\s+case|'
    r'what\s+breaks|what\s+fails|what\s+if|what\s+happens\s+when|'
    r'under\s+what\s+conditions|what\s+would\s+go\s+wrong)\b',
    re.IGNORECASE,
)

# Vague reference: question specifically names a vague term from the answer
_VAGUE_TERM_PROBE = re.compile(
    r'\b(what\s+do\s+you\s+mean\s+by|what\s+specifically|be\s+more\s+specific|'
    r'how\s+exactly\s+do\s+you\s+define|what\s+does\s+that\s+mean\s+in|'
    r'when\s+you\s+say\s+[\"\']?\w+[\"\']?,|what\s+are\s+you\s+referring)\b',
    re.IGNORECASE,
)


# ── Check result ──────────────────────────────────────────────────────────────

class CheckResult(NamedTuple):
    name: str
    passed: bool
    evidence: str   # what was found in the question
    severity: str   # "CRITICAL" | "WARN" | "INFO"


# ── Per-turn evaluation ───────────────────────────────────────────────────────

@dataclass
class TurnEvaluation:
    turn_number: int
    question: str
    answer: str
    passed_checks: list[CheckResult] = field(default_factory=list)
    failed_checks: list[CheckResult] = field(default_factory=list)
    ai_phrase_hits: list[str] = field(default_factory=list)
    has_mechanism_probe: bool = False
    has_ownership_probe: bool = False
    has_contradiction_surface: bool = False
    has_pressure: bool = False
    references_prior_answer: bool = False
    is_repetitive: bool = False
    humanness_score: int | None = None

    @property
    def passed(self) -> bool:
        return (
            len(self.ai_phrase_hits) == 0
            and not any(c.severity == "CRITICAL" for c in self.failed_checks)
        )

    @property
    def critical_failures(self) -> list[CheckResult]:
        return [c for c in self.failed_checks if c.severity == "CRITICAL"]


# ── Evaluator ─────────────────────────────────────────────────────────────────

class RealistmEvaluator:
    """
    Evaluates each generated interviewer question for behavioral realism.

    Usage:
        evaluator = RealistmEvaluator()
        result = evaluator.evaluate_turn(
            turn_number=1,
            question="How exactly does common centroid work?",
            answer="I used common centroid for matching.",
            prior_questions=["Tell me about your experience."],
            memory_context="WEAK: matching",
        )
    """

    def evaluate_turn(
        self,
        turn_number: int,
        question: str,
        answer: str,
        prior_questions: list[str] | None = None,
        memory_has_contradiction: bool = False,
        answer_has_ownership_claim: bool | None = None,
        answer_is_vague: bool | None = None,
    ) -> TurnEvaluation:
        ev = TurnEvaluation(turn_number=turn_number, question=question, answer=answer)
        q_lower = question.lower()
        a_lower = answer.lower()

        # ── 1. AI phrase blacklist ─────────────────────────────────────────────
        for pattern, desc in _COMPILED_BLACKLIST:
            if pattern.search(question):
                ev.ai_phrase_hits.append(desc)
                ev.failed_checks.append(CheckResult(
                    name="ai_phrase_blacklist",
                    passed=False,
                    evidence=f"Found: {desc!r} in: {question[:80]}",
                    severity="CRITICAL",
                ))

        # ── 2. Mechanism probe check ───────────────────────────────────────────
        ev.has_mechanism_probe = bool(_MECHANISM_WORDS.search(question))

        # FAIL if: answer was vague/short AND question doesn't probe mechanism
        answer_words = len(answer.split())
        answer_appears_vague = answer_words < 40 or answer_is_vague

        if answer_appears_vague and not ev.has_mechanism_probe:
            ev.failed_checks.append(CheckResult(
                name="mechanism_probe_required",
                passed=False,
                evidence=f"Vague {answer_words}-word answer but no mechanism probe in question",
                severity="CRITICAL",
            ))
        elif ev.has_mechanism_probe:
            ev.passed_checks.append(CheckResult(
                name="mechanism_probe",
                passed=True,
                evidence="Question probes mechanism ✓",
                severity="INFO",
            ))

        # ── 3. Ownership challenge ─────────────────────────────────────────────
        ev.has_ownership_probe = bool(_OWNERSHIP_WORDS.search(question))
        has_ownership_claim = (
            answer_has_ownership_claim
            if answer_has_ownership_claim is not None
            else bool(re.search(r'\bI\s+(designed|built|implemented|taped out|led|ran|owned)\b', answer, re.IGNORECASE))
        )

        if has_ownership_claim and not ev.has_ownership_probe and turn_number <= 3:
            # Not immediately required on every turn, but should appear early
            ev.passed_checks.append(CheckResult(
                name="ownership_verification_opportunity",
                passed=True,
                evidence="Ownership claim present — probe recommended but not required",
                severity="INFO",
            ))

        # ── 4. Contradiction surface ───────────────────────────────────────────
        ev.has_contradiction_surface = bool(_CONTRADICTION_WORDS.search(question))
        if memory_has_contradiction and not ev.has_contradiction_surface:
            ev.failed_checks.append(CheckResult(
                name="contradiction_ignored",
                passed=False,
                evidence="Active contradiction in memory but not surfaced in question",
                severity="WARN",
            ))
        elif ev.has_contradiction_surface:
            ev.passed_checks.append(CheckResult(
                name="contradiction_surfaced",
                passed=True,
                evidence="Question surfaces prior contradiction ✓",
                severity="INFO",
            ))

        # ── 5. Topic continuity ────────────────────────────────────────────────
        # Check word overlap between question and answer (technical words > 4 chars)
        answer_keywords = {
            w.lower().rstrip('.,;:?!')
            for w in answer.split()
            if len(w) > 4 and w.isalpha()
        }
        question_keywords = {
            w.lower().rstrip('.,;:?!')
            for w in question.split()
            if len(w) > 4 and w.isalpha()
        }
        overlap = answer_keywords & question_keywords
        ev.references_prior_answer = len(overlap) >= 1

        if not ev.references_prior_answer and turn_number > 1:
            ev.failed_checks.append(CheckResult(
                name="topic_continuity",
                passed=False,
                evidence=f"No keyword overlap between answer and question (answer keywords: {list(answer_keywords)[:5]})",
                severity="CRITICAL",
            ))
        elif ev.references_prior_answer:
            ev.passed_checks.append(CheckResult(
                name="topic_continuity",
                passed=True,
                evidence=f"References prior answer via: {list(overlap)[:3]} ✓",
                severity="INFO",
            ))

        # ── 6. Repetition check ────────────────────────────────────────────────
        if prior_questions:
            ev.is_repetitive = _is_repetitive(question, prior_questions)
            if ev.is_repetitive:
                ev.failed_checks.append(CheckResult(
                    name="repetition",
                    passed=False,
                    evidence="Question is too similar to a recent question",
                    severity="WARN",
                ))
            else:
                ev.passed_checks.append(CheckResult(
                    name="no_repetition",
                    passed=True,
                    evidence="Question is distinct from recent questions ✓",
                    severity="INFO",
                ))

        # ── 7. Length check ────────────────────────────────────────────────────
        q_words = len(question.split())
        if q_words > 45:
            ev.failed_checks.append(CheckResult(
                name="question_length",
                passed=False,
                evidence=f"Question is {q_words} words — too long for a real interviewer",
                severity="WARN",
            ))
        elif q_words < 5:
            ev.failed_checks.append(CheckResult(
                name="question_length",
                passed=False,
                evidence=f"Question is only {q_words} words — too short",
                severity="WARN",
            ))
        else:
            ev.passed_checks.append(CheckResult(
                name="question_length",
                passed=True,
                evidence=f"Question length: {q_words} words ✓",
                severity="INFO",
            ))

        ev.has_pressure = bool(_PRESSURE_WORDS.search(question))

        return ev


def _is_repetitive(question: str, prior_questions: list[str], threshold: float = 0.5) -> bool:
    """
    Returns True if question shares >50% keyword overlap with any recent question.
    Uses Jaccard similarity on technical words.
    """
    q_words = {
        w.lower().rstrip('.,;:?!')
        for w in question.split()
        if len(w) > 4
    }
    if not q_words:
        return False

    for prior in prior_questions[-3:]:  # only check last 3
        p_words = {
            w.lower().rstrip('.,;:?!')
            for w in prior.split()
            if len(w) > 4
        }
        if not p_words:
            continue
        intersection = q_words & p_words
        union = q_words | p_words
        jaccard = len(intersection) / len(union) if union else 0
        if jaccard > threshold:
            return True
    return False


# ── Humanness judge (optional LLM-based) ──────────────────────────────────────

HUMANNESS_JUDGE_SYSTEM = """\
You are evaluating whether an interview question sounds like it came from a real senior engineer.

Score 1–10:
  1–3: Obviously AI-generated. Unnatural phrasing, generic follow-ups, filler.
  4–6: Borderline. Some natural elements but feels mechanical or AI-like.
  7–8: Mostly human. A real engineer could plausibly ask this.
  9–10: Indistinguishable from a human senior engineer.

Deduct points for:
  - Generic phrases ("can you elaborate", "tell me more")
  - Starting with affirmation ("Great", "Good point", "Interesting")
  - Academic/textbook framing instead of practical focus
  - Multiple questions in one
  - Asking for a definition when experience would be more natural
  - Over-long, over-structured questions
  - Questions that could apply to ANY answer

Add points for:
  - References a specific thing the candidate said
  - Asks for the mechanism behind a claim
  - Feels skeptical but fair
  - Concise — how a busy senior engineer would ask
  - Targets a specific gap in the answer

Return ONLY JSON: {"score": N, "reasoning": "one sentence"}"""


async def evaluate_humanness(question: str, answer: str, domain: str) -> dict:
    """Optional LLM-as-judge evaluation for perceived humanness."""
    from app.providers.llm import generate
    prompt = f"DOMAIN: {domain}\n\nCANDIDATE ANSWER:\n{answer}\n\nINTERVIEWER QUESTION:\n{question}"
    try:
        import json, re
        raw = await generate(
            system=HUMANNESS_JUDGE_SYSTEM,
            prompt=prompt,
            max_tokens=80,
            temperature=0.1,
        )
        raw = re.sub(r'```(?:json)?', '', raw).strip()
        data = json.loads(raw)
        return {"score": int(data.get("score", 5)), "reasoning": data.get("reasoning", "")}
    except Exception:
        return {"score": 0, "reasoning": "evaluation failed"}


# ── Session-level metrics ─────────────────────────────────────────────────────

@dataclass
class SimulationMetrics:
    """Aggregated quality metrics for a full simulation run."""
    profile_type: str
    domain: str
    total_turns: int = 0
    turns_with_mechanism_probe: int = 0
    turns_with_ownership_probe: int = 0
    turns_with_contradiction_surface: int = 0
    turns_with_pressure: int = 0
    turns_with_topic_continuity: int = 0
    turns_without_repetition: int = 0
    ai_phrase_leakage_count: int = 0
    critical_failures: int = 0
    warn_failures: int = 0
    humanness_scores: list[int] = field(default_factory=list)

    def record(self, ev: TurnEvaluation) -> None:
        self.total_turns += 1
        if ev.has_mechanism_probe:     self.turns_with_mechanism_probe += 1
        if ev.has_ownership_probe:     self.turns_with_ownership_probe += 1
        if ev.has_contradiction_surface: self.turns_with_contradiction_surface += 1
        if ev.has_pressure:            self.turns_with_pressure += 1
        if ev.references_prior_answer: self.turns_with_topic_continuity += 1
        if not ev.is_repetitive:       self.turns_without_repetition += 1
        self.ai_phrase_leakage_count += len(ev.ai_phrase_hits)
        self.critical_failures += len(ev.critical_failures)
        self.warn_failures += len([c for c in ev.failed_checks if c.severity == "WARN"])
        if ev.humanness_score is not None:
            self.humanness_scores.append(ev.humanness_score)

    def rate(self, numerator: int) -> str:
        if self.total_turns == 0:
            return "N/A"
        pct = int(numerator / self.total_turns * 100)
        return f"{numerator}/{self.total_turns} ({pct}%)"

    def avg_humanness(self) -> float | None:
        if not self.humanness_scores:
            return None
        return sum(self.humanness_scores) / len(self.humanness_scores)

    def overall_pass(self) -> bool:
        """Simulation passes if zero critical failures and zero AI phrase leakage."""
        return self.critical_failures == 0 and self.ai_phrase_leakage_count == 0


# ═══════════════════════════════════════════════════════════════════════════════
# INTELLIGENCE LAYER v1 — BEHAVIORAL EVALUATION ADD-ON
# Four new metrics. None of these run in the hot path.
# ═══════════════════════════════════════════════════════════════════════════════

# ── Metric 1: interviewer_naturalness_score ────────────────────────────────────
# Heuristic. Zero latency. Runs inline at eval time.
# Measures whether the question emerged from the specific answer, or could
# have been asked regardless of what the candidate said.

def compute_naturalness_score(question: str, answer: str) -> int:
    """
    Jaccard overlap between question and answer technical keywords.
    Bonus for specific numbers and tool names from the answer appearing in the question.

    Score 0–10.
    >= 7: question clearly references specific answer content.
    <= 3: question could have been asked as an opener — no connection.
    """
    # Extract technical words (> 4 chars, alpha only)
    answer_words = {w.lower().rstrip('.,;:?!') for w in answer.split() if len(w) > 4 and w.isalpha()}
    question_words = {w.lower().rstrip('.,;:?!') for w in question.split() if len(w) > 4 and w.isalpha()}

    if not question_words:
        return 0

    overlap = answer_words & question_words
    base_score = min(8, int(len(overlap) / max(1, len(question_words) * 0.3) * 10))

    # Bonus: question contains a number from the answer
    answer_numbers = re.findall(r'\d+(?:\.\d+)?(?:ps|ns|mv|v|mhz|ghz|nm|um|%|k|m)?', answer.lower())
    question_lower = question.lower()
    number_bonus = 1 if any(n in question_lower for n in answer_numbers) else 0

    # Bonus: question contains a tool name from the answer
    tool_names = ['virtuoso', 'innovus', 'calibre', 'vcs', 'questa', 'vivado', 'genus', 'tempus', 'voltus']
    tool_bonus = 1 if any(t in answer.lower() and t in question_lower for t in tool_names) else 0

    return min(10, base_score + number_bonus + tool_bonus)


# ── Metric 2: pressure_effectiveness_score ────────────────────────────────────
# Composite heuristic. Runs inline. Only scored in PRESSURE/ESCALATING modes.
# Measures whether pressure questions were adversarial and targeted correctly.

def compute_pressure_effectiveness(
    question: str,
    mode: str,
    next_turn_depth: int | None = None,  # eval.depth from the NEXT turn (retrospective)
) -> int | None:
    """
    Score 0–10. Returns None if not in a pressure mode.

    Component A (0–4): Mode alignment — is the question adversarial?
    Component B (0–3): Answer engagement — did depth improve next turn? (retrospective)
    Component C (0–3): No rescue behaviors — question doesn't hint at the answer
    """
    if mode not in ("PRESSURE", "ESCALATING"):
        return None

    q = question.lower()

    # Component A: adversarial framing indicators
    pressure_phrases = [
        "what breaks", "what fails", "does it still", "what if", "failure mode",
        "edge case", "corner case", "worst case", "what happens when", "prove",
        "under what conditions", "cross-domain", "consequence of",
    ]
    adversarial_count = sum(1 for p in pressure_phrases if p in q)
    component_a = min(4, adversarial_count * 2)

    # Component B: retrospective — did the candidate's depth improve?
    if next_turn_depth is not None:
        component_b = 3 if next_turn_depth >= 7 else (2 if next_turn_depth >= 5 else 1)
    else:
        component_b = 2  # neutral when not yet known

    # Component C: no rescue behavior
    rescue_phrases = [
        "remember", "think about", "consider", "hint", "the answer is",
        "you should", "typically you would", "the way to",
    ]
    has_rescue = any(p in q for p in rescue_phrases)
    component_c = 0 if has_rescue else 3

    return min(10, component_a + component_b + component_c)


# ── Metric 3: realism_score — LLM judge (async, off hot path) ─────────────────

REALISM_JUDGE_SYSTEM = """\
You are evaluating whether an interview question sounds like a real senior engineer or an AI assistant.

Score 1–10:
  1–3: Obviously AI-generated. Filler phrases, generic, could apply to any answer.
  4–6: Mixed. Some natural elements but mechanical or AI-like overall.
  7–8: Mostly human. A real engineer could plausibly ask this in a real interview.
  9–10: Indistinguishable from a human senior engineer.

DEDUCT points for:
  - Filler openers: "Great", "Interesting", "I see", "Absolutely"
  - Generic probes: "Can you elaborate", "Tell me more", "Can you expand"
  - Multiple questions in one response
  - Academic/formal register instead of engineer register
  - Any positive affirmation of the prior answer
  - Question could be asked regardless of what the candidate said

ADD points for:
  - References a specific word, number, or claim from the candidate's answer
  - Concise phrasing — how a senior engineer types in Slack
  - Skeptical tone without being hostile
  - Asks for mechanism, edge case, or failure mode
  - Sounds like it came from someone who just heard that specific answer

Return ONLY JSON: {"score": N, "reason": "one short sentence"}\
"""


async def evaluate_realism_score(question: str, answer: str, domain: str) -> dict:
    """
    LLM-as-judge for perceived humanness.
    Returns {"score": int, "reason": str}.
    Called asynchronously after question generation — never in hot path.
    """
    from app.providers.llm import generate
    import json as _json

    prompt = f"DOMAIN: {domain}\n\nCANDIDATE ANSWER:\n{answer}\n\nINTERVIEWER QUESTION:\n{question}"
    try:
        raw = await generate(
            system=REALISM_JUDGE_SYSTEM,
            prompt=prompt,
            max_tokens=60,
            temperature=0.1,
        )
        raw = re.sub(r'```(?:json)?', '', raw).strip()
        data = _json.loads(raw)
        return {
            "score": max(1, min(10, int(data.get("score", 5)))),
            "reason": str(data.get("reason", "")),
        }
    except Exception:
        return {"score": 0, "reason": "evaluation failed"}


# ── Metric 4: interruption_quality_score — LLM judge (async, off hot path) ────

INTERRUPTION_JUDGE_SYSTEM = """\
The interviewer interrupted or redirected a candidate mid-answer.
Score the QUALITY of the interruption 1–10.

HIGH score (8–10): Interruption is short (under 12 words), names exactly what was wrong
  or missing, redirects to a specific gap. Candidate has no ambiguity about what to address.

LOW score (1–4): Vague redirection ("go deeper", "tell me more"), too long,
  sounds hostile without cause, or doesn't identify the specific gap.

Return ONLY JSON: {"score": N, "reason": "one short sentence"}\
"""


async def evaluate_interruption_quality(question: str, answer: str) -> dict:
    """
    LLM-as-judge for interruption/correction quality.
    Only called when mode was ESCALATING or a correction was applied.
    Returns {"score": int, "reason": str}.
    """
    from app.providers.llm import generate
    import json as _json

    prompt = f"CANDIDATE ANSWER:\n{answer}\n\nINTERVIEWER RESPONSE:\n{question}"
    try:
        raw = await generate(
            system=INTERRUPTION_JUDGE_SYSTEM,
            prompt=prompt,
            max_tokens=60,
            temperature=0.1,
        )
        raw = re.sub(r'```(?:json)?', '', raw).strip()
        data = _json.loads(raw)
        return {
            "score": max(1, min(10, int(data.get("score", 5)))),
            "reason": str(data.get("reason", "")),
        }
    except Exception:
        return {"score": 0, "reason": "evaluation failed"}


# ── Extended TurnEvaluation fields ─────────────────────────────────────────────
# These are computed and stored alongside existing fields.
# Add to TurnEvaluation dataclass instances after construction:
#
#   ev = evaluator.evaluate_turn(...)
#   ev.naturalness_score = compute_naturalness_score(question, answer)
#   ev.pressure_score = compute_pressure_effectiveness(question, mode)
#   # async (off hot path):
#   ev.realism = await evaluate_realism_score(question, answer, domain)
#   ev.interruption = await evaluate_interruption_quality(question, answer)
#
# SimulationMetrics additions — add these to SimulationMetrics.record():

def record_intelligence_metrics(
    metrics: 'SimulationMetrics',
    ev: 'TurnEvaluation',
    naturalness: int,
    pressure: int | None,
) -> None:
    """
    Update SimulationMetrics with intelligence layer scores.
    Called from simulator.py after each turn.

    Usage:
        from tests.simulation.evaluator import record_intelligence_metrics
        record_intelligence_metrics(metrics, ev, naturalness_score, pressure_score)
    """
    if not hasattr(metrics, 'naturalness_scores'):
        metrics.naturalness_scores = []
    if not hasattr(metrics, 'pressure_effective_count'):
        metrics.pressure_effective_count = 0
    if not hasattr(metrics, 'pressure_total_count'):
        metrics.pressure_total_count = 0

    metrics.naturalness_scores.append(naturalness)
    if pressure is not None:
        metrics.pressure_total_count += 1
        if pressure >= 7:
            metrics.pressure_effective_count += 1


def render_intelligence_metrics(metrics: 'SimulationMetrics') -> list[str]:
    """
    Returns formatted lines for the simulation report.
    Called from report.py _render_metrics().
    """
    lines = []
    if hasattr(metrics, 'naturalness_scores') and metrics.naturalness_scores:
        avg = sum(metrics.naturalness_scores) / len(metrics.naturalness_scores)
        lines.append(f"    naturalness_score (avg):    {avg:.1f}/10")
    if hasattr(metrics, 'pressure_total_count') and metrics.pressure_total_count > 0:
        rate = metrics.pressure_effective_count / metrics.pressure_total_count * 100
        lines.append(f"    pressure_effectiveness:     {metrics.pressure_effective_count}/{metrics.pressure_total_count} ({int(rate)}% >= 7/10)")
    return lines
