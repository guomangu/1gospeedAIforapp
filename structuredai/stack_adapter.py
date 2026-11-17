"""Deterministic stack hook execution with killpg timeouts, monorepo routing & DEPS_REALITY."""

import os
import shlex
import shutil
import signal
import subprocess
import tempfile
from importlib import metadata
from pathlib import Path

from structuredai.config import Config
from structuredai.monorepo_router import route_subproject
from structuredai.node_stack import collect_node_deps, node_commands, run_node_build, run_node_suite
from structuredai.security_gate import sanitized_env


class StackAdapter:
    """Runs syntax, lint, test & build hooks safely inside isolated subprocesses."""

    def __init__(self, project_dir: Path, stack: dict, cfg: Config) -> None:
        self.project_dir, self.stack, self.cfg = project_dir, stack, cfg

    def run_cmd(self, cmd_tpl: str, ctx: dict[str, str], check_tool: bool = True, cwd: Path | None = None) -> tuple[bool, str]:
        """Run formatted command safely with shlex-quoted args, killpg timeout & sanitized env."""
        if not cmd_tpl.strip():
            return True, ""
        parts = shlex.split(cmd_tpl.format(**{k: (shlex.quote(v) if v else "") for k, v in ctx.items()}))
        if check_tool and parts and not parts[0].startswith("./") and shutil.which(parts[0]) is None:
            return True, f"[SKIP] tool '{parts[0]}' not installed"
        with tempfile.TemporaryDirectory(prefix="sai_sbx_") as sbx:
            env = sanitized_env(dict(os.environ), Path(sbx))
            if not self.cfg.allow_network_tests:
                env["HTTP_PROXY"] = env["HTTPS_PROXY"] = "http://127.0.0.1:0"
                env["NO_PROXY"] = env["no_proxy"] = "localhost,127.0.0.1,::1"
            try:
                proc = subprocess.Popen(parts, cwd=cwd or self.project_dir, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
            except FileNotFoundError:
                return False, f"Command not found: {parts[0]}"
            try:
                out, _ = proc.communicate(timeout=self.cfg.subprocess_timeout_sec)
                return proc.returncode == 0, out or ""
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL); proc.wait()
                return False, f"TIMEOUT after {self.cfg.subprocess_timeout_sec}s"

    def _ctx(self, rel_file: str = "", test_file: str = "", base_dir: Path | None = None) -> dict[str, str]:
        p = Path(rel_file) if rel_file else Path(".")
        return {"file": rel_file, "dir": str(p.parent), "stem": p.stem, "test_file": test_file, "project_dir": str(base_dir or self.project_dir)}

    def _route_node(self, rel_file: str, test_file: str, key: str) -> tuple[Path, str, dict[str, str]]:
        sub, sub_rel = route_subproject(self.project_dir, rel_file)
        t_sub = str((self.project_dir / test_file).resolve().relative_to(sub.resolve())) if test_file else ""
        cmd = self.cfg.custom_cmds.get(key) or node_commands(sub, self.project_dir).get(key, "")
        return sub, cmd, self._ctx(sub_rel, t_sub, sub)

    def _run_hook(self, key: str, rel_file: str = "", test_file: str = "", check_tool: bool = True) -> tuple[bool, str]:
        if self.stack.get("name") == "node_ts" and not self.cfg.custom_cmds.get(key):
            sub, cmd, ctx = self._route_node(rel_file, test_file, key)
            return self.run_cmd(cmd, ctx, check_tool=check_tool, cwd=sub)
        return self.run_cmd(self.stack.get(key, ""), self._ctx(rel_file, test_file), check_tool=check_tool)

    def check_syntax(self, rel_file: str) -> tuple[bool, str]:
        return (True, "") if Path(rel_file).suffix.lower() in self.cfg.exempt_exts else self._run_hook("syntax_check", rel_file)

    def lint(self, rel_file: str) -> tuple[bool, str]:
        return self._run_hook("lint", rel_file)

    def test_targeted(self, rel_file: str, test_file: str) -> tuple[bool, str]:
        return self._run_hook("test_targeted", rel_file, test_file, check_tool=False)

    def test_suite(self) -> tuple[bool, str]:
        return run_node_suite(self) if (self.stack.get("name") == "node_ts" and not self.cfg.custom_cmds.get("test_targeted")) else self.run_cmd(self.stack.get("test_suite", ""), self._ctx(), check_tool=False)

    def build_check(self) -> tuple[bool, str]:
        return run_node_build(self) if (self.stack.get("name") == "node_ts" and not self.cfg.custom_cmds.get("build_check")) else self.run_cmd(self.stack.get("build_check", ""), self._ctx())


def build_deps_reality(project_dir: Path, stack_name: str) -> tuple[str, set[str]]:
    """Build deterministic DEPS_REALITY.md and return known installed package roots."""
    pkgs: set[str] = set()
    lines = ["# DEPS_REALITY (Packages détectés déterministement)"]
    if stack_name == "node_ts":
        dep_lines, pkgs = collect_node_deps(project_dir)
        return "\n".join((lines + dep_lines)[:30]) + "\n", pkgs
    if stack_name == "python":
        for dist in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
            name = dist.metadata["Name"]
            if (norm := name.lower().replace("-", "_")) not in pkgs:
                pkgs.add(norm); lines.append(f"- `{name}=={dist.version}`")
    for m in ("pyproject.toml", "package.json", "Cargo.toml", "go.mod"):
        if (project_dir / m).is_file():
            lines.append(f"- Manifest détecté : `{m}`")
    return "\n".join(lines[:30]) + "\n", pkgs
