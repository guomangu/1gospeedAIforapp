"""Regression tests for monorepo routing, brownfield safety, prompts, git & GGUF."""

import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from structuredai.brownfield import index_brownfield
from structuredai.config_loader import build_config
from structuredai.gbnf_schemas import schema_for
from structuredai.git_tx import GitTx
from structuredai.graph_ops import next_actionable_node
from structuredai.pass_critique import run_architect
from structuredai.passes import run_draft
from structuredai.prompts import build_prompt
from structuredai.security_gate import PathJail
from structuredai.stack_adapter import StackAdapter, build_deps_reality
from structuredai.stack_detector import BUILTIN_STACKS, detect_stack
from structuredai.state_store import init_architect_with_retries, load_or_init_state
from structuredai.steering import consume_directives, read_steering
from structuredai.sys_probes import read_gguf_arch, urlopen_safe


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

    def test_urlopen_safe_bypasses_proxy_for_loopback(self) -> None:
        import os
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from threading import Thread

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
            def log_message(self, *_):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        t = Thread(target=srv.serve_forever, daemon=True); t.start()
        old = {k: os.environ.get(k) for k in ("http_proxy", "no_proxy")}
        try:
            os.environ["http_proxy"] = "http://127.0.0.1:1"
            os.environ["no_proxy"] = "localhost,127.0.0.0/8,::1"
            with urlopen_safe(f"http://127.0.0.1:{srv.server_port}/health", timeout=2) as r:
                self.assertEqual(r.status, 200)
        finally:
            srv.shutdown(); srv.server_close()
            for k, v in old.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v

    def test_architect_schema_pattern_dedup_deps_and_retry_hint(self) -> None:
        import re
        sch = schema_for("0_ARCHITECT", (".ts", ".tsx"))
        pat = sch["properties"]["files"]["items"]["properties"]["path"]["pattern"]
        self.assertRegex("backend/src/domain/laws.ts", pat)
        self.assertRegex("frontend/src/components/web-badge.tsx", pat)
        self.assertNotRegex("backend/src/domain/types", pat)
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            cfg = build_config(proj / ".env")
            calls: list[tuple[str, float | None]] = []

            def fake_complete(_role: str, _sys_p: str, usr_p: str, temp=None):
                calls.append((usr_p, temp))
                m = MagicMock()
                if len(calls) == 1:
                    m.data = {"files": [{"path": "backend/src/domain/types", "purpose": "p", "depends_on": [], "req_ids": ["REQ-01"], "is_entry": False}]}
                else:
                    m.data = {"files": [
                        {"path": "backend/src/domain/types.ts", "purpose": "p1", "depends_on": ["shared-db/schema.ts"], "req_ids": ["REQ-01"], "is_entry": False},
                        {"path": "backend/src/domain/types.ts", "purpose": "p1", "depends_on": ["shared-db/schema.ts"], "req_ids": ["REQ-02"], "is_entry": False},
                    ]}
                return m

            llm = MagicMock()
            llm.complete.side_effect = fake_complete
            state = load_or_init_state(cfg, proj / ".orchestrator" / "state.json", "brownfield", "node_ts")
            ctx = {
                "cfg": cfg, "llm": llm, "jail": PathJail(proj), "stack": {"name": "node_ts", "code_exts": [".ts", ".tsx"]},
                "nodes": state["nodes"], "exports": {"backend/src/db/schema.ts": {"locations"}},
                "meta_dir": proj / ".orchestrator" / "tree", "spec_text": "# SPEC\n- REQ-01\n- REQ-02",
            }
            init_architect_with_retries(ctx, state, proj / ".orchestrator" / "state.json")
            self.assertEqual(len(calls), 2)
            self.assertIn("FIX_HINT", calls[1][0])
            self.assertIsNotNone(calls[1][1])
            self.assertEqual(state["nodes"]["backend/src/domain/types.ts"]["depends_on"], ["backend/src/db/schema.ts"])
            self.assertEqual(state["nodes"]["backend/src/domain/types.ts"]["req_ids"], ["REQ-01", "REQ-02"])
            self.assertTrue(re.search(r"REQs|\['REQ-01', 'REQ-02'\]", (ctx["meta_dir"] / "backend/src/domain/types.ts" / "CONTRACT.md").read_text()))

    def test_run_draft_reads_existing_file_without_error(self) -> None:
        from structuredai.error_distiller import distill_error
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            (proj / ".env").write_text(f"TARGET_PROJECT_DIR={proj}\n", encoding="utf-8")
            cfg = build_config(proj / ".env")
            target = proj / "mod.py"
            target.write_text("def broken(:\n    pass\n", encoding="utf-8")
            seen_usr: list[str] = []

            def fake_complete(_role: str, _sys_p: str, usr_p: str, temp=None):
                seen_usr.append(usr_p)
                m = MagicMock()
                m.data = {"filename": "mod.py", "code": "def ok() -> int:\n    return 1\n", "exports_summary": "ok"}
                return m

            llm = MagicMock()
            llm.complete.side_effect = fake_complete
            ctx = {
                "cfg": cfg, "llm": llm, "jail": PathJail(proj),
                "adapter": StackAdapter(proj, BUILTIN_STACKS["python"], cfg),
                "stack": BUILTIN_STACKS["python"], "meta_dir": proj / ".orchestrator" / "tree",
                "nodes": {}, "deps_md": "", "installed_pkgs": set(), "exports": {},
            }
            node = {"state": "1_DRAFT", "purpose": "return 1", "depends_on": [], "last_error": "SyntaxError line 1"}
            st = run_draft(ctx, "mod.py", node, None, "")
            self.assertEqual(st, "2_EXTRACT")
            self.assertIn("EXISTING_CODE", seen_usr[0])
            self.assertIn("def broken(:", seen_usr[0])
            self.assertIn("FIX_HINT", seen_usr[0])
            ts_ctx = distill_error("src/a.ts(3,10): error TS2835: use .js", "l1\nl2\nimport './x';\nl4\n")
            self.assertIn("3: import './x';", ts_ctx)

    def test_ts_import_preflight_self_and_wave_non_abort(self) -> None:
        from structuredai.brownfield import build_existing_brief
        from structuredai.hardening import handle_idle_or_next_wave
        from structuredai.time_budget import TimeBudgetManager
        from structuredai.validators import preflight_imports

        errs_self = preflight_imports("import { Loi } from './types';\n", ".ts", {"backend/src/db/schema.ts": {"locations"}}, {"hono"}, "backend/src/domain/types.ts")
        self.assertTrue(any("Self-import" in e for e in errs_self))
        errs_miss = preflight_imports("import { X } from './missing.js';\n", ".ts", {"backend/src/db/schema.ts": {"locations"}}, {"hono"}, "backend/src/domain/types.ts")
        self.assertTrue(any("Missing local module" in e for e in errs_miss))
        errs_ok = preflight_imports("import { locations } from '../db/schema.js';\n", ".ts", {"backend/src/db/schema.ts": {"locations"}}, {"hono"}, "backend/src/domain/types.ts")
        self.assertEqual(errs_ok, [])
        self.assertEqual(schema_for("0_ARCHITECT", (".ts", ".tsx"), min_items=3)["properties"]["files"]["minItems"], 3)

        with tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            (proj / ".env").write_text(f"TARGET_PROJECT_DIR={proj}\nMAX_IDLE_HARDENING_CYCLES=1\n", encoding="utf-8")
            cfg = build_config(proj / ".env")
            exports = {f"a/{i:02d}.ts": set() for i in range(30)}
            brief = build_existing_brief(cfg, {"z_failed.ts": {"state": "FAILED_MANUAL_REVIEW"}}, exports)
            self.assertIn("z_failed.ts [FAILED_MANUAL_REVIEW]", brief)
            state = load_or_init_state(cfg, proj / ".orchestrator" / "state.json", "brownfield", "node_ts")
            state["nodes"]["backend/src/domain/types.ts"] = {"state": "FAILED_MANUAL_REVIEW", "req_ids": ["REQ-01"]}
            llm = MagicMock(); llm.complete.side_effect = ValueError("0_ARCHITECT returned empty files list")
            ad = MagicMock(); ad.test_suite.return_value = (True, ""); ad.build_check.return_value = (True, "")
            ctx = {"cfg": cfg, "llm": llm, "jail": PathJail(proj), "adapter": ad, "git": MagicMock(), "stack": {"name": "node_ts", "code_exts": [".ts"]}, "meta_dir": proj / ".orchestrator" / "tree", "nodes": state["nodes"], "exports": {}, "spec_text": "# SPEC\n- REQ-01\n- REQ-02"}
            res = handle_idle_or_next_wave(ctx, state, TimeBudgetManager(3600), proj / ".orchestrator" / "state.json")
            self.assertEqual(state["time_budget"]["architect_waves"], 2)
            self.assertEqual(res, "H3_INCOMPLETE_SPEC")
