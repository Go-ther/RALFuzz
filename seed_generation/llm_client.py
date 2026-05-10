from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

import requests


def _normalize_usage(data: Dict, endpoint: str) -> Dict:
    if not isinstance(data, dict):
        return {"available": False, "endpoint": endpoint}

    usage = data.get("usage")
    if isinstance(usage, dict):
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
        if total_tokens is None and (prompt_tokens is not None or completion_tokens is not None):
            total_tokens = int(prompt_tokens or 0) + int(completion_tokens or 0)
        return {
            "available": any(v is not None for v in (prompt_tokens, completion_tokens, total_tokens)),
            "endpoint": endpoint,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    if "prompt_eval_count" in data or "eval_count" in data:
        prompt_tokens = data.get("prompt_eval_count")
        completion_tokens = data.get("eval_count")
        total_tokens = None
        if prompt_tokens is not None or completion_tokens is not None:
            total_tokens = int(prompt_tokens or 0) + int(completion_tokens or 0)
        return {
            "available": True,
            "endpoint": endpoint,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    return {"available": False, "endpoint": endpoint}


def merge_usage(items: List[Dict]) -> Dict:
    available_items = [item for item in items if isinstance(item, dict) and item.get("available")]
    if not available_items:
        return {
            "available": False,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        }
    totals: Dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        vals = [item.get(key) for item in available_items if item.get(key) is not None]
        totals[key] = sum(int(v) for v in vals) if vals else 0
    return {
        "available": True,
        "prompt_tokens": totals.get("prompt_tokens", 0),
        "completion_tokens": totals.get("completion_tokens", 0),
        "total_tokens": totals.get("total_tokens", 0),
    }


class OpenAICompatClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        endpoint_mode: str = "auto",
        timeout_sec: int = 120,
        sequential_delay_ms: int = 0,
        network_retries: int = 2,
        network_retry_backoff_sec: float = 2.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.endpoint_mode = endpoint_mode
        self.timeout_sec = timeout_sec
        self.sequential_delay_ms = max(0, sequential_delay_ms)
        self.network_retries = max(0, int(network_retries))
        self.network_retry_backoff_sec = max(0.0, float(network_retry_backoff_sec))

    def _is_deepseek_api(self) -> bool:
        return "api.deepseek.com" in self.base_url.lower()

    def _base_root(self) -> str:
        if self.base_url.endswith("/v1"):
            return self.base_url[: -len("/v1")]
        return self.base_url

    def _resolve_url(self, path: str) -> str:
        normalized_path = path
        if self.base_url.endswith("/v1") and path.startswith("/v1/"):
            normalized_path = path[len("/v1") :]
        if self.base_url.endswith("/v1") and path.startswith("/api/"):
            return self._base_root() + path
        return self.base_url + normalized_path

    def _should_retry_status(self, status_code: int) -> bool:
        return status_code in {408, 409, 429, 500, 502, 503, 504}

    def _retry_delay_sec(self, attempt_index: int) -> float:
        if self.network_retry_backoff_sec <= 0:
            return 0.0
        return self.network_retry_backoff_sec * (2 ** max(0, attempt_index))

    def _post(self, path: str, payload: Dict) -> Dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = self._resolve_url(path)
        max_attempts = 1 + self.network_retries
        for attempt in range(max_attempts):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout_sec)
            except requests.RequestException:
                if attempt + 1 >= max_attempts:
                    raise
                delay_sec = self._retry_delay_sec(attempt)
                if delay_sec > 0:
                    time.sleep(delay_sec)
                continue

            try:
                resp.raise_for_status()
            except requests.HTTPError as exc:
                body = ""
                try:
                    body = resp.text
                except Exception:
                    pass
                if attempt + 1 < max_attempts and self._should_retry_status(resp.status_code):
                    delay_sec = self._retry_delay_sec(attempt)
                    if delay_sec > 0:
                        time.sleep(delay_sec)
                    continue
                raise RuntimeError(f"HTTP {resp.status_code}: {body}") from exc
            return resp.json()

        raise RuntimeError(f"request retries exhausted for {url}")

    def _chat_completion(
        self,
        prompt: str,
        n: Optional[int],
        max_tokens: int,
        temperature: float,
        top_p: float,
    ) -> Tuple[List[str], Dict]:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
        }
        if self._is_deepseek_api():
            payload["thinking"] = {"type": "disabled"}
        if n is not None:
            payload["n"] = n
        data = self._post("/v1/chat/completions", payload)
        outs: List[str] = []
        for ch in data.get("choices", []):
            msg = ch.get("message", {})
            content = msg.get("content", "")
            if isinstance(content, str):
                outs.append(content)
            elif isinstance(content, list):
                outs.append("".join(x.get("text", "") for x in content if isinstance(x, dict)))
        return outs, _normalize_usage(data, "chat")

    def _completion(
        self,
        prompt: str,
        n: Optional[int],
        max_tokens: int,
        temperature: float,
        top_p: float,
    ) -> Tuple[List[str], Dict]:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
        }
        if n is not None:
            payload["n"] = n
        data = self._post("/v1/completions", payload)
        outs = [ch.get("text", "") for ch in data.get("choices", []) if isinstance(ch.get("text", ""), str)]
        return outs, _normalize_usage(data, "completion")

    def _ollama_chat(
        self,
        prompt: str,
        max_tokens: int,
        temperature: float,
        top_p: float,
    ) -> Tuple[List[str], Dict]:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "num_predict": max_tokens,
            },
        }
        data = self._post("/api/chat", payload)
        msg = data.get("message", {}) if isinstance(data, dict) else {}
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        if isinstance(content, str):
            return [content], _normalize_usage(data, "ollama")
        if isinstance(content, list):
            return ["".join(x.get("text", "") for x in content if isinstance(x, dict))], _normalize_usage(data, "ollama")
        return [], _normalize_usage(data, "ollama")

    def generate(self, prompt: str, n: int, max_tokens: int, temperature: float, top_p: float) -> Tuple[List[str], str, Dict]:
        target_n = max(1, n)
        errs: List[str] = []

        def sequential_fetch(kind: str) -> Tuple[List[str], Dict]:
            out: List[str] = []
            usages: List[Dict] = []
            while len(out) < target_n:
                try:
                    if kind == "chat":
                        chunks, usage = self._chat_completion(
                            prompt,
                            n=None,
                            max_tokens=max_tokens,
                            temperature=temperature,
                            top_p=top_p,
                        )
                    else:
                        chunks, usage = self._completion(
                            prompt,
                            n=None,
                            max_tokens=max_tokens,
                            temperature=temperature,
                            top_p=top_p,
                        )
                except Exception as exc:
                    errs.append(f"{kind} single-shot request failed: {exc}")
                    break
                usages.append(usage)
                if chunks:
                    out.append(chunks[0])
                else:
                    errs.append(f"{kind} single-shot request returned empty choices")
                    break
                if self.sequential_delay_ms > 0 and len(out) < target_n:
                    time.sleep(self.sequential_delay_ms / 1000.0)
            return out, merge_usage(usages)

        def sequential_fetch_ollama() -> Tuple[List[str], Dict]:
            out: List[str] = []
            usages: List[Dict] = []
            while len(out) < target_n:
                try:
                    chunks, usage = self._ollama_chat(
                        prompt,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        top_p=top_p,
                    )
                except Exception as exc:
                    errs.append(f"ollama single-shot request failed: {exc}")
                    break
                usages.append(usage)
                if chunks and isinstance(chunks[0], str) and chunks[0].strip():
                    out.append(chunks[0])
                else:
                    errs.append("ollama single-shot request returned empty content")
                    break
                if self.sequential_delay_ms > 0 and len(out) < target_n:
                    time.sleep(self.sequential_delay_ms / 1000.0)
            return out, merge_usage(usages)

        if self.endpoint_mode == "ollama":
            outs, usage = sequential_fetch_ollama()
            if len(outs) == target_n:
                return outs, "ollama", usage
            raise RuntimeError("; ".join(errs) if errs else "ollama generation failed")

        if self.endpoint_mode in ("chat", "auto"):
            outs, usage = sequential_fetch("chat")
            if len(outs) == target_n:
                return outs, "chat", usage
            if self.endpoint_mode == "chat":
                raise RuntimeError("; ".join(errs) if errs else "chat generation failed")

        if self.endpoint_mode in ("completion", "auto"):
            outs, usage = sequential_fetch("completion")
            if len(outs) == target_n:
                return outs, "completion", usage
            if self.endpoint_mode == "completion":
                raise RuntimeError("; ".join(errs) if errs else "completion generation failed")

        raise RuntimeError("; ".join(errs) if errs else "no completions returned")
