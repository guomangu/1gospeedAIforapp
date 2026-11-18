"""Atomic state persistence, steering application & architect bootstrap retries."""

import json
import os
import time
from pathlib import Path

from structuredai.brownfield import index_brownfield
from structuredai.config import Config
from structuredai.pass_critique import run_architect
from structuredai.steering import SteeringDirectives, consume_directives

__all__ = ["apply_steering", "atomic_write_json", "index_brownfield", "init_architect_with_retries", "load_or_init_state"]


def atomic_write_json(path: Path, data: dict) -> None:
    """Persist state.json atomically via temp file + fsync + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2, ensure_ascii=False)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(tmp, path)


def load_or_init_state(cfg: Config, state_path: Path, mode: str, stack_name: str) -> dict:
    """Load resumable state.json or initialize a fresh schema v3 state."""
    if cfg.resume_mode != "fresh" and state_path.is_file():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {
        "schema_version": 3, "run_id": f"run_{int(time.time())}",
        "mode": mode, "stack": stack_name,
        "time_budget": {"elapsed_active_seconds": 0.0, "last_green_tag": None, "architect_waves": 1, "idle_hardening_cycles": 0},
        "nodes": {},
    }


def apply_steering(nodes: dict, d: SteeringDirectives, steering_path: Path) -> list[str]:
    """Apply live human PRIORITY / RETRY / SKIP commands and return consumed lines."""
    done: list[str] = []
    for p in d.priorities:
        if p in nodes:
            nodes[p]["priority_boost"] = 50
            done.append(f"PRIORITY: {p}")
    for p in d.retries:
        if p in nodes:
            nodes[p].update({"state": "1_DRAFT", "retries": 0, "last_error": "", "history_hashes": []})
            done.append(f"RETRY: {p}")
    for p in d.skips:
        if p in nodes:
            nodes[p]["state"] = "SKIPPED"
            done.append(f"SKIP: {p}")
    consume_directives(steering_path, done)
    return done


def init_architect_with_retries(ctx: dict, state: dict, state_path: Path) -> None:
    """Retry initial 0_ARCHITECT decomposition up to max_retries on small-LLM format errors."""
    last_err = ""
    exts = ctx["stack"].get("code_exts", [])
    for attempt in range(ctx["cfg"].max_retries):
        try:
            tmp = min(0.65, ctx["cfg"].llm_temperature + 0.12 * attempt) if attempt > 0 else None
            run_architect(ctx, ctx["spec_text"], fix_hint=last_err, temp=tmp)
            atomic_write_json(state_path, state)
            return
        except (RuntimeError, ValueError, TypeError, KeyError, OSError, PermissionError) as exc:
            last_err = f"Previous error: {str(exc)[:220]}. Every path MUST be unique and end with {exts}."
    raise RuntimeError(f"0_ARCHITECT failed after retries: {last_err}")
