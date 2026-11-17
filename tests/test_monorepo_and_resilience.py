"""Regression tests for monorepo routing, brownfield safety, prompts, git & GGUF."""

import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from structuredai.brownfield import index_brownfield
from structuredai.config_loader import build_config
from structuredai.git_tx import GitTx
from structuredai.graph_ops import next_actionable_node
from structuredai.pass_critique import run_architect
from structuredai.prompts import build_prompt
from structuredai.security_gate import PathJail
from structuredai.stack_adapter import StackAdapter, build_deps_reality
from structuredai.stack_detector import BUILTIN_STACKS, detect_stack
from structuredai.steering import consume_directives, read_steering
from structuredai.sys_probes import read_gguf_arch


class TestMonorepoAndResilience(unittest.TestCase):
    def test_monorepo_detection_ignores_nested_node_modules_py(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "politique AI"
            (proj / "backend").mkdir(parents=True)
            (proj / "backend" / "tsconfig.json").write_text("{}", encoding="utf-8")
            (proj / "backend" / "package.json").write_text('{"dependencies":{"hono":"^4.0.0"}}', encoding="utf-8")
            nm = proj / "frontend" / "node_modules" / "pkg"
            nm.mkdir(parents=True)
            (nm / "helper.py").write_text("x = 1\n", encoding="utf-8")
            env_p = Path(td) / ".env"
            env_p.write_text(f"TARGET_PROJECT_DIR={proj}\nTARGET_STACK=auto\n", encoding="utf-8")
            cfg = build_config(env_p)
            stack = detect_stack(proj, "REQ-01: API", cfg)
            self.assertEqual(stack["name"], "node_ts")
            deps_md, pkgs = build_deps_reality(proj, "node_ts")
            self.assertIn("hono", pkgs)
            self.assertIn("backend/package.json", deps_md)
            exports: dict[str, set[str]] = {}
            (proj / "backend" / "src").mkdir()
            (proj / "backend" / "src" / "app.ts").write_text("export const ok = true;\n", encoding="utf-8")
            index_brownfield(cfg, proj / ".orchestrator" / "tree", [".ts", ".tsx"], exports)
            self.assertIn("backend/src/app.ts", exports)
            self.assertNotIn("frontend/node_modules/pkg/helper.py", exports)

    def test_prompt_preserves_target_and_fix_hint_and_stack(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            env_p = Path(td) / ".env"
            env_p.write_text("LLM_CTX_SIZE=2048\nLLM_MAX_OUTPUT_TOKENS=512\nTARGET_STACK=auto\n", encoding="utf-8")
            cfg = build_config(env_p)
            huge = "sig()\n" * 3000
            _sys_p, usr_p = build_prompt(
                "3_CRITIQUE", cfg,
                [("SPEC", "REQ-01"), ("REALITIES", huge), ("TARGET", "backend/src/x.ts")],
                stack_name="node_ts",
            )
            self.assertIn("backend/src/x.ts", usr_p)
            sys_d, usr_d = build_prompt(
                "1_DRAFT", cfg,
                [("CONTRACT", "c"), ("NEIGHBOR_REALITY", huge), ("FIX_HINT", "SyntaxError line 4")],
                stack_name="node_ts",
            )
            self.assertIn("(node_ts)", sys_d)
            self.assertIn("SyntaxError line 4", usr_d)

    def test_space_path_shlex_quote_and_git_brownfield_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "politique AI"
            (proj / "src").mkdir(parents=True)
            f = proj / "src" / "keep.py"
            f.write_text("def ok():\n    return 1\n", encoding="utf-8")
            git = GitTx(proj)
            git.ensure_ready(".orchestrator")
            f.write_text("BROKEN", encoding="utf-8")
            git.rollback_paths(["src/keep.py"])
            self.assertIn("return 1", f.read_text(encoding="utf-8"))
            cfg = build_config(Path(td) / ".env")
            ad = StackAdapter(proj, BUILTIN_STACKS["python"], cfg)
            ok, out = ad.build_check()
            self.assertTrue(ok, out)

    def test_transitive_block_and_unblock_and_steering_consume(self) -> None:
        nodes = {
            "a.py": {"state": "FAILED_MANUAL_REVIEW", "depends_on": []},
            "b.py": {"state": "1_DRAFT", "depends_on": ["a.py"]},
            "c.py": {"state": "1_DRAFT", "depends_on": ["b.py"]},
        }
        self.assertIsNone(next_actionable_node(nodes))
        self.assertEqual(nodes["b.py"]["state"], "BLOCKED_BY_UPSTREAM")
        nodes["a.py"]["state"] = "6_LOCKED"
        self.assertEqual(next_actionable_node(nodes), "b.py")
        with tempfile.TemporaryDirectory() as td:
            sp = Path(td) / "STEERING.md"
            sp.write_text("STATUS: RUN\npriority:  a.py\nnote [a.py]: check edge\n", encoding="utf-8")
            d = read_steering(sp)
            self.assertEqual(d.priorities, ["a.py"])
            consume_directives(sp, ["PRIORITY: a.py", "NOTE [a.py]: check edge"])
            txt = sp.read_text(encoding="utf-8")
            self.assertIn("# [APPLIED] priority:  a.py", txt)
            self.assertIn("# [APPLIED] note [a.py]: check edge", txt)

    def test_read_gguf_arch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "m.gguf"
            key, val = b"general.architecture", b"qwen35"
            buf = b"GGUF" + struct.pack("<IQQ", 3, 0, 1)
            buf += struct.pack("<Q", len(key)) + key + struct.pack("<IQ", 8, len(val)) + val
            p.write_bytes(buf)
            self.assertEqual(read_gguf_arch(p), "qwen35")

    def test_architect_rejects_missing_ext_and_dir_collision(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            cfg = build_config(proj / ".env")
            llm = MagicMock()
            llm.complete.return_value.data = {"files": [
                {"path": "backend/src/types", "purpose": "p", "depends_on": [], "req_ids": ["REQ-01"], "is_entry": False},
                {"path": "backend/src/types/a.ts", "purpose": "p", "depends_on": [], "req_ids": ["REQ-01"], "is_entry": False},
            ]}
            ctx = {"cfg": cfg, "llm": llm, "jail": PathJail(proj), "stack": {"name": "node_ts", "code_exts": [".ts", ".tsx"]}, "nodes": {}, "exports": {}, "meta_dir": proj / ".orchestrator" / "tree"}
            with self.assertRaises(ValueError):
                run_architect(ctx, "REQ-01")
