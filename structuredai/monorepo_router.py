"""Monorepo & subproject discovery and path routing."""

import posixpath
import re
from pathlib import Path

_TS_SPEC_RE = re.compile(r"""(?:import|export)\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]""")
_NODE_CORE = frozenset({"crypto", "http", "https", "fs", "path", "url", "util", "assert", "events", "stream"})

IGNORED_DIRS = frozenset({
    ".git", ".venv", ".orchestrator", "node_modules", "data", "dist", "build",
    ".expo", "target", "__pycache__", ".pytest_cache", ".ruff_cache", "docs",
})
MANIFESTS = ("tsconfig.json", "package.json", "pyproject.toml", "Cargo.toml", "go.mod")


def discover_subprojects(project_dir: Path) -> list[Path]:
    """Return root if it has a manifest, else immediate subprojects."""
    if any((project_dir / m).is_file() for m in MANIFESTS):
        return [project_dir]
    subs: list[Path] = []
    try:
        entries = sorted(project_dir.iterdir())
    except OSError:
        return []
    for sub in entries:
        if sub.is_dir() and sub.name not in IGNORED_DIRS and not sub.name.startswith(".") and any((sub / m).is_file() for m in MANIFESTS):
            subs.append(sub)
    return subs


def route_subproject(project_dir: Path, rel_file: str) -> tuple[Path, str]:
    """Return (nearest_subproject_dir, path_relative_to_subproject) for rel_file."""
    if not rel_file:
        return project_dir, ""
    root = project_dir.resolve()
    full = (root / rel_file).resolve()
    cur = full.parent
    while cur.is_relative_to(root):
        if any((cur / m).is_file() for m in MANIFESTS):
            return cur, str(full.relative_to(cur))
        if cur == root:
            break
        cur = cur.parent
    return root, rel_file


def check_ts_imports(code: str, rel_path: str, known_exports: dict[str, set[str]], installed_pkgs: set[str]) -> list[str]:
    """Validate npm deps, disallow TS self-imports, and check local/NodeNext relative imports."""
    errs: list[str] = []
    avail = ", ".join(sorted(p for p in installed_pkgs if not p.startswith(("node:", "@types/")))[:15])
    rel_dir = posixpath.dirname(rel_path) if rel_path else ""
    self_stem = posixpath.splitext(rel_path)[0] if rel_path else ""
    for spec in _TS_SPEC_RE.findall(code):
        if spec.startswith(("node:", "@/")) or spec in _NODE_CORE:
            continue
        if spec.startswith("."):
            if not rel_path:
                continue
            target = posixpath.normpath(posixpath.join(rel_dir, spec))
            t_stem = re.sub(r"\.(?:[cm]?[jt]s|tsx)$", "", target)
            if t_stem == self_stem:
                errs.append(f"Self-import forbidden: '{rel_path}' cannot import '{spec}'. Define symbols locally.")
                continue
            if rel_path.startswith(("backend/", "harvester/")) and not spec.endswith((".js", ".json", ".mjs", ".cjs")):
                errs.append(f"NodeNext relative import '{spec}' must end with '.js' or be removed if file does not exist.")
            if known_exports and "shared-db/" not in target and not any(
                f"{t_stem}{s}" in known_exports for s in (".ts", ".tsx", "/index.ts", "/index.tsx")
            ):
                errs.append(f"Missing local module '{spec}' (resolved '{t_stem}.ts'). Remove import or define symbols locally.")
            continue
        if installed_pkgs:
            pkg = "/".join(spec.split("/")[:2]) if spec.startswith("@") else spec.split("/")[0]
            if pkg not in installed_pkgs:
                errs.append(f"Uninstalled npm package '{pkg}'. Use ONLY: {avail}")
    return errs
