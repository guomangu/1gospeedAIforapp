"""Stateless OpenAI-compatible HTTP client with GBNF, thinking strip & speed guard."""

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from structuredai.config import Config
from structuredai.gbnf_schemas import GBNF_GRAMMARS, SCHEMAS, schema_for
from structuredai.sys_probes import urlopen_safe

_THINK_RE = re.compile(r"<think>.*?(?:</think>|$)", re.DOTALL | re.IGNORECASE)
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


@dataclass(frozen=True)
class LLMResponse:
    data: dict
    raw_text: str
    prompt_tokens: int
    completion_tokens: int
    duration_sec: float
    tokens_per_sec: float


def strip_and_parse_json(raw: str) -> dict:
    """Remove stray <think> tags or markdown fences and parse strict JSON."""
    cleaned = _THINK_RE.sub("", raw).strip()
    if not cleaned.startswith("{"):
        if m := _JSON_FENCE_RE.search(cleaned):
            cleaned = m.group(1)
        elif "{" in cleaned and "}" in cleaned:
            cleaned = cleaned[cleaned.find("{") : cleaned.rfind("}") + 1]
    obj = json.loads(cleaned)
    if not isinstance(obj, dict):
        raise TypeError("LLM response JSON root must be an object")
    return obj


class LLMClient:
    """Stateless client targeting llama-server, Ollama, or OpenAI-compatible APIs."""

    def __init__(self, cfg: Config) -> None:
        self.cfg, self.consecutive_anomalies, self.avg_tps, self.code_exts, self.arch_min_files = cfg, 0, 0.0, (), 1

    def _build_payload(self, role: str, sys_p: str, usr_p: str, temp: float | None) -> dict:
        model = self.cfg.llm_critique_model if (role == "3_CRITIQUE" and self.cfg.llm_critique_model) else self.cfg.llm_model
        payload: dict = {"model": model, "stream": False, "max_tokens": self.cfg.llm_max_output_tokens, "messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": usr_p}], "temperature": self.cfg.llm_temperature if temp is None else temp}
        if self.cfg.llm_provider in {"llama_cpp", "ollama"}:
            payload["repeat_penalty"] = 1.15
        if self.cfg.llm_provider == "llama_cpp":
            payload["presence_penalty"] = 0.2
        if self.cfg.llm_disable_thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        mode = self.cfg.llm_structured_mode
        if mode == "gbnf" and self.cfg.llm_provider == "llama_cpp" and role in GBNF_GRAMMARS:
            payload["grammar"] = GBNF_GRAMMARS[role]
        elif mode in {"gbnf", "json_schema"} and role in SCHEMAS:
            payload["response_format"] = {"type": "json_schema", "json_schema": {"name": role, "strict": True, "schema": schema_for(role, self.code_exts, self.arch_min_files)}}
        elif mode == "json_object":
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _post(self, url: str, body: bytes, headers: dict[str, str]) -> dict:
        for attempt in range(3):
            try:
                with urlopen_safe(urllib.request.Request(url, data=body, headers=headers, method="POST"), timeout=self.cfg.llm_timeout_sec) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 503 and attempt < 2:
                    time.sleep(1.5); continue
                self.consecutive_anomalies += 1; raise RuntimeError(f"LLM HTTP {exc.code}: {exc.read().decode('utf-8', errors='ignore')[:240]}") from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt < 2:
                    time.sleep(1.0); continue
                self.consecutive_anomalies += 1; raise RuntimeError(f"LLM HTTP error: {exc}") from exc
        raise RuntimeError("LLM HTTP unreachable")

    def complete(self, role: str, sys_p: str, usr_p: str, temp: float | None = None) -> LLMResponse:
        """Execute a single stateless completion call and validate structured JSON."""
        url = self.cfg.llm_base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {self.cfg.llm_api_key}"} if self.cfg.llm_api_key else {})}
        t0 = time.monotonic()
        raw_body = self._post(url, json.dumps(self._build_payload(role, sys_p, usr_p, temp)).encode("utf-8"), headers)
        dt = max(0.01, time.monotonic() - t0)
        choice = raw_body["choices"][0]
        content, usage = choice["message"]["content"] or "", raw_body.get("usage", {})
        ptok, ctok = int(usage.get("prompt_tokens", len(sys_p + usr_p) // 3)), int(usage.get("completion_tokens", max(1, len(content) // 3)))
        tps = float(raw_body.get("timings", {}).get("predicted_per_second") or (ctok / dt))
        self.avg_tps = tps if self.avg_tps == 0.0 else (0.7 * self.avg_tps + 0.3 * tps)
        low_tps = self.cfg.llm_provider == "llama_cpp" and ctok >= 20 and tps < self.cfg.min_tokens_per_sec
        self.consecutive_anomalies = (self.consecutive_anomalies + 1) if (not content.strip() or low_tps) else 0
        try:
            return LLMResponse(strip_and_parse_json(content), content, ptok, ctok, dt, tps)
        except json.JSONDecodeError as exc:
            if choice.get("finish_reason") == "length":
                raise ValueError(f"LLM output truncated at max_tokens={self.cfg.llm_max_output_tokens}") from exc
            raise
