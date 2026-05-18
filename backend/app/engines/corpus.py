"""
corpus.py — Interviewer realism corpus.

75 entries across 7 categories. Plugs into build_question_prompt() via
get_mode_examples() and get_signal_examples(). Zero latency — pure data.

Integration point:
    from app.engines.corpus import get_mode_examples, get_signal_examples
    Used in prompts.py build_question_prompt() to inject 1–2 examples per turn.

Each entry:
    category     — which behavioral category
    utterance    — the exact interviewer line (tone reference for LLM)
    intent       — what it extracts
    trigger      — when to use it (matches InlineSignals / InterviewerMode)
    targets      — candidate weakness it surfaces
"""
from __future__ import annotations
from dataclasses import dataclass
from app.models.session import InterviewerMode


@dataclass(frozen=True)
class CorpusEntry:
    category: str
    utterance: str
    intent: str
    trigger: str
    targets: str


CORPUS: list[CorpusEntry] = [

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY A: SKEPTICISM PROBES
    # Tone: flat. Not hostile. Senior engineer who has heard this answer before.
    # ═══════════════════════════════════════════════════════════════════════════

    CorpusEntry(
        category="skepticism_probe",
        utterance="You said sub-0.1% mismatch. Sim or silicon?",
        intent="Distinguish simulation claims from silicon measurement",
        trigger="Candidate states a specific performance number without specifying verification method",
        targets="Presenting sim results as silicon-verified performance",
    ),
    CorpusEntry(
        category="skepticism_probe",
        utterance="Common centroid is the answer everyone gives. What specifically about your layout made it necessary over careful manual placement?",
        intent="Force design rationale, not technique citation",
        trigger="Candidate names a technique without explaining why it was the right choice for this design",
        targets="Reflexive technique citation without design-specific justification",
    ),
    CorpusEntry(
        category="skepticism_probe",
        utterance="You said you owned the floorplan. Did you set the constraints, do the placement, or review it after someone else ran it?",
        intent="Verify ownership granularity",
        trigger="Vague ownership claim: 'I owned X', 'I was responsible for Y'",
        targets="Inflated ownership — reviewing vs. doing",
    ),
    CorpusEntry(
        category="skepticism_probe",
        utterance="The tool found that, or you found it?",
        intent="Distinguish automated detection from engineer insight",
        trigger="Candidate describes finding a bug or issue without specifying how",
        targets="Presenting EDA tool output as personal engineering skill",
    ),
    CorpusEntry(
        category="skepticism_probe",
        utterance="That's textbook. On your actual block — what constraint was forcing that decision?",
        intent="Force departure from memorized answers into real design context",
        trigger="Answer sounds clean and structured, like a definition",
        targets="Memorized answer with no design-specific grounding",
    ),
    CorpusEntry(
        category="skepticism_probe",
        utterance="You ran this at typical corner. What happened at slow-slow, low voltage?",
        intent="Test PVT corner coverage",
        trigger="Candidate describes simulation results without mentioning corners",
        targets="Nominal-only verification mindset",
    ),
    CorpusEntry(
        category="skepticism_probe",
        utterance="Your call or your manager's call?",
        intent="Separate individual decisions from team/management decisions",
        trigger="Candidate presents a major design decision as their own choice",
        targets="Overstating individual decision authority on team projects",
    ),
    CorpusEntry(
        category="skepticism_probe",
        utterance="When you say timing closed — closed to what margin?",
        intent="Force specific slack values instead of binary pass/fail",
        trigger="Candidate says 'timing closed' or 'we met timing' without numbers",
        targets="Binary pass/fail thinking; no margin awareness",
    ),
    CorpusEntry(
        category="skepticism_probe",
        utterance="This IP has been through tape-out, or are we still on the first version?",
        intent="Assess silicon maturity of the design",
        trigger="Candidate describes block as proven or production-quality",
        targets="Overstating silicon confidence without tape-out history",
    ),
    CorpusEntry(
        category="skepticism_probe",
        utterance="You've described what the tool did. What did you do when the tool got it wrong?",
        intent="Probe manual engineering capability beyond automated flow",
        trigger="Candidate describes a successful flow with no manual intervention",
        targets="Passive EDA usage — no evidence of understanding tool limitations",
    ),
    CorpusEntry(
        category="skepticism_probe",
        utterance="How long did that actually take?",
        intent="Sanity-check effort claims; force time-grounded answers",
        trigger="Candidate describes a complex fix or optimization without mentioning effort",
        targets="Making hard problems sound easy; no visceral sense of difficulty",
    ),
    CorpusEntry(
        category="skepticism_probe",
        utterance="Did you measure that post-silicon or are you extrapolating from sim?",
        intent="Enforce silicon vs. simulation boundary",
        trigger="Candidate quotes a number without specifying measurement context",
        targets="Treating simulation as equivalent to silicon measurement",
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY B: CONTRADICTION ATTACKS
    # Tone: direct. Factual. Not accusatory. Uses candidate's exact words.
    # ═══════════════════════════════════════════════════════════════════════════

    CorpusEntry(
        category="contradiction_attack",
        utterance="Earlier you said timing wasn't an issue. Now you're describing three months of timing closure. Which is it?",
        intent="Force reconciliation of contradictory effort claims",
        trigger="Memory flags prior claim of ease vs. current description of difficulty",
        targets="Adjusting narrative based on perceived interviewer expectations",
    ),
    CorpusEntry(
        category="contradiction_attack",
        utterance="You told me you always use common centroid. Now you're saying manual placement was fine for this block. Walk me through that.",
        intent="Surface absolute-practice claim vs. project-specific exception",
        trigger="Prior claim of universal practice contradicts current design description",
        targets="Claiming absolute practices that don't match actual project behavior",
    ),
    CorpusEntry(
        category="contradiction_attack",
        utterance="A minute ago you said the layout engineer handled that. Now you're saying you designed the matching yourself. Which one?",
        intent="Pin down actual ownership",
        trigger="Ownership description contradicts prior statement about team structure",
        targets="Ownership inflation mid-interview",
    ),
    CorpusEntry(
        category="contradiction_attack",
        utterance="You said 28nm process. You also said Vt of 700mV. Those don't go together. Which number is wrong?",
        intent="Catch technically inconsistent parameter claims",
        trigger="Memory flags incompatible technical numbers in the same answer history",
        targets="Quoting numbers without understanding their relationships",
    ),
    CorpusEntry(
        category="contradiction_attack",
        utterance="You said IR drop was under spec. Then you described fixing a voltage drop issue. Those two things don't fit.",
        intent="Force explanation of pre/post-fix timeline",
        trigger="'No problem' claim followed by description of fixing that exact problem",
        targets="Describing problems and solutions while claiming there were no problems",
    ),
    CorpusEntry(
        category="contradiction_attack",
        utterance="Earlier: clock skew was never your concern. Now: two weeks on CTS. Help me understand that.",
        intent="Reconcile effort with prior minimization claim",
        trigger="Prior 'no issue' claim vs. current multi-week effort description",
        targets="Retrospectively minimizing difficulty",
    ),
    CorpusEntry(
        category="contradiction_attack",
        utterance="You said the UVM environment was built from scratch. Then you said you extended an existing scoreboard. Those aren't the same thing.",
        intent="Establish what was actually built vs. inherited",
        trigger="'Built from scratch' claim contradicts later description of modification",
        targets="Inflating scope of individual contribution",
    ),
    CorpusEntry(
        category="contradiction_attack",
        utterance="You said you never had latch-up issues on this project. Two answers ago you described adding guard rings to fix a latch-up failure. Which is accurate?",
        intent="Surface factual contradiction between two turn-separated claims",
        trigger="Memory flags 'no issue' claim + description of fix for that issue",
        targets="Memory inconsistency — candidate forgot prior claims",
    ),
    CorpusEntry(
        category="contradiction_attack",
        utterance="You described a full tape-out flow earlier. Now you're saying this block never went to fab. One of those is wrong.",
        intent="Clarify tape-out vs. prototype status",
        trigger="Tape-out claim in one answer, prototype/sim-only in another",
        targets="Silicon claim inflation",
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY C: PRESSURE ESCALATION
    # Tone: shorter. No framing. Question IS the pressure.
    # Applied only after demonstrated understanding — tests ceiling.
    # ═══════════════════════════════════════════════════════════════════════════

    CorpusEntry(
        category="pressure_escalation",
        utterance="Common centroid handles linear gradients. What happens with a non-uniform oxide thickness gradient across the array?",
        intent="Test whether technique understanding holds under second-order effects",
        trigger="PRESSURE mode; candidate answered common centroid mechanism correctly",
        targets="Understanding the rule without knowing when it breaks",
    ),
    CorpusEntry(
        category="pressure_escalation",
        utterance="You closed timing at 1.8V nominal. Your customer runs at 1.62V. Does it still close?",
        intent="Test margin analysis and worst-case voltage thinking",
        trigger="PRESSURE mode; candidate confident on timing closure",
        targets="Nominal-only design mindset",
    ),
    CorpusEntry(
        category="pressure_escalation",
        utterance="Your guard ring stopped latch-up in sim. At what injection current does the p+ ring resistance matter, and have you checked that?",
        intent="Force quantitative validation of a qualitative fix",
        trigger="PRESSURE mode; candidate described guard ring solution",
        targets="Qualitative fix without quantitative verification",
    ),
    CorpusEntry(
        category="pressure_escalation",
        utterance="Coverage is at 98%. Walk me through the 2% that's missing — what's in those bins and why are they uncovered?",
        intent="Distinguish engineers who own their coverage from those who ran the tool",
        trigger="PRESSURE mode; candidate claimed coverage closure",
        targets="Coverage number without understanding the gap",
    ),
    CorpusEntry(
        category="pressure_escalation",
        utterance="Your ECO added 12 buffers. What's the IR drop impact of those buffers on the domain they're in?",
        intent="Test cross-domain awareness — timing fix with power consequence",
        trigger="PRESSURE mode; candidate described timing ECO without power analysis",
        targets="Siloed optimization without cross-domain awareness",
    ),
    CorpusEntry(
        category="pressure_escalation",
        utterance="You matched the diff pair. Did you match the tail current source? What's the CMRR impact if you didn't?",
        intent="Test completeness of matching strategy",
        trigger="PRESSURE mode; candidate described differential pair matching",
        targets="Partial matching strategy that misses critical devices",
    ),
    CorpusEntry(
        category="pressure_escalation",
        utterance="CTS hit 150ps, spec is 200ps, you have 50ps margin. Post-silicon first lot comes back 80ps worse than sim. Do you still close?",
        intent="Test post-silicon margin stack-up thinking",
        trigger="PRESSURE mode; candidate described meeting CTS spec",
        targets="No margin budget between simulation and silicon",
    ),
    CorpusEntry(
        category="pressure_escalation",
        utterance="Interdigitation fixed your gradient problem. Now your routing congestion in that area is up 15%. How do you resolve that?",
        intent="Force acknowledgment of technique tradeoffs",
        trigger="PRESSURE mode; candidate described interdigitation without area/routing cost",
        targets="Single-dimension optimization ignoring tradeoffs",
    ),
    CorpusEntry(
        category="pressure_escalation",
        utterance="Your assertion caught the bug in sim. Without that assertion, what's your estimate of the probability this makes it to tape-out?",
        intent="Force verification escape risk estimation",
        trigger="PRESSURE mode; candidate described assertion-based bug catch",
        targets="Describing what found the bug, not quantifying the risk it represented",
    ),
    CorpusEntry(
        category="pressure_escalation",
        utterance="DRC clean. Timing clean. LVS clean. You tape out. First silicon fails functional test. First three hypotheses.",
        intent="Test silicon debug methodology from first principles",
        trigger="PRESSURE mode; candidate described a complete, clean flow",
        targets="Clean-sim-equals-working-silicon assumption; no debug methodology",
    ),
    CorpusEntry(
        category="pressure_escalation",
        utterance="Your power analysis used average switching activity. Peak activity on that block — have you modeled it?",
        intent="Test peak vs. average power distinction",
        trigger="PRESSURE mode; candidate described IR drop or power analysis",
        targets="Average-activity power analysis presented as sufficient",
    ),
    CorpusEntry(
        category="pressure_escalation",
        utterance="You closed setup. Now there's a new timing path through a late ECO change. How do you know you haven't opened a hold window?",
        intent="Test hold-setup interaction awareness after ECO",
        trigger="PRESSURE mode; candidate described late-stage ECO",
        targets="ECO changes analyzed for one violation type, not both",
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY D: DEEP MECHANISM FORCING
    # Correct conclusion stated. Mechanism skipped. Force the physics.
    # Format: "[outcome stated]. At the [level] — [how exactly]?"
    # ═══════════════════════════════════════════════════════════════════════════

    CorpusEntry(
        category="mechanism_forcing",
        utterance="You said common centroid reduces mismatch. At the device physics level — what is the gradient doing to threshold voltage, and why does the layout cancel it?",
        intent="Force device-level mechanism, not layout-level outcome",
        trigger="Candidate stated outcome without physics explanation",
        targets="Layout knowledge without device physics foundation",
    ),
    CorpusEntry(
        category="mechanism_forcing",
        utterance="Shielded routing reduced coupling. What's the electrical mechanism — why does the shield work?",
        intent="Force shielding physics explanation",
        trigger="Candidate cited shielded routing as a solution without the mechanism",
        targets="Using EDA recommendations without understanding them",
    ),
    CorpusEntry(
        category="mechanism_forcing",
        utterance="Guard ring prevents latch-up. Walk me through the parasitic device structure that would have triggered without it.",
        intent="Force PNPN/NPN structure description",
        trigger="Candidate stated guard ring solution without the parasitic structure",
        targets="Knowing the fix without knowing what it's fixing",
    ),
    CorpusEntry(
        category="mechanism_forcing",
        utterance="Setup violation — where in the timing arc did the delay come from? Cell or net?",
        intent="Separate cell delay from interconnect delay",
        trigger="Candidate described setup fix without identifying the delay source",
        targets="Treating setup violations as monolithic without path analysis",
    ),
    CorpusEntry(
        category="mechanism_forcing",
        utterance="Dummy cells were required. What failure mode do you get at the array edge without them?",
        intent="Force edge effect explanation in matching arrays",
        trigger="Candidate added dummy cells without explaining why",
        targets="Following layout rules without understanding their purpose",
    ),
    CorpusEntry(
        category="mechanism_forcing",
        utterance="Electromigration failed on that net. What's physically happening in the metal at the current density that caused it?",
        intent="Force metal void formation mechanism",
        trigger="Candidate described EM failure fix without mechanism",
        targets="EM as a rule-check item rather than a physical phenomenon",
    ),
    CorpusEntry(
        category="mechanism_forcing",
        utterance="Hold fix added delay on the data path. Why does that fix hold and not setup — explain the timing relationship.",
        intent="Test timing arc understanding for hold specifically",
        trigger="Candidate described hold fix correctly but mechanism unclear",
        targets="Applying fixes without understanding the underlying timing constraint",
    ),
    CorpusEntry(
        category="mechanism_forcing",
        utterance="Your scoreboard checks completions. Where does the expected value come from — what's the reference model?",
        intent="Force reference model description",
        trigger="Candidate described scoreboard without explaining what it compares against",
        targets="Scoreboard as a black box rather than a designed component",
    ),
    CorpusEntry(
        category="mechanism_forcing",
        utterance="ESD protection absorbed the event. What's the physical discharge path from the pad to VSS through your protection device?",
        intent="Force current path explanation",
        trigger="Candidate described ESD solution without the discharge mechanism",
        targets="ESD compliance as a checklist item rather than circuit understanding",
    ),
    CorpusEntry(
        category="mechanism_forcing",
        utterance="Star topology reduced coupling noise. Why — physically — compared to a mesh?",
        intent="Force supply topology tradeoff explanation",
        trigger="Candidate stated topology choice without mechanism",
        targets="Received-wisdom topology choice without understanding why",
    ),
    CorpusEntry(
        category="mechanism_forcing",
        utterance="Your CTS reduced skew. What specifically in the buffer tree changed to do that — insertion delay, buffer sizing, or topology?",
        intent="Force CTS mechanism specificity",
        trigger="Candidate said CTS improved skew without identifying the lever",
        targets="CTS result without understanding which parameter drove it",
    ),
    CorpusEntry(
        category="mechanism_forcing",
        utterance="Constrained random hit that coverage bin. What constraint specifically made the randomizer reach it?",
        intent="Force constraint-to-coverage mapping explanation",
        trigger="Candidate described constrained random closing coverage without specifics",
        targets="Coverage closure as a numbers game without engineering the stimulus",
    ),
    CorpusEntry(
        category="mechanism_forcing",
        utterance="The IR drop improved when you added the decap ring. What's the electrical mechanism — how does decap reduce dynamic IR drop?",
        intent="Force decap charging/discharging mechanism",
        trigger="Candidate described decap as a fix without explaining how it works",
        targets="Using decap as a recipe fix without understanding charge reservoir behavior",
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY E: INTERRUPTION PATTERNS
    # Short. Precise. Redirects mid-answer without being rude.
    # Used when answer is heading in wrong direction.
    # ═══════════════════════════════════════════════════════════════════════════

    CorpusEntry(
        category="interruption",
        utterance="Stop — that's what the tool does. What did you do?",
        intent="Redirect from tool behavior to engineer behavior",
        trigger="Answer narrates EDA tool actions instead of engineer decisions",
        targets="Passive relationship with EDA tools; presenting automation as skill",
    ),
    CorpusEntry(
        category="interruption",
        utterance="Hold on. You said 'we.' Who specifically made that call?",
        intent="Isolate individual contribution from team contribution",
        trigger="Candidate uses plural subject for a specific technical decision",
        targets="Hiding individual gaps behind team language",
    ),
    CorpusEntry(
        category="interruption",
        utterance="You've named five techniques. Pick the one that actually mattered on this project and go deep on that one.",
        intent="Force prioritization and depth over breadth",
        trigger="Answer lists multiple techniques without depth on any",
        targets="Surface-level coverage of many topics to avoid committing to depth",
    ),
    CorpusEntry(
        category="interruption",
        utterance="That's the theory. Skip to what you ran into in practice.",
        intent="Cut through textbook preamble to practical experience",
        trigger="Answer starts with definition or theory before getting to experience",
        targets="Using theory as a delay tactic for gaps in practical knowledge",
    ),
    CorpusEntry(
        category="interruption",
        utterance="I know how UVM works. What was wrong with your implementation specifically.",
        intent="Stop tutorial; get to the specific problem",
        trigger="Candidate begins explaining UVM fundamentals when asked about their implementation",
        targets="Explaining concepts instead of answering the specific question",
    ),
    CorpusEntry(
        category="interruption",
        utterance="The number — what was the actual slack?",
        intent="Interrupt vague timing description to get specifics",
        trigger="Candidate says 'timing was tight' or 'there wasn't much margin'",
        targets="Qualitative language when quantitative data should exist",
    ),
    CorpusEntry(
        category="interruption",
        utterance="Before you go further — simulation or silicon?",
        intent="Establish measurement context before detailed claims",
        trigger="Candidate making detailed claims without specifying sim vs. silicon",
        targets="Blurring sim/silicon boundary",
    ),
    CorpusEntry(
        category="interruption",
        utterance="Wait — you moved past the first problem without explaining the root cause.",
        intent="Keep candidate on unresolved issue",
        trigger="Candidate describes second issue before resolving the first",
        targets="Shallow root cause analysis; moving on without depth",
    ),
    CorpusEntry(
        category="interruption",
        utterance="That's not what I asked. I asked what failed, not what you added.",
        intent="Redirect from solution to failure mode",
        trigger="Candidate answers 'what failed' question by describing the fix instead",
        targets="Cannot articulate the failure mode — only remembers the fix",
    ),
    CorpusEntry(
        category="interruption",
        utterance="Slower. You jumped from 'ran the tool' to 'problem solved.' What happened in between?",
        intent="Surface the gap between running a tool and the actual debug work",
        trigger="Answer compresses the hard part into a single clause",
        targets="Skipping the actual engineering work in the narrative",
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY F: DEBUGGING PIVOTS
    # Shifts from knowledge to application. "You know the concept — apply it."
    # Scenario-based. Forces diagnostic methodology.
    # ═══════════════════════════════════════════════════════════════════════════

    CorpusEntry(
        category="debugging_pivot",
        utterance="Post-layout sim passes. First silicon fails the same test. First three debug steps.",
        intent="Test silicon debug methodology",
        trigger="Candidate described a clean simulation flow; now test if they can debug silicon",
        targets="No silicon debug experience; sim-centric thinking",
    ),
    CorpusEntry(
        category="debugging_pivot",
        utterance="DRC clean. LVS clean. OTA oscillates in silicon. Where do you look first?",
        intent="Test parasitic-driven instability diagnosis",
        trigger="Candidate described clean verification for analog block",
        targets="Cannot diagnose post-layout parasitics issues not caught by sign-off checks",
    ),
    CorpusEntry(
        category="debugging_pivot",
        utterance="Coverage at 94%. Last 6% stuck for two weeks. How do you get unstuck?",
        intent="Test coverage closure strategy under diminishing returns",
        trigger="Candidate described coverage-driven verification",
        targets="No strategy when constrained random stops finding new coverage",
    ),
    CorpusEntry(
        category="debugging_pivot",
        utterance="One-line RTL change. Three unrelated tests now fail. How do you isolate it?",
        intent="Test regression isolation methodology",
        trigger="Candidate described a working verification flow",
        targets="No systematic approach to regression debugging",
    ),
    CorpusEntry(
        category="debugging_pivot",
        utterance="Hold violations at zero after CTS. You add one buffer to fix a setup path. Now 14 hold violations. Why?",
        intent="Test setup-hold interaction after CTS",
        trigger="Candidate described CTS and timing closure",
        targets="Doesn't understand that setup fixes can open hold windows",
    ),
    CorpusEntry(
        category="debugging_pivot",
        utterance="Matched pair shows 0.8% mismatch post-layout. Your spec is 0.3%. You've already used common centroid. Next move.",
        intent="Test fallback strategy when primary matching technique is insufficient",
        trigger="Candidate described common centroid as their matching solution",
        targets="No plan B when primary technique falls short of spec",
    ),
    CorpusEntry(
        category="debugging_pivot",
        utterance="Sim completes but your scoreboard never fires. DUT output looks correct on the waveform. What's wrong?",
        intent="Test verification environment debugging",
        trigger="Candidate described a working UVM environment",
        targets="Cannot distinguish DUT bug from testbench bug",
    ),
    CorpusEntry(
        category="debugging_pivot",
        utterance="Your power grid passes IR drop at room temp. Fails at 125°C with 10% voltage droop. What physical effects are you now dealing with?",
        intent="Test temperature-dependent power grid analysis",
        trigger="Candidate described IR drop analysis at nominal conditions",
        targets="Temperature effects on metal resistance and timing not considered",
    ),
    CorpusEntry(
        category="debugging_pivot",
        utterance="Your assertion fires on a specific transaction but not the next identical one. How do you debug the assertion itself?",
        intent="Test assertion debug capability",
        trigger="Candidate described assertion-based verification without discussing assertion correctness",
        targets="Trusting assertions as ground truth without being able to debug them",
    ),
    CorpusEntry(
        category="debugging_pivot",
        utterance="Timing report shows a path you've never seen in 50 previous runs. It's failing by 200ps. Your process.",
        intent="Test new-path discovery and debug process",
        trigger="Candidate described steady-state timing closure without late-emerging paths",
        targets="No methodology for paths that appear after the main closure flow",
    ),

    # ═══════════════════════════════════════════════════════════════════════════
    # CATEGORY G: CANDIDATE CORRECTION HANDLING
    # Candidate said something technically wrong. Never explain. Never help.
    # State the error. Redirect. Wait.
    # ═══════════════════════════════════════════════════════════════════════════

    CorpusEntry(
        category="correction",
        utterance="Guard rings don't prevent mismatch — they prevent latch-up. What does common centroid actually do?",
        intent="Correct mechanism misattribution; redirect to correct concept",
        trigger="Candidate attributed common centroid's function to guard rings or vice versa",
        targets="Confident misattribution of mechanism to wrong technique",
    ),
    CorpusEntry(
        category="correction",
        utterance="Hold violations aren't fixed by speeding up the data path. Think again.",
        intent="Correct hold fix direction without giving the answer",
        trigger="Candidate described making data path faster to fix a hold violation",
        targets="Direction confusion: hold violations require slowing the data path",
    ),
    CorpusEntry(
        category="correction",
        utterance="UVM sequences drive stimulus. They don't check output. What you're describing is a scoreboard.",
        intent="Correct UVM component responsibility confusion",
        trigger="Candidate attributed checking responsibility to a sequence",
        targets="UVM component roles confused — terminology without functional understanding",
    ),
    CorpusEntry(
        category="correction",
        utterance="You're describing setup behavior. I asked about hold.",
        intent="Redirect setup/hold conflation",
        trigger="Candidate answers hold question with setup explanation",
        targets="Setup/hold conflation — common knowledge gap under pressure",
    ),
    CorpusEntry(
        category="correction",
        utterance="Interdigitation and common centroid are two different techniques. Which one did you actually use?",
        intent="Force distinction between two commonly conflated layout techniques",
        trigger="Candidate used both terms interchangeably",
        targets="Naming techniques without knowing their specific differences",
    ),
    CorpusEntry(
        category="correction",
        utterance="That would increase your hold margin, not fix a hold violation. What you're describing is a setup fix.",
        intent="Precise correction on timing fix direction",
        trigger="Candidate proposed a fix that moves margin in the wrong direction for hold",
        targets="Hold fix logic not grounded in timing arc analysis",
    ),
    CorpusEntry(
        category="correction",
        utterance="You're confusing coverage holes with unreachable states. What type of gap did you actually have?",
        intent="Distinguish coverage unreachable from coverage undriven",
        trigger="Candidate treats all coverage gaps as the same type",
        targets="Coverage analysis depth — unreachable vs. not-yet-stimulated",
    ),
    CorpusEntry(
        category="correction",
        utterance="That's a setup violation, not hold. Hold means the data path is too fast, not too slow.",
        intent="Correct the direction of the timing violation",
        trigger="Candidate inverts the hold condition",
        targets="Hold violation definition confused with setup definition",
    ),
    CorpusEntry(
        category="correction",
        utterance="LVS checks layout-vs-schematic, not layout-vs-spec. You're describing a different check.",
        intent="Correct LVS scope misunderstanding",
        trigger="Candidate describes LVS as verifying design intent or functional correctness",
        targets="LVS purpose and scope misunderstood",
    ),
    CorpusEntry(
        category="correction",
        utterance="An agent in UVM doesn't contain a sequence. An agent contains a driver, monitor, and sequencer. Sequences are separate.",
        intent="Correct UVM hierarchy confusion",
        trigger="Candidate places sequences inside agents",
        targets="UVM structural hierarchy not internalized",
    ),
]


# ── Lookup functions ──────────────────────────────────────────────────────────

def get_mode_examples(mode: InterviewerMode, n: int = 2) -> list[CorpusEntry]:
    """
    Returns n corpus examples most relevant to the current interviewer mode.
    Used to inject tone-calibrated examples into the question prompt.

    Called from build_question_prompt() — must be fast (O(n) scan).
    """
    category_map = {
        InterviewerMode.PROBING:       "skepticism_probe",
        InterviewerMode.DEEPENING:     "mechanism_forcing",
        InterviewerMode.ESCALATING:    "skepticism_probe",
        InterviewerMode.PRESSURE:      "pressure_escalation",
        InterviewerMode.RECOVERING:    "debugging_pivot",   # narrow, scenario-based
        InterviewerMode.TRANSITIONING: "skepticism_probe",
    }
    target = category_map.get(mode, "skepticism_probe")
    matches = [e for e in CORPUS if e.category == target]
    # Deterministic selection: use mode enum value as seed offset
    offset = list(InterviewerMode).index(mode) * 3
    return matches[offset % len(matches) : offset % len(matches) + n] if matches else []


def get_signal_examples(
    vagueness_high: bool = False,
    wrong_answer: bool = False,
    memorization_suspected: bool = False,
    contradiction_active: bool = False,
    n: int = 1,
) -> list[CorpusEntry]:
    """
    Returns examples triggered by specific inline signals.
    Priority: contradiction > wrong > vagueness > memorization.
    """
    if contradiction_active:
        return [e for e in CORPUS if e.category == "contradiction_attack"][:n]
    if wrong_answer:
        return [e for e in CORPUS if e.category == "correction"][:n]
    if vagueness_high:
        return [e for e in CORPUS if e.category == "mechanism_forcing"][:n]
    if memorization_suspected:
        return [e for e in CORPUS if e.category == "skepticism_probe"
                and "memorized" in e.targets.lower() or "textbook" in e.targets.lower()][:n]
    return []


def get_interruption_example() -> CorpusEntry | None:
    """Returns one interruption example for ESCALATING mode phrasing calibration."""
    entries = [e for e in CORPUS if e.category == "interruption"]
    return entries[0] if entries else None
