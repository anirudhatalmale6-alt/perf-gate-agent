"""Swappable local-LLM client.

Default backend is Ollama over its local HTTP API (http://localhost:11434), so
inference stays on the machine. An OpenAI-compatible backend (vLLM, or Azure
OpenAI on a private endpoint) can be selected purely via config/env - the rest
of the agent does not change. If no backend is reachable the client reports
unavailable and the agent falls back to Stage-1-only output.
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from typing import Optional


class LLMUnavailable(Exception):
    pass


class LLMClient:
    def __init__(self, backend="ollama", model="qwen2.5-coder:7b",
                 base_url=None, api_key=None, timeout=60):
        self.backend = backend
        self.model = model
        self.timeout = timeout
        self.api_key = api_key or os.environ.get("PERF_GATE_API_KEY", "")
        if backend == "ollama":
            self.base_url = base_url or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        else:  # openai-compatible (vLLM / Azure OpenAI private endpoint / etc.)
            self.base_url = base_url or os.environ.get("PERF_GATE_BASE_URL", "http://localhost:8000/v1")

    def _post(self, url: str, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ConnectionError) as exc:
            raise LLMUnavailable(str(exc)) from exc

    def available(self) -> bool:
        try:
            if self.backend == "ollama":
                req = urllib.request.Request(self.base_url + "/api/tags")
                with urllib.request.urlopen(req, timeout=5):
                    return True
            else:
                # A cheap probe: many OpenAI-compatible servers expose /models.
                req = urllib.request.Request(self.base_url.rstrip("/") + "/models")
                if self.api_key:
                    req.add_header("Authorization", f"Bearer {self.api_key}")
                with urllib.request.urlopen(req, timeout=5):
                    return True
        except Exception:
            return False

    def complete(self, system: str, user: str, temperature: float = 0.1) -> str:
        if self.backend == "ollama":
            payload = {
                "model": self.model,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "stream": False,
                "options": {"temperature": temperature},
                "format": "json",
            }
            out = self._post(self.base_url.rstrip("/") + "/api/chat", payload)
            return out.get("message", {}).get("content", "")
        else:
            payload = {
                "model": self.model,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "temperature": temperature,
                "response_format": {"type": "json_object"},
            }
            out = self._post(self.base_url.rstrip("/") + "/chat/completions", payload)
            return out.get("choices", [{}])[0].get("message", {}).get("content", "")
