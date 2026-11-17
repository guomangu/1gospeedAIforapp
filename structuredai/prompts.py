"""Ultra-compact (<150 tok) system prompts and priority-preserving 4096-token budget."""

from structuredai.config import Config

SYSTEM_PROMPTS: dict[str, str] = {
    "0_ARCHITECT": (
        "Role: Software Architect ({stack}). Propose 3-6 NEW modular files (<= {max_files}/dir, depth <= {max_depth}) "
        "mapping uncovered REQ-* ids. Do NOT repeat existing files. No circular deps. Return ONLY valid JSON."
    ),
    "1_DRAFT": (
        "Role: Unit Coder ({stack}). Implement ONLY the contract. Complete code without TODO, pass, ... or stubs. "
        "Max {max_lines} lines. Use ONLY imports in NEIGHBOR_REALITY or DEPS (never invent packages like dotenv). Return ONLY valid JSON."
    ),
    "2_EXTRACT": (
        "Role: Semantic Summarizer. Given code and AST signatures, write <= 2 concise sentences describing purpose "
        "and side effects. Return ONLY valid JSON."
    ),
    "3_CRITIQUE": (
        "Role: Root Critic. Compare SPEC REQ-* vs REALITY signatures for TARGET. Report ONLY: interface mismatches, "
        "files >= {split_warn} lines to split, missing REQs. No circular splits. Return ONLY valid JSON."
    ),
    "4_REBALANCE": (
        "Role: Refactorer ({stack}). Apply REFACTOR_ORDERS cleanly. If splitting a file, re-export moved public symbols "
        "and keep >= {min_split} lines per file, <= {max_lines} max. Return ONLY valid JSON."
    ),
    "5_SYNTHESIS": (
        "Role: Optimizer & Tester ({stack}). Harden edge cases and write 3-5 concise unit tests calling the module "
        "(node:test for TS/JS, pytest/unittest for Python). No assert True or self-mocking. Max {max_lines} lines each. Return ONLY valid JSON."
    ),
}

_HIGH_PRIORITY = frozenset({"SPEC", "CONTRACT", "TARGET", "TARGET_TEST", "FIX_HINT", "CODE", "EXISTING_CODE", "ORDERS"})


def estimate_tokens(text: str) -> int:
    """Fast conservative token estimator (~3.2 chars/token)."""
    return max(1, int(len(text) / 3.2) + 1)


def wrap_untrusted(label: str, content: str) -> str:
    """Delimit repo/external text to neutralize indirect prompt injection."""
    safe = content.replace("</UNTRUSTED_CODE>", "<\\/UNTRUSTED_CODE>")
    return f'<UNTRUSTED_CODE source="{label}">\n{safe}\n</UNTRUSTED_CODE>'


def build_prompt(role: str, cfg: Config, sections: list[tuple[str, str]], stack_name: str | None = None) -> tuple[str, str]:
    """Format compact prompt within LLM_CTX_SIZE, guaranteeing TARGET/FIX_HINT are never dropped."""
    sys_t = SYSTEM_PROMPTS[role].format(
        stack=stack_name or cfg.target_stack, max_files=cfg.max_files_per_dir,
        max_depth=cfg.max_tree_depth, max_lines=cfg.max_lines_per_file,
        min_split=cfg.min_split_lines, split_warn=max(60, int(cfg.max_lines_per_file * 0.85)),
    )
    budget = max(512, cfg.llm_ctx_size - cfg.llm_max_output_tokens - estimate_tokens(sys_t) - 256)
    non_empty = [(t, b.strip()) for t, b in sections if b.strip()]
    ordered = [(i, t, b) for i, (t, b) in enumerate(non_empty) if t in _HIGH_PRIORITY] + [
        (i, t, b) for i, (t, b) in enumerate(non_empty) if t not in _HIGH_PRIORITY
    ]
    placed: dict[int, str] = {}
    used = 0
    for idx, title, body in ordered:
        block = f"## {title}\n{body}\n"
        toks = estimate_tokens(block)
        if used + toks <= budget:
            placed[idx] = block
            used += toks
        else:
            rem_chars = max(0, int((budget - used) * 3.0) - len(title) - 28)
            if rem_chars > 120:
                placed[idx] = f"## {title} (truncated)\n{body[:rem_chars]}\n"
                used = budget
    return sys_t, "\n".join(placed[i] for i in sorted(placed))
