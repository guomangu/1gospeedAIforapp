"""Host watchdog managing llama-server lifecycle, RAM ceiling, disk & thermal pauses."""

import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from structuredai.config import Config
from structuredai.monorepo_router import IGNORED_DIRS
from structuredai.sys_probes import free_disk_mb, read_cpu_temp_c, read_rss_mb


class Watchdog:
    """Supervises local llama-server RSS, thermal limits, and disk free space."""

    def __init__(self, cfg: Config) -> None:
        self.cfg, self.proc, self.restarts = cfg, None, 0
        parsed = urlparse(cfg.llm_base_url)
        self.host, self.port = parsed.hostname or "127.0.0.1", parsed.port or 18080

    def _health_ok(self) -> bool:
        try:
            with urllib.request.urlopen(f"http://{self.host}:{self.port}/health", timeout=1) as r:
                return r.status == 200
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    def ensure_llm_ready(self) -> None:
        if not self.cfg.llm_auto_start or self.cfg.llm_provider != "llama_cpp":
            return
        if self.proc and self.proc.poll() is None and self._health_ok():
            return
        self.start_llama()

    def start_llama(self) -> None:
        self.stop_llama()
        log_p = self.cfg.target_dir / self.cfg.state_dir_name / "llama-server.log"
        log_p.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.cfg.llama_server_bin, "-m", self.cfg.llm_model_path,
            "--host", self.host, "--port", str(self.port),
            "-c", str(self.cfg.llm_ctx_size), "-np", "1", "-t", str(self.cfg.cpu_threads),
            "-ctk", self.cfg.llama_kv_cache_type, "-ctv", self.cfg.llama_kv_cache_type, "--no-warmup",
            *(["--alias", self.cfg.llm_model] if self.cfg.llm_model else []),
            *(["--reasoning-budget", "0"] if self.cfg.llm_disable_thinking else []),
        ]
        self._log_fp = log_p.open("w", encoding="utf-8")
        self.proc = subprocess.Popen(cmd, stdout=self._log_fp, stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(120):
            if self._health_ok():
                return
            if self.proc.poll() is not None:
                break
            time.sleep(0.5)
        tail = log_p.read_text(encoding="utf-8", errors="ignore")[-400:] if log_p.exists() else ""
        raise RuntimeError(f"llama-server failed on {self.host}:{self.port}: {tail}")

    def stop_llama(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        if hasattr(self, "_log_fp") and self._log_fp:
            self._log_fp.close()
            self._log_fp = None

    def check_health(self, force_restart: bool = False) -> tuple[float, float | None, bool]:
        """Monitor RAM, thermal zone, and disk headroom; returns (rss_mb, temp_c, low_disk)."""
        rss = read_rss_mb(os.getpid()) + (read_rss_mb(self.proc.pid) if self.proc else 0.0)
        if force_restart or (self.proc and rss > self.cfg.llama_rss_restart_mb):
            self.restarts += 1
            self.start_llama()
        temp = read_cpu_temp_c()
        if self.cfg.thermal_pause_c > 0 and temp and temp >= self.cfg.thermal_pause_c:
            time.sleep(15)
        free_mb = free_disk_mb(self.cfg.target_dir)
        if free_mb < self.cfg.disk_min_free_mb:
            self._cleanup_caches()
            free_mb = free_disk_mb(self.cfg.target_dir)
        return rss, temp, free_mb < 512.0

    def _cleanup_caches(self) -> None:
        caches = {".pytest_cache", "__pycache__", ".ruff_cache"}
        skip = (IGNORED_DIRS - caches) | {self.cfg.state_dir_name}
        for root, dirs, _files in os.walk(self.cfg.target_dir, onerror=lambda _e: None):
            for d in list(dirs):
                if d in caches:
                    shutil.rmtree(Path(root) / d, ignore_errors=True)
                    dirs.remove(d)
                elif d in skip:
                    dirs.remove(d)
