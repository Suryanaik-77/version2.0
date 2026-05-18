"""
run_chaos.py — Chaos validation harness entry point.

Usage:
  # Mock LLM (fast, structural validation only, no API key needed):
  python tests/run_chaos.py --mock

  # Real LLM (full behavioral validation, requires OPENAI_API_KEY):
  python tests/run_chaos.py

  # Real LLM, specific chaos type:
  python tests/run_chaos.py --type overconfident_wrong

  # Skip concurrent stress tests:
  python tests/run_chaos.py --no-stress

  # Run only stress tests:
  python tests/run_chaos.py --stress-only
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("JWT_SECRET_KEY", "chaos-test-key")

import structlog
from app.config import get_settings
from app.models.session import VLSIDomain
from tests.chaos.candidates import ChaosType, get_chaos_by_type, get_chaos_answers
from tests.chaos.injector import run_chaos_scenario, run_all_chaos
from tests.chaos.stress import run_concurrent_sessions, measure_baseline_latency
from tests.chaos.report import ChaosValidationReport, render

log = structlog.get_logger(__name__)


async def main(args: argparse.Namespace) -> int:
    settings = get_settings()
    use_mock = args.mock or not settings.OPENAI_API_KEY

    if use_mock:
        print("\n[MOCK MODE] No OPENAI_API_KEY — using mock LLM for structural tests")
    else:
        print("\n[REAL LLM MODE] Using OpenAI API for full behavioral validation")

    t_start = time.monotonic()

    # ── Run chaos behavioral tests ─────────────────────────────────────────────
    chaos_results = []
    if not args.stress_only:
        if args.type:
            try:
                chaos_type = ChaosType(args.type)
                chaos_list = get_chaos_by_type(chaos_type)
            except ValueError:
                print(f"Unknown chaos type: {args.type}")
                print("Available:", ", ".join(t.value for t in ChaosType))
                return 1
        else:
            chaos_list = get_chaos_answers()

        print(f"\nRunning {len(chaos_list)} chaos behavioral scenarios...")
        for i, chaos in enumerate(chaos_list):
            print(f"  [{i+1}/{len(chaos_list)}] {chaos.chaos_type.value} ({chaos.domain.value})...", end=" ", flush=True)
            t_s = time.monotonic()
            result = await run_chaos_scenario(
                chaos,
                session_id=f"chaos-{i}",
                prior_claims=["I used common centroid for all matched pairs."] if chaos.chaos_type == ChaosType.SELF_CORRECTION else [],
                use_mock_llm=use_mock,
            )
            elapsed = int((time.monotonic() - t_s) * 1000)
            status = "PASS" if result.passed else f"FAIL({result.failure_class})"
            print(f"{status} ({elapsed}ms)")
            if result.failure_details:
                for d in result.failure_details[:1]:
                    print(f"          ! {d[:70]}")
            chaos_results.append(result)

    # ── Run concurrent stress tests ────────────────────────────────────────────
    stress_5 = stress_10 = None
    baseline = {"p50": 0, "p95": 0, "max": 0}

    if not args.no_stress:
        print("\nMeasuring baseline latency (3 runs)...")
        baseline = await measure_baseline_latency(use_mock_llm=use_mock, runs=3)
        print(f"  Baseline: p50={baseline['p50']}ms p95={baseline['p95']}ms")

        print("\nRunning 5-session concurrent stress test...")
        stress_5 = await run_concurrent_sessions(5, use_mock_llm=use_mock)
        status = "PASS" if stress_5.passed else "FAIL"
        print(f"  [{status}] {stress_5.sessions_completed}/5 completed | "
              f"p50={stress_5.latency_p50_ms}ms p95={stress_5.latency_p95_ms}ms | "
              f"wall={stress_5.total_wall_clock_ms}ms | "
              f"contamination={len(stress_5.cross_contamination)}")

        print("\nRunning 10-session concurrent stress test...")
        stress_10 = await run_concurrent_sessions(10, use_mock_llm=use_mock)
        status = "PASS" if stress_10.passed else "FAIL"
        print(f"  [{status}] {stress_10.sessions_completed}/10 completed | "
              f"p50={stress_10.latency_p50_ms}ms p95={stress_10.latency_p95_ms}ms | "
              f"wall={stress_10.total_wall_clock_ms}ms | "
              f"contamination={len(stress_10.cross_contamination)}")

    # ── Render report ──────────────────────────────────────────────────────────
    report_obj = ChaosValidationReport(
        chaos_results=chaos_results,
        stress_5=stress_5,
        stress_10=stress_10,
        baseline_latency=baseline,
        use_mock_llm=use_mock,
    )

    total_ms = int((time.monotonic() - t_start) * 1000)
    report_text = render(report_obj)
    print()
    print(report_text)
    print(f"\nTotal time: {total_ms}ms")

    # Write report
    results_dir = Path(__file__).parent / "chaos" / "results"
    results_dir.mkdir(exist_ok=True)
    out_path = results_dir / "latest_chaos.txt"
    out_path.write_text(report_text)
    print(f"Report written to: {out_path}")

    # Exit code
    verdict = "READY" in report_text.split("VERDICT:")[-1]
    return 0 if verdict else 1


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Chaos Stability Validation Harness")
    p.add_argument("--mock",        action="store_true", help="Use mock LLM (no API key needed)")
    p.add_argument("--no-stress",   action="store_true", help="Skip concurrent stress tests")
    p.add_argument("--stress-only", action="store_true", help="Run stress tests only")
    p.add_argument("--type",        type=str,            help="Run specific chaos type only")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    sys.exit(asyncio.run(main(args)))
