"""Safe brownfield indexing (skipping node_modules/data/docs) & architecture brief."""

import os
from pathlib import Path

from structuredai.ast_reality import extract_symbols, format_reality_md, legacy_outline
from structuredai.config import Config
from structuredai.monorepo_router import IGNORED_DIRS, discover_subprojects


def iter_source_files(target_dir: Path, code_exts: list[str], state_dir_name: str) -> list[Path]:
    """Walk target_dir safely with os.walk pruning IGNORED_DIRS and unreadable dirs."""
    out: list[Path] = []
    skip = set(IGNORED_DIRS) | {state_dir_name}
    exts = set(code_exts)
    for root, dirs, files in os.walk(target_dir, onerror=lambda _e: None):
        dirs[:] = sorted(d for d in dirs if d not in skip and not d.startswith("."))
        rp = Path(root)
        for fn in sorted(files):
            fp = rp / fn
            if fp.suffix in exts:
                out.append(fp)
    return out


def index_brownfield(cfg: Config, meta_dir: Path, code_exts: list[str], exports: dict[str, set[str]]) -> None:
    """Scan real project source files deterministically to seed REALITY.md & exports."""
    for p in iter_source_files(cfg.target_dir, code_exts, cfg.state_dir_name):
        try:
            if p.stat().st_size > cfg.max_ingest_bytes:
                continue
        except OSError:
            continue
        rel = str(p.relative_to(cfg.target_dir))
        txt = p.read_text(encoding="utf-8", errors="ignore")
        syms = extract_symbols(txt, p.suffix)
        exports[rel] = set(syms.get("exports", []))
        rpath = meta_dir / rel / "REALITY.md"
        if not rpath.exists():
            rpath.parent.mkdir(parents=True, exist_ok=True)
            extra = legacy_outline(p, cfg.max_ingest_bytes) if len(txt.splitlines()) > cfg.max_lines_per_file else "Existing module"
            rpath.write_text(format_reality_md(rel, len(txt.splitlines()), syms, extra), encoding="utf-8")


def build_existing_brief(cfg: Config, nodes: dict, exports: dict[str, set[str]]) -> str:
    """Summarize existing subprojects, files and locked nodes for 0_ARCHITECT."""
    subs = [s.name for s in discover_subprojects(cfg.target_dir) if s != cfg.target_dir]
    lines: list[str] = []
    if subs:
        lines.append(f"Subprojects: {', '.join(subs)}")
    existing = sorted(set(exports.keys()) | set(nodes.keys()))
    if existing:
        lines.append("Existing/planned files (do NOT duplicate):")
        for rel in existing[:25]:
            st = nodes.get(rel, {}).get("state", "EXISTING")
            lines.append(f"- {rel} [{st}]")
    return "\n".join(lines)


def resolve_deps(raw_deps: list[str], valid: set[str]) -> list[str]:
    """Keep valid node/existing dependencies and resolve unique suffix matches (e.g. schema.ts)."""
    out: list[str] = []
    for raw in raw_deps:
        d = raw.lstrip("./")
        if d in valid:
            out.append(d)
        elif (matches := [v for v in sorted(valid) if v.endswith("/" + Path(d).name)]) and len(matches) == 1:
            out.append(matches[0])
    return list(dict.fromkeys(out))
