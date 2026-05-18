"""
report.py — Terminal report renderer for simulation results.

Outputs a detailed, color-free terminal report readable in CI logs.
Designed for easy scanning: PASS/FAIL clearly visible, evidence inline.
"""
from __future__ import annotations

import textwrap
from tests.simulation.evaluator import SimulationMetrics, TurnEvaluation
from tests.simulation.simulator import SimulationResult, ScenarioResult, TurnResult


WIDTH = 70


def render_full_report(results: list[SimulationResult], scenario_results: list[ScenarioResult]) -> str:
    lines = []

    lines.append("=" * WIDTH)
    lines.append("  VLSI INTERVIEWER REALISM SIMULATION REPORT")
    lines.append("=" * WIDTH)

    # ── Scenario results ──────────────────────────────────────────────────────
    if scenario_results:
        lines.append("")
        lines.append("BEHAVIORAL SCENARIO TESTS")
        lines.append("-" * WIDTH)
        passed_count = sum(1 for r in scenario_results if r.passed)
        lines.append(f"  {passed_count}/{len(scenario_results)} scenarios passed")
        lines.append("")

        for r in scenario_results:
            status = "PASS" if r.passed else "FAIL"
            lines.append(f"  [{status}] {r.scenario_name}")
            lines.append(f"         Q: {_wrap(r.question, 60, '            ')}")
            if r.failure_reasons:
                for reason in r.failure_reasons:
                    lines.append(f"         ! {reason}")
            lines.append(f"         latency: {r.latency_ms}ms")
            lines.append("")

    # ── Full simulation results ───────────────────────────────────────────────
    if results:
        lines.append("")
        lines.append("FULL SIMULATION RESULTS")
        lines.append("-" * WIDTH)

        all_passed = sum(1 for r in results if r.overall_passed)
        lines.append(f"  {all_passed}/{len(results)} simulations passed")
        lines.append("")

        for sim in results:
            lines.extend(_render_simulation(sim))

    # ── Combined summary ──────────────────────────────────────────────────────
    lines.append("")
    lines.append("=" * WIDTH)
    lines.append("AGGREGATE METRICS")
    lines.append("-" * WIDTH)

    all_metrics = [r.metrics for r in results]
    if all_metrics:
        total_ai_leakage = sum(m.ai_phrase_leakage_count for m in all_metrics)
        total_critical   = sum(m.critical_failures for m in all_metrics)
        total_turns      = sum(m.total_turns for m in all_metrics)
        total_mechanism  = sum(m.turns_with_mechanism_probe for m in all_metrics)
        total_continuity = sum(m.turns_with_topic_continuity for m in all_metrics)

        lines.append(f"  AI phrase leakage:       {total_ai_leakage} occurrences {'✓' if total_ai_leakage == 0 else '✗ FAIL'}")
        lines.append(f"  Critical failures:       {total_critical} {'✓' if total_critical == 0 else '✗ FAIL'}")
        lines.append(f"  Mechanism probe rate:    {total_mechanism}/{total_turns} ({_pct(total_mechanism, total_turns)}%)")
        lines.append(f"  Topic continuity rate:   {total_continuity}/{total_turns} ({_pct(total_continuity, total_turns)}%)")

        humanness_all = []
        for m in all_metrics:
            humanness_all.extend(m.humanness_scores)
        if humanness_all:
            avg_h = sum(humanness_all) / len(humanness_all)
            lines.append(f"  Avg humanness score:     {avg_h:.1f}/10")

    lines.append("")
    scenario_pass = all(r.passed for r in scenario_results) if scenario_results else True
    sim_pass      = all(r.overall_passed for r in results) if results else True
    overall = "REALISM: PASS" if (scenario_pass and sim_pass) else "REALISM: FAIL"
    lines.append(f"  {overall}")
    lines.append("=" * WIDTH)

    return "\n".join(lines)


def _render_simulation(sim: SimulationResult) -> list[str]:
    lines = []
    status = "PASS" if sim.overall_passed else "FAIL"
    lines.append(f"[{status}] Profile: {sim.profile_type.upper()} | Domain: {sim.domain}")
    lines.append("")

    for turn in sim.turns:
        lines.extend(_render_turn(turn))

    lines.extend(_render_metrics(sim.metrics))
    lines.append("")
    lines.append("-" * WIDTH)
    return lines


def _render_turn(turn: TurnResult) -> list[str]:
    lines = []
    ev = turn.evaluation
    status = "✓" if ev.passed else "✗"

    lines.append(f"  Turn {turn.turn_number} [{status}] mode: {turn.mode_before.value} → {turn.mode_after.value}")
    lines.append(f"  A: {_wrap(turn.answer, 64, '     ')}")
    lines.append(f"  Q: {_wrap(turn.question, 64, '     ')}")

    # Behavioral indicators
    indicators = []
    if ev.has_mechanism_probe:      indicators.append("mechanism_probe")
    if ev.has_pressure:             indicators.append("pressure")
    if ev.has_contradiction_surface: indicators.append("contradiction_surfaced")
    if ev.has_ownership_probe:      indicators.append("ownership_probe")
    if ev.references_prior_answer:  indicators.append("references_prior")
    if ev.is_repetitive:            indicators.append("REPETITIVE")
    if indicators:
        lines.append(f"     behaviors: {', '.join(indicators)}")

    # Eval scores
    if turn.eval_scores:
        avg = sum(turn.eval_scores.values()) / len(turn.eval_scores)
        depth = turn.eval_scores.get('depth', '?')
        lines.append(f"     eval: avg={avg:.1f} depth={depth} | latency={turn.latency_ms}ms first_token={turn.first_token_ms}ms")

    # Failures
    if ev.ai_phrase_hits:
        for hit in ev.ai_phrase_hits:
            lines.append(f"     ✗ AI PHRASE: {hit}")

    for check in ev.failed_checks:
        if check.severity == "CRITICAL":
            lines.append(f"     ✗ [{check.severity}] {check.name}: {check.evidence[:70]}")
        elif check.severity == "WARN":
            lines.append(f"     ~ [WARN] {check.name}: {check.evidence[:70]}")

    # Humanness
    if ev.humanness_score is not None:
        lines.append(f"     humanness: {ev.humanness_score}/10")

    lines.append("")
    return lines


def _render_metrics(m: SimulationMetrics) -> list[str]:
    lines = ["  METRICS SUMMARY:"]
    lines.append(f"    mechanism_probe_rate:       {m.rate(m.turns_with_mechanism_probe)}")
    lines.append(f"    topic_continuity_rate:      {m.rate(m.turns_with_topic_continuity)}")
    lines.append(f"    no_repetition_rate:         {m.rate(m.turns_without_repetition)}")
    lines.append(f"    pressure_application_rate:  {m.rate(m.turns_with_pressure)}")
    lines.append(f"    contradiction_catch_rate:   {m.rate(m.turns_with_contradiction_surface)}")
    lines.append(f"    ai_phrase_leakage:          {m.ai_phrase_leakage_count} {'✓' if m.ai_phrase_leakage_count == 0 else '✗'}")
    lines.append(f"    critical_failures:          {m.critical_failures} {'✓' if m.critical_failures == 0 else '✗'}")
    if m.humanness_scores:
        avg_h = sum(m.humanness_scores) / len(m.humanness_scores)
        lines.append(f"    avg_humanness_score:        {avg_h:.1f}/10")
    lines.append(f"    overall: {'PASS ✓' if m.overall_pass() else 'FAIL ✗'}")
    return lines


def _wrap(text: str, width: int, subsequent_indent: str = "") -> str:
    if len(text) <= width:
        return text
    wrapped = textwrap.wrap(text, width=width, subsequent_indent=subsequent_indent)
    return "\n".join(wrapped)


def _pct(num: int, denom: int) -> int:
    if denom == 0:
        return 0
    return int(num / denom * 100)
