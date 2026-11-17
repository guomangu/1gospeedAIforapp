"""Low-level Linux system probes, GGUF arch check & single-instance file lock."""

import fcntl
import os
import shutil
import socket
import struct
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


def urlopen_safe(url_or_req, timeout: float):
    """Open URL bypassing HTTP proxies for loopback hosts (127.0.0.1/localhost/::1)."""
    url = url_or_req if isinstance(url_or_req, str) else url_or_req.full_url
    host = (urlparse(url).hostname or "").lower()
    if host in {"localhost", "::1"} or host.startswith("127."):
        return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url_or_req, timeout=timeout)
    return urllib.request.urlopen(url_or_req, timeout=timeout)

class InstanceLock:
    """Exclusive non-blocking lock preventing concurrent runs on the same project."""

    def __init__(self, lock_path: Path) -> None:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = lock_path.open("w", encoding="utf-8")
        try:
            fcntl.flock(self._fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fp.write(f"{os.getpid()}\n")
            self._fp.flush()
        except BlockingIOError as exc:
            self._fp.close()
            raise RuntimeError(f"Another StructuredAI instance owns {lock_path}") from exc

    def release(self) -> None:
        fcntl.flock(self._fp.fileno(), fcntl.LOCK_UN)
        self._fp.close()


def read_rss_mb(pid: int) -> float:
    """Read resident set size (VmRSS) from /proc/<pid>/status."""
    try:
        for ln in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if ln.startswith("VmRSS:"):
                return int(ln.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


def read_cpu_temp_c() -> float | None:
    """Read max thermal zone temperature in Celsius if available."""
    temps: list[float] = []
    for p in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
        try:
            temps.append(int(p.read_text().strip()) / 1000.0)
        except (OSError, ValueError):
            continue
    return max(temps) if temps else None


def free_disk_mb(path: Path) -> float:
    return shutil.disk_usage(path).free / (1024.0 * 1024.0)


def is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def read_gguf_arch(model_path: Path) -> str:
    """Read general.architecture from GGUF v2/v3 header without loading weights."""
    try:
        with model_path.open("rb") as fp:
            if fp.read(4) != b"GGUF":
                return ""
            _ver, _tensors, kv_count = struct.unpack("<IQQ", fp.read(20))
            for _ in range(min(kv_count, 32)):
                klen = struct.unpack("<Q", fp.read(8))[0]
                key = fp.read(min(klen, 256)).decode("utf-8", errors="ignore")
                vtype = struct.unpack("<I", fp.read(4))[0]
                if vtype == 8:
                    vlen = struct.unpack("<Q", fp.read(8))[0]
                    val = fp.read(min(vlen, 256)).decode("utf-8", errors="ignore")
                    if key == "general.architecture":
                        return val
                elif vtype in (0, 1, 7): fp.seek(1, 1)
                elif vtype in (2, 3): fp.seek(2, 1)
                elif vtype in (4, 5, 6): fp.seek(4, 1)
                elif vtype in (10, 11, 12): fp.seek(8, 1)
                else: return ""
    except (OSError, struct.error):
        pass
    return ""
