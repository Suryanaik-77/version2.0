"""
run_harness.py — Simulation harness entry point.

Usage:
  # Fast mode (no LLM for candidate answers, uses templates):
  python tests/run_harness.py

  # Full mode (LLM generates candidate answers too):
  python tests/run_harness.py --full

  # With humanness scoring (2x API calls, slower):
  python tests/run_harness.py --humanness

  # Specific profile only:
  python tests/run_harness.py --profile weak

  # Scenarios only (fastest):
  python tests/run_harness.py --scenarios-only

  # Specific scenario:
  python tests/run_harness.py --scenario mechanism_probe_on_shallow

Requires: OPENAI_API_KEY in environment or .env file.
All results written to tests/simulation/results/latest.txt
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("JWT_SECRET_KEY", "test-harness-key-not-for-production")

import structlog
from app.config import get_settings
from app.models.session import VLSIDomain
from tests.simulation.profiles import CandidateType, build_profiles
from tests.simulation.scenarios import SCENARIOS
from tests.simulation.simulator import run_full_simulation, run_scenario
from tests.simulation.report import render_full_report

log = structlog.get_logger(__name__)


async def main(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY not set. Set it in .env or environment.")
        return 1

    t_start = time.monotonic()
    simulation_results = []
    scenario_results = []

    # ── Run scenarios ─────────────────────────────────────────────────────────
    scenarios_to_run = SCENARIOS
    if args.scenario:
        scenarios_to_run = [s for s in SCENARIOS if s.name == args.scenario]
        if not scenarios_to_run:
            print(f"ERROR: Scenario '{args.scenario}' not found.")
            print("Available:", ", ".join(s.name for s in SCENARIOS))
            return 1

    print(f"\nRunning {len(scenarios_to_run)} behavioral scenarios...")
    for i, scenario in enumerate(scenarios_to_run):
        print(f"  [{i+1}/{len(scenarios_to_run)}] {scenario.name}...", end=" ", flush=True)
        t_s = time.monotonic()
        result = await run_scenario(scenario)
        elapsed = int((time.monotonic() - t_s) * 1000)
        status = "PASS" if result.passed else "FAIL"
        print(f"{status} ({elapsed}ms)")
        if not result.passed:
            for reason in result.failure_reasons:
                print(f"          ! {reason}")
        scenario_results.append(result)

    # ── Run full simulations ───────────────────────────────────────────────────
    if not args.scenarios_only:
        domain = VLSIDomain.ANALOG_LAYOUT  # default domain for simulations

        profiles_to_run = build_profiles(domain)
        if args.profile:
            profiles_to_run = [p for p in profiles_to_run if p.type.value == args.profile]
            if not profiles_to_run:
                print(f"ERROR: Profile '{args.profile}' not found.")
                print("Available:", ", ".join(t.value for t in CandidateType))
                return 1

        turns = 4 if not args.full else 6

        print(f"\nRunning {len(profiles_to_run)} full simulations ({turns} turns each)...")
        for i, profile in enumerate(profiles_to_run):
            print(f"  [{i+1}/{len(profiles_to_run)}] {profile.type.value}...", end=" ", flush=True)
            t_p = time.monotonic()
            result = await run_full_simulation(
                profile=profile,
                turns=turns,
                use_llm_for_answers=args.full,
                evaluate_humanness_score=args.humanness,
            )
            elapsed = int((time.monotonic() - t_p) * 1000)
            status = "PASS" if result.overall_passed else "FAIL"
            print(f"{status} ({elapsed}ms)")
            simulation_results.append(result)

    # ── Render report ─────────────────────────────────────────────────────────
    total_elapsed = int((time.monotonic() - t_start) * 1000)
    report = render_full_report(simulation_results, scenario_results)
    print()
    print(report)
    print(f"\nTotal time: {total_elapsed}ms")

    # Write to file
    results_dir = Path(__file__).parent / "simulation" / "results"
    results_dir.mkdir(exist_ok=True)
    output_path = results_dir / "latest.txt"
    output_path.write_text(report)
    print(f"Report written to: {output_path}")

    # Exit code
    scenario_ok = all(r.passed for r in scenario_results)
    sim_ok = all(r.overall_passed for r in simulation_results)
    return 0 if (scenario_ok and sim_ok) else 1


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="VLSI Interviewer Realism Simulation Harness")
    p.add_argument("--full",           action="store_true", help="Use LLM for candidate answers (more realistic, higher cost)")
    p.add_argument("--humanness",      action="store_true", help="Enable LLM-as-judge humanness scoring")
    p.add_argument("--scenarios-only", action="store_true", help="Run behavioral scenarios only, skip full simulation")
    p.add_argument("--profile",        type=str,            help="Run specific profile only (e.g. weak, strong, vague)")
    p.add_argument("--scenario",       type=str,            help="Run specific scenario only")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    exit_code = asyncio.run(main(args))
    sys.exit(exit_code)
