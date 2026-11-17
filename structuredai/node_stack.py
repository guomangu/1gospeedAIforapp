"""Node.js / TypeScript monorepo command resolution, test suite & dependency scan."""

import json
import shlex
import shutil
from pathlib import Path

from structuredai.monorepo_router import discover_subprojects


def _find_tsx_loader(sub_dir: Path, project_dir: Path) -> Path | None:
    cand = sub_dir / "node_modules" / "tsx" / "dist" / "loader.mjs"
    if cand.is_file():
        return cand
    for s in discover_subprojects(project_dir):
        alt = s / "node_modules" / "tsx" / "dist" / "loader.mjs"
        if alt.is_file():
            return alt
    return None


def node_commands(sub_dir: Path, project_dir: Path) -> dict[str, str]:
    """Resolve real executable syntax/lint/test/build commands for a JS/TS package."""
    nb = sub_dir / "node_modules" / ".bin"
    tsc = "./node_modules/.bin/tsc --noEmit" if (nb / "tsc").exists() else ("tsc --noEmit" if shutil.which("tsc") else "")
    lint = "./node_modules/.bin/eslint {file}" if (nb / "eslint").exists() else ""
    tsx_ldr = _find_tsx_loader(sub_dir, project_dir)
    if (nb / "vitest").exists():
        test_t = "./node_modules/.bin/vitest run {test_file}"
    elif tsx_ldr:
        test_t = f"node --import {shlex.quote(str(tsx_ldr))} --test {{test_file}}"
    else:
        test_t = "node --test {test_file}"
    scripts = {}
    pkg_p = sub_dir / "package.json"
    if pkg_p.is_file():
        try:
            scripts = json.loads(pkg_p.read_text(encoding="utf-8")).get("scripts", {})
        except (OSError, json.JSONDecodeError):
            pass
    bld = "npm run build" if "build" in scripts else tsc
    return {"syntax_check": tsc, "lint": lint, "test_targeted": test_t, "build_check": bld}


def collect_node_deps(project_dir: Path) -> tuple[list[str], set[str]]:
    """Collect real npm dependency names across root and subprojects for DEPS_REALITY."""
    lines: list[str] = []
    pkgs: set[str] = {"node:test", "node:assert", "node:crypto", "node:http", "node:fs", "node:path"}
    for sub in discover_subprojects(project_dir) or [project_dir]:
        pkg_p = sub / "package.json"
        if not pkg_p.is_file():
            continue
        try:
            data = json.loads(pkg_p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rel_sub = "." if sub == project_dir else sub.name
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        pkgs.update(deps.keys())
        if sample := ", ".join(sorted(deps.keys())[:10]):
            lines.append(f"- `{rel_sub}/package.json`: {sample}")
    return lines, pkgs


def run_node_suite(adapter) -> tuple[bool, str]:
    """Run all JS/TS subproject test suites across a monorepo."""
    outs: list[str] = []
    for sub in discover_subprojects(adapter.project_dir):
        tfiles = sorted(str(p.relative_to(sub)) for p in (sub / "tests").rglob("*.test.*")) if (sub / "tests").is_dir() else []
        if not tfiles:
            continue
        tpl = node_commands(sub, adapter.project_dir)["test_targeted"]
        cmd = tpl.replace("{test_file}", " ".join(shlex.quote(t) for t in tfiles))
        ok, out = adapter.run_cmd(cmd, {}, check_tool=False, cwd=sub)
        outs.append(out)
        if not ok:
            return False, "\n".join(outs)
    return True, "\n".join(outs)


def run_node_build(adapter) -> tuple[bool, str]:
    """Run build/typecheck across all JS/TS subprojects."""
    for sub in discover_subprojects(adapter.project_dir):
        cmd = node_commands(sub, adapter.project_dir).get("build_check", "")
        ok, out = adapter.run_cmd(cmd, adapter._ctx(base_dir=sub), cwd=sub)
        if not ok:
            return False, out
    return True, ""
