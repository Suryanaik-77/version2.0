"""
taxonomy.py — Domain concept taxonomy + topic coverage tracking.

Ported from V1 strategy_engine.py CONCEPT_TAXONOMY.
Pure data + pure logic. No I/O, no LLM calls.

Usage:
    topics = get_topics("physical_design")
    next_topic = pick_next_topic("physical_design", coverage, turn_count)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import random


@dataclass
class Concept:
    id: str
    topic: str
    description: str
    depth: int  # 0=foundational, 1=basic, 2=intermediate, 3=advanced, 4=expert


# ── Concept Taxonomy ─────────────────────────────────────────────────────────

TAXONOMY: dict[str, dict[str, list[Concept]]] = {

    "PHYSICAL_DESIGN": {
        "floorplanning": [
            Concept("pd_fp_purpose",       "floorplanning", "Purpose and objectives of floorplanning", 0),
            Concept("pd_fp_utilization",   "floorplanning", "Utilization targets and trade-offs with routability", 1),
            Concept("pd_fp_macro",         "floorplanning", "Macro placement strategy for timing and congestion", 2),
            Concept("pd_fp_power_domains", "floorplanning", "Power domain planning and voltage islands", 3),
            Concept("pd_fp_hierarchical",  "floorplanning", "Hierarchical floorplanning and partitioning", 4),
        ],
        "power_planning": [
            Concept("pd_pp_purpose",  "power_planning", "PDN fundamentals — rings, straps, mesh", 0),
            Concept("pd_pp_ir_drop",  "power_planning", "IR drop causes, impact on timing, mitigation", 2),
            Concept("pd_pp_em",       "power_planning", "Electromigration rules and current density", 3),
            Concept("pd_pp_dynamic",  "power_planning", "Dynamic IR analysis and switching impact", 4),
        ],
        "placement": [
            Concept("pd_pl_purpose",       "placement", "Placement objectives and algorithms", 0),
            Concept("pd_pl_timing",        "placement", "Timing-driven placement and critical paths", 2),
            Concept("pd_pl_congestion",    "placement", "Congestion analysis and relief techniques", 2),
            Concept("pd_pl_scan",          "placement", "Scan chain optimization during placement", 3),
        ],
        "clock_tree_synthesis": [
            Concept("pd_cts_purpose",   "clock_tree_synthesis", "CTS purpose — what problem it solves", 0),
            Concept("pd_cts_skew",      "clock_tree_synthesis", "Clock skew definition and targets", 1),
            Concept("pd_cts_buffer",    "clock_tree_synthesis", "Buffer/inverter chain sizing", 2),
            Concept("pd_cts_debug",     "clock_tree_synthesis", "CTS debug — diagnosing skew after CTS", 2),
            Concept("pd_cts_tool",      "clock_tree_synthesis", "ICC2-specific CTS commands", 3),
            Concept("pd_cts_useful",    "clock_tree_synthesis", "Useful skew for timing closure", 4),
        ],
        "routing": [
            Concept("pd_rt_global",    "routing", "Global routing concepts", 0),
            Concept("pd_rt_detailed",  "routing", "Detailed routing — DRC rules", 2),
            Concept("pd_rt_crosstalk", "routing", "Crosstalk noise and timing delta", 3),
            Concept("pd_rt_si",        "routing", "Signal integrity — shielding and ECO", 4),
        ],
        "timing_closure": [
            Concept("pd_tc_basics",  "timing_closure", "STA fundamentals — setup, hold, slack", 1),
            Concept("pd_tc_setup",   "timing_closure", "Setup violation identification and ECO", 2),
            Concept("pd_tc_hold",    "timing_closure", "Hold violation root causes and fixes", 2),
            Concept("pd_tc_ocv",     "timing_closure", "On-chip variation — derating, CPPR", 3),
            Concept("pd_tc_mcmm",    "timing_closure", "Multi-corner multi-mode analysis", 4),
        ],
    },

    "ANALOG_LAYOUT": {
        "basic_layout": [
            Concept("al_bl_layers",   "basic_layout", "Layer stack — diffusion, poly, metal", 0),
            Concept("al_bl_rules",    "basic_layout", "Design rules — spacing, width, enclosure", 1),
            Concept("al_bl_lvs",      "basic_layout", "LVS — what it checks and why it fails", 2),
            Concept("al_bl_wells",    "basic_layout", "N-well, P-well, deep N-well", 1),
            Concept("al_bl_debug",    "basic_layout", "Common LVS errors and debug approach", 3),
        ],
        "device_matching": [
            Concept("al_dm_purpose",  "device_matching", "Why matching matters — offset, gain error", 0),
            Concept("al_dm_tech",     "device_matching", "Interdigitation and common centroid", 1),
            Concept("al_dm_gradient", "device_matching", "Process gradients and matching impact", 2),
            Concept("al_dm_pelgrom",  "device_matching", "Pelgrom model and mismatch quantification", 3),
            Concept("al_dm_mirror",   "device_matching", "Current mirror layout and matching", 2),
            Concept("al_dm_diff",     "device_matching", "Differential pair layout and symmetry", 3),
        ],
        "parasitic_awareness": [
            Concept("al_pa_cap",        "parasitic_awareness", "Parasitic capacitance sources", 1),
            Concept("al_pa_res",        "parasitic_awareness", "Sheet resistance and via resistance", 2),
            Concept("al_pa_extraction", "parasitic_awareness", "Parasitic extraction and post-layout sim", 3),
            Concept("al_pa_coupling",   "parasitic_awareness", "Substrate coupling in mixed-signal", 3),
            Concept("al_pa_postlayout", "parasitic_awareness", "Post-layout vs pre-layout differences", 2),
        ],
        "latchup_esd": [
            Concept("al_lu_mechanism",  "latchup_esd", "Latch-up mechanism — parasitic BJT", 1),
            Concept("al_lu_prevention", "latchup_esd", "Guard rings and substrate contacts", 2),
            Concept("al_esd_protect",   "latchup_esd", "ESD protection structures", 3),
            Concept("al_esd_io",        "latchup_esd", "I/O pad ESD and power clamp design", 4),
        ],
        "analog_routing": [
            Concept("al_ar_shield",  "analog_routing", "Shielding sensitive signals", 1),
            Concept("al_ar_sym",     "analog_routing", "Symmetric routing for differential paths", 2),
            Concept("al_ar_finger",  "analog_routing", "Multi-finger transistor layout", 2),
            Concept("al_ar_power",   "analog_routing", "Clean power routing for low-noise blocks", 3),
        ],
        "process_tech": [
            Concept("al_pt_cmos",    "process_tech", "CMOS process flow — NMOS and PMOS", 0),
            Concept("al_pt_lde",     "process_tech", "Layout dependent effects — STI, WPE, LOD", 2),
            Concept("al_pt_finfet",  "process_tech", "FinFET layout vs planar CMOS", 3),
            Concept("al_pt_rel",     "process_tech", "Reliability — NBTI, HCI, TDDB", 4),
        ],
    },

    "DESIGN_VERIFICATION": {
        "verification_methods": [
            Concept("dv_vm_overview",  "verification_methods", "Verification planning and coverage", 0),
            Concept("dv_vm_dir_rand",  "verification_methods", "Directed vs constrained random", 1),
            Concept("dv_vm_uvm",       "verification_methods", "UVM architecture — agent, scoreboard", 2),
            Concept("dv_vm_phases",    "verification_methods", "UVM phases — build, connect, run", 2),
            Concept("dv_vm_formal",    "verification_methods", "Formal verification — bounded vs unbounded", 3),
            Concept("dv_vm_emulation", "verification_methods", "Emulation and prototyping", 4),
        ],
        "systemverilog": [
            Concept("dv_sv_types",       "systemverilog", "Data types — logic, packed, unpacked", 0),
            Concept("dv_sv_interfaces",  "systemverilog", "Interfaces and modports", 1),
            Concept("dv_sv_constraints", "systemverilog", "Constraint random — dist, soft, solve", 2),
            Concept("dv_sv_classes",     "systemverilog", "OOP — class, inheritance, factory", 2),
            Concept("dv_sv_threads",     "systemverilog", "Threads — fork/join, mailbox, semaphore", 3),
        ],
        "functional_coverage": [
            Concept("dv_fc_purpose",   "functional_coverage", "Functional vs code coverage", 0),
            Concept("dv_fc_covergrp",  "functional_coverage", "Covergroup and coverpoint definition", 2),
            Concept("dv_fc_cross",     "functional_coverage", "Cross coverage and explosion problem", 3),
            Concept("dv_fc_closure",   "functional_coverage", "Coverage closure — closing holes", 3),
            Concept("dv_fc_metrics",   "functional_coverage", "When is coverage enough?", 4),
        ],
        "assertions_sva": [
            Concept("dv_sva_purpose",   "assertions_sva", "Why assertions — catching bugs at source", 0),
            Concept("dv_sva_immediate", "assertions_sva", "Immediate assertions — syntax", 1),
            Concept("dv_sva_conc",      "assertions_sva", "Concurrent assertions — clock, property", 2),
            Concept("dv_sva_temporal",  "assertions_sva", "Temporal operators — ##, |=>, |->", 3),
            Concept("dv_sva_vacuous",   "assertions_sva", "Vacuous passing — assertion never fires", 3),
        ],
        "uvm_components": [
            Concept("dv_uvm_agent",    "uvm_components", "Agent structure — driver, monitor, sequencer", 1),
            Concept("dv_uvm_seq",      "uvm_components", "Sequences — body, start_item, finish_item", 2),
            Concept("dv_uvm_sb",       "uvm_components", "Scoreboard — reference model, comparison", 2),
            Concept("dv_uvm_config",   "uvm_components", "Configuration — uvm_config_db", 3),
            Concept("dv_uvm_ral",      "uvm_components", "RAL — register abstraction layer", 4),
        ],
        "debugging": [
            Concept("dv_db_waveform",   "debugging", "Waveform debugging — root cause from sim", 1),
            Concept("dv_db_protocol",   "debugging", "Protocol debug — AXI/APB violations", 2),
            Concept("dv_db_x",          "debugging", "X-propagation — uninitialized signals", 2),
            Concept("dv_db_regression", "debugging", "Regression failure triage", 3),
            Concept("dv_db_formal_cex", "debugging", "Counterexample analysis in formal", 4),
        ],
    },
}


# ── Topic Selection ──────────────────────────────────────────────────────────

def get_topics(domain: str) -> list[str]:
    """Returns list of topic names for a domain."""
    return list(TAXONOMY.get(domain, {}).keys())


def get_concepts(domain: str, topic: str) -> list[Concept]:
    """Returns concepts for a topic."""
    return TAXONOMY.get(domain, {}).get(topic, [])


def pick_next_topic(
    domain: str,
    coverage: dict[str, int],  # topic -> questions asked count
    turn_count: int,
    last_topic: str = "",
    max_consecutive: int = 3,
) -> tuple[str, Concept]:
    """
    Pick next topic + concept based on coverage gaps.
    Returns (topic_name, concept) tuple.

    Logic:
    - Least-covered topic first (ensures breadth)
    - Don't repeat same topic more than max_consecutive times
    - Pick concept at appropriate depth for turn progression
    """
    topics = get_topics(domain)
    if not topics:
        return ("general", Concept("general", "general", "General VLSI question", 1))

    # Count consecutive on last topic
    last_count = coverage.get(last_topic, 0)

    # Score each topic — lower coverage = higher priority
    scored = []
    for t in topics:
        asked = coverage.get(t, 0)
        penalty = 100 if (t == last_topic and last_count >= max_consecutive) else 0
        score = asked + penalty
        scored.append((score, t))

    scored.sort(key=lambda x: x[0])
    chosen_topic = scored[0][1]

    # Pick concept at appropriate depth
    concepts = get_concepts(domain, chosen_topic)
    if not concepts:
        return (chosen_topic, Concept(f"{chosen_topic}_gen", chosen_topic, f"General {chosen_topic} question", 1))

    # Depth progression: early turns = low depth, later = higher
    target_depth = min(4, turn_count // 4)
    # Find closest concept to target depth that hasn't been overasked
    best = min(concepts, key=lambda c: abs(c.depth - target_depth))
    return (chosen_topic, best)


def get_concept_count(domain: str) -> int:
    """Total concept nodes for a domain."""
    return sum(len(concepts) for concepts in TAXONOMY.get(domain, {}).values())
