"""Minimal Agent Client Protocol (ACP) stdio server exposing StructuredAI Local in Zed."""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from structuredai.config_loader import build_config
from structuredai.steering import append_steering_directive, set_steering_status
from structuredai.sys_probes import urlopen_safe


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _send_chunk(session_id: str, text: str) -> None:
    upd = {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}
    _send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id, "update": upd}})


def _query_local_llm(cfg, prompt: str, dash_txt: str) -> str:
    url = cfg.llm_base_url.rstrip("/") + "/chat/completions"
    sys_msg = (
        "Assistant de supervision StructuredAI. Réponds brièvement. N'écris pas de code complet ici : "
        "pour lancer le développement autonome sur le dépôt, indique `/start` ou `./start.sh`.\n" + dash_txt[:900]
    )
    payload = {"model": cfg.llm_model, "stream": False, "temperature": cfg.llm_temperature, "max_tokens": 512, "messages": [{"role": "system", "content": sys_msg}, {"role": "user", "content": prompt[:2000]}]}
    if cfg.llm_disable_thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen_safe(req, timeout=cfg.llm_timeout_sec) as r:
            return json.loads(r.read().decode("utf-8"))["choices"][0]["message"]["content"] or "(réponse vide)"
    except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
        return f"Serveur IA local ({url}) indisponible : {exc}\nLancez `/start` ou `./start.sh`."


def _launch_engine(sdir: Path) -> str:
    root = Path(__file__).resolve().parent.parent
    sdir.mkdir(parents=True, exist_ok=True)
    with (sdir / "orchestrator.log").open("a", encoding="utf-8") as fp:
        subprocess.Popen([str(root / "start.sh"), "start"], cwd=root, stdout=fp, stderr=subprocess.STDOUT, start_new_session=True)
    return f"Orchestrateur lancé en arrière-plan. Suivez `{sdir / 'DASHBOARD.md'}` ou `/status`."


def handle_prompt(cfg, text: str) -> str:
    """Execute steering slash commands or forward supervision query to local llama-server."""
    sdir = cfg.target_dir / cfg.state_dir_name
    dash_p, steer_p, cmd = sdir / "DASHBOARD.md", sdir / "STEERING.md", text.strip()
    if cmd == "/start":
        return _launch_engine(sdir)
    if cmd == "/status":
        return dash_p.read_text(encoding="utf-8") if dash_p.is_file() else f"Aucun DASHBOARD.md actif dans {sdir}. Lancez `/start` ou `./start.sh`."
    if cmd in {"/pause", "/resume", "/stop"}:
        st = {"/pause": "PAUSE", "/resume": "RUN", "/stop": "STOP"}[cmd]
        set_steering_status(steer_p, st)
        return f"STEERING.md mis à jour -> STATUS: {st}"
    if cmd.startswith(("/priority ", "/retry ")):
        verb, rel = cmd[1:].split(" ", 1)
        append_steering_directive(steer_p, verb.upper(), rel.strip())
        return f"STEERING.md mis à jour -> {verb.upper()}: {rel.strip()}"
    if cmd == "/help":
        return "Commandes : /start, /status, /pause, /resume, /stop, /priority <fichier>, /retry <fichier>, ou question de supervision."
    return _query_local_llm(cfg, cmd, dash_p.read_text(encoding="utf-8") if dash_p.is_file() else "")


def handle_rpc(msg: dict, cfg) -> None:
    """Process one ACP JSON-RPC 2.0 request."""
    req_id, method, params = msg.get("id"), msg.get("method", ""), msg.get("params", {})
    if method == "initialize":
        caps = {"loadSession": False, "promptCapabilities": {"image": False, "audio": False, "embeddedContext": True}}
        _send({"jsonrpc": "2.0", "id": req_id, "result": {"protocolVersion": 1, "agentCapabilities": caps, "agentInfo": {"name": "structuredai-local", "title": "StructuredAI Local", "version": "0.1.0"}, "authMethods": []}})
    elif method == "session/new":
        _send({"jsonrpc": "2.0", "id": req_id, "result": {"sessionId": "sai-local-1"}})
    elif method == "session/prompt":
        sid = params.get("sessionId", "sai-local-1")
        parts = [p.get("text", "") for p in params.get("prompt", []) if isinstance(p, dict) and p.get("type") == "text"]
        _send_chunk(sid, handle_prompt(cfg, "\n".join(parts)))
        _send({"jsonrpc": "2.0", "id": req_id, "result": {"stopReason": "end_turn"}})
    elif req_id is not None:
        _send({"jsonrpc": "2.0", "id": req_id, "result": {}})


def main() -> int:
    cfg = build_config(Path(os.environ.get("STRUCTURED_AI_ENV", ".env")))
    for line in sys.stdin:
        if s := line.strip():
            handle_rpc(json.loads(s), cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
