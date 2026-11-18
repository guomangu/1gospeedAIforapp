"""Multi-wave expansion and H3_HARDEN (7_HARDENING) loop controller."""

import re
from pathlib import Path

from structuredai.pass_critique import run_architect
from structuredai.state_store import atomic_write_json
from structuredai.time_budget import TimeBudgetManager

_REQ_RE = re.compile(r"\b(REQ-\d+)\b")


def extract_spec_reqs(spec_text: str) -> list[str]:
    """Return unique ordered REQ-* IDs declared in SPEC.md."""
    seen: list[str] = []
    for m in _REQ_RE.findall(spec_text):
        if m not in seen:
            seen.append(m)
    return seen


def uncovered_reqs(spec_text: str, nodes: dict) -> list[str]:
    """Return REQ-* IDs not yet mapped to any 6_LOCKED node."""
    covered: set[str] = set()
    for info in nodes.values():
        if info.get("state") == "6_LOCKED":
            covered.update(info.get("req_ids", []))
    return [r for r in extract_spec_reqs(spec_text) if r not in covered]


def handle_idle_or_next_wave(ctx: dict, state: dict, tbm: TimeBudgetManager, state_path: Path) -> str | None:
    """Plan next wave if REQs remain, else run bounded H3 hardening checks; returns stop reason or None."""
    nodes = state["nodes"]
    tb = state["time_budget"]
    waves = int(tb.get("architect_waves", 1))
    missing = uncovered_reqs(ctx["spec_text"], nodes)
    if missing and waves < 6 and not tbm.is_frozen():
        hint = f"WAVE_{waves + 1}: Uncovered REQs: {', '.join(missing)}. Add ONLY new files not in existing list; return [] if complete."
        try:
            added = run_architect(ctx, ctx["spec_text"], allow_empty=True, fix_hint=hint)
            new_nodes = [p for p in added if nodes.get(p, {}).get("state") == "1_DRAFT"]
            tb["architect_waves"] = (waves + 1) if new_nodes else 99
            if new_nodes:
                tb["idle_hardening_cycles"] = 0
                atomic_write_json(state_path, state)
                return None
        except (RuntimeError, ValueError, TypeError, KeyError, OSError):
            tb["architect_waves"] = waves + 1

    ok_suite, _ = ctx["adapter"].test_suite()
    ok_bld, _ = ctx["adapter"].build_check()
    if ok_suite and ok_bld and tb.get("last_green_tag") is None and nodes:
        tb["last_green_tag"] = ctx["git"].tag_green_step(1)
    idle = int(tb.get("idle_hardening_cycles", 0)) + 1
    tb["idle_hardening_cycles"] = idle
    if idle >= ctx["cfg"].max_idle_hardening or (not missing and ok_suite and ok_bld):
        return "ALL_NODES_RESOLVED"
    return None
