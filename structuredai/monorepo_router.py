"""Monorepo & subproject discovery and path routing."""

from pathlib import Path

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
