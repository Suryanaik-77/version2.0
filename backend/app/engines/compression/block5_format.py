"""
block5_format.py — Output Format Spec (Block 5 of 5)

Defines the EXACT output type and format of the compression pipeline.
The CompressedPrompt is the single contract between this system
and build_question_prompt().

Output structure:
    CompressedPrompt(
        mode=...,
        system_context=...,
        memory=...,
        eval=...,
        signals=...,
        transcript=...,
        meta=CompressMeta(...)
    )

Rules:
  - All fields are strings (empty string = absent, never None)
  - Structure is fixed — no optional keys, no prose variability
  - to_prompt_string() produces the deterministic user-turn prompt
  - meta contains diagnostics — never injected into the LLM prompt
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from app.engines.compression.block4_fallback import CompressionMode


# ── Output types ──────────────────────────────────────────────────────────────

@dataclass
class CompressMeta:
    """
    Diagnostic metadata. Logged but NEVER included in the LLM prompt.
    Used by observability and simulation harness.
    """
    mode:              CompressionMode    # FULL | MINIMAL | TRANSCRIPT
    total_tokens:      int                # estimated total user-prompt tokens
    system_tokens:     int                # always 492
    sections_present:  list[str]          # which sections are non-empty
    sections_skipped:  list[str]          # which sections were empty/dropped
    fallback_reason:   str                # empty if FULL mode
    memory_missing:    bool = False
    eval_missing:      bool = False
    signals_empty:     bool = False
    transcript_fallback: bool = False
    elapsed_ms:        float = 0.0        # compression wall time


@dataclass
class CompressedPrompt:
    """
    The single output of the compression pipeline.
    All string fields are empty string when absent — never None.
    Fixed structure — no field is ever added or removed at runtime.
    """
    # LLM-facing fields (all str, all bounded)
    system_context:  str    # always QUESTION_SYSTEM — never mutated
    memory:          str    # serialized memory block or ""
    eval:            str    # serialized eval block or ""
    signals:         str    # serialized signals block or ""
    transcript:      str    # transcript (possibly tail-truncated) — never ""
    last_question:   str    # most recent interviewer question, or ""
    domain_label:    str    # domain string for DOMAIN: line
    mode_label:      str    # mode string for INTERVIEW PHASE: line
    example:         str    # corpus example utterance or ""
    trend:           str    # trend note or ""
    avoid:           str    # avoid block or ""

    # Diagnostics — never injected into prompt
    meta: CompressMeta

    def to_prompt_string(self) -> str:
        """
        Produces the exact user-turn prompt string.
        Fixed structure — no variability in ordering or labels.

        Format:
            DOMAIN: {domain_label}
            INTERVIEW PHASE: {mode_label}
            [SIGNALS: ...]
            [CONTRADICTION: ...]
            [CONTEXT: ...]
            [EVAL: ...]
            [EXAMPLE: ...]
            [trend note]
            [AVOID: ...]

            CANDIDATE ANSWER:
            {transcript}

            Your question:
        """
        sections = []

        sections.append(f"DOMAIN: {self.domain_label}")
        sections.append(f"INTERVIEW PHASE: {self.mode_label}")

        if self.signals:
            sections.append(self.signals)

        if self.memory:
            sections.append(f"CONTEXT:\n{self.memory}")

        if self.eval:
            sections.append(self.eval)

        if self.example:
            sections.append(self.example)

        if self.trend:
            sections.append(self.trend)

        if self.avoid:
            sections.append(self.avoid)

        header = "\n".join(sections)
        return (
            f"{header}\n\n"
            f"CANDIDATE ANSWER:\n{self.transcript}\n\n"
            f"Your question:"
        )

    def to_dict(self) -> dict:
        """
        Machine-readable dict representation.
        Used by simulation harness for logging and assertions.
        Fixed keys — structure never changes.
        """
        return {
            "COMPRESSED_PROMPT": {
                "system_context": f"[{self.meta.system_tokens} tokens, fixed]",
                "memory":         self.memory or "[empty]",
                "eval":           self.eval or "[empty]",
                "signals":        self.signals or "[empty]",
                "transcript":     self.transcript,
                "last_question":  self.last_question or "[empty]",
                "domain_label":   self.domain_label,
                "mode_label":     self.mode_label,
                "example":        self.example or "[empty]",
                "trend":          self.trend or "[empty]",
                "avoid":          self.avoid or "[empty]",
            },
            "_meta": {
                "mode":              self.meta.mode.value,
                "total_tokens":      self.meta.total_tokens,
                "system_tokens":     self.meta.system_tokens,
                "sections_present":  self.meta.sections_present,
                "sections_skipped":  self.meta.sections_skipped,
                "fallback_reason":   self.meta.fallback_reason or "none",
                "memory_missing":    self.meta.memory_missing,
                "eval_missing":      self.meta.eval_missing,
                "signals_empty":     self.meta.signals_empty,
                "transcript_fallback": self.meta.transcript_fallback,
                "elapsed_ms":        round(self.meta.elapsed_ms, 2),
            },
        }


# ── Main assembler ────────────────────────────────────────────────────────────

def assemble(
    system_context:  str,
    memory_text:     str,
    eval_text:       str,
    signals_text:    str,
    transcript_text: str,
    last_question:   str,
    domain_label:    str,
    mode_label:      str,
    example_text:    str,
    trend_text:      str,
    avoid_text:      str,
    mode:            CompressionMode,
    fallback_reason: str,
    memory_missing:  bool,
    eval_missing:    bool,
    signals_empty:   bool,
    transcript_fallback: bool,
    elapsed_ms:      float,
) -> CompressedPrompt:
    """
    Final assembly step. Computes meta, returns typed CompressedPrompt.
    No transformation here — strings are passed through unchanged.
    """
    from app.engines.compression.block2_budget import tokens, SYSTEM_PROMPT_TOKENS

    all_sections = {
        "signals":       signals_text,
        "memory":        memory_text,
        "eval":          eval_text,
        "example":       example_text,
        "trend":         trend_text,
        "avoid":         avoid_text,
        "transcript":    transcript_text,
    }

    present = [k for k, v in all_sections.items() if v]
    skipped = [k for k, v in all_sections.items() if not v]

    user_prompt_preview = "\n".join(v for v in all_sections.values() if v)
    total_tokens = SYSTEM_PROMPT_TOKENS + tokens(user_prompt_preview)

    return CompressedPrompt(
        system_context  = system_context,
        memory          = memory_text,
        eval            = eval_text,
        signals         = signals_text,
        transcript      = transcript_text,
        last_question   = last_question,
        domain_label    = domain_label,
        mode_label      = mode_label,
        example         = example_text,
        trend           = trend_text,
        avoid           = avoid_text,
        meta            = CompressMeta(
            mode               = mode,
            total_tokens       = total_tokens,
            system_tokens      = SYSTEM_PROMPT_TOKENS,
            sections_present   = present,
            sections_skipped   = skipped,
            fallback_reason    = fallback_reason,
            memory_missing     = memory_missing,
            eval_missing       = eval_missing,
            signals_empty      = signals_empty,
            transcript_fallback= transcript_fallback,
            elapsed_ms         = elapsed_ms,
        ),
    )
