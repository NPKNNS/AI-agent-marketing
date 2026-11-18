"""Ask an OpenAI-compatible LLM (vLLM gateway, Ollama, OpenAI) for a JSON answer.

ask_json() never raises: on timeout, a bad answer or any error it returns None and the
caller falls back to rules. Local/gateway LLMs fail often — the agent must survive that.

Enforces Competition Agent Limits:
- Strictly at most 1 request in flight at a time (module-level lock).
- Strictly < 20 requests per minute (minimum 3.2s spacing between requests).
"""
import json
import re
import threading
import time

import httpx
from openai import OpenAI

# Ways to switch Qwen's "thinking" on/off; servers disagree, so try each and remember what works.
THINKING_STYLES = [
    lambda on: {"reasoning_effort": "medium" if on else "none"},                    # event-llm gateway, Ollama, OpenAI
    lambda on: {"extra_body": {"chat_template_kwargs": {"enable_thinking": on}}},    # vLLM
]

_LLM_LOCK = threading.Lock()
_LAST_CALL_TS = 0.0
_MIN_INTERVAL_S = 3.2  # max ~18.75 req/min (strictly under the 20 req/min competition limit)


class LLM:
    def __init__(self, base_url: str, model: str, api_key: str, timeout: float = 60, thinking: bool = False,
                 max_tokens: int = 1200, temperature: float = 0.25):
        http_timeout = httpx.Timeout(timeout, connect=4.0)
        self.client = OpenAI(base_url=base_url, api_key=api_key or "none", timeout=http_timeout, max_retries=0)
        self.model, self.thinking, self.timeout = model, thinking, timeout
        self.max_tokens, self.temperature = max_tokens, temperature
        self.style = 0
        self.last_error = None
        self.seconds = 0.0

    def ask_json(self, system: str, user: str, schema: dict, timeout: float | None = None) -> dict | None:
        global _LAST_CALL_TS
        self.last_error = None
        eff_timeout = min(timeout, self.timeout) if timeout else self.timeout
        client = self.client.with_options(timeout=httpx.Timeout(eff_timeout, connect=min(4.0, eff_timeout)))
        t0 = time.time()

        with _LLM_LOCK:
            for style in range(self.style, len(THINKING_STYLES)):
                wait_s = _MIN_INTERVAL_S - (time.time() - _LAST_CALL_TS)
                if wait_s > 0:
                    time.sleep(wait_s)
                _LAST_CALL_TS = time.time()
                try:
                    r = client.chat.completions.create(
                        model=self.model, temperature=self.temperature, max_tokens=self.max_tokens,
                        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                        response_format={"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}},
                        **THINKING_STYLES[style](self.thinking))
                except Exception as e:                       # noqa: BLE001 — any failure means "use the fallback"
                    self.last_error = f"{type(e).__name__}: {e}"[:300]
                    if getattr(e, "status_code", None) == 400:   # server did not like this thinking switch
                        continue
                    self.seconds = round(time.time() - t0, 1)
                    return None
                self.style = style
                text = r.choices[0].message.content or ""
                answer = parse_json(text)
                if answer is None:
                    self.last_error = f"not JSON (finish={r.choices[0].finish_reason}): {text[:200]!r}"
                self.seconds = round(time.time() - t0, 1)
                return answer
        return None


def parse_json(text: str) -> dict | None:
    """Accept plain JSON, JSON in ``` fences, or JSON after a <think> block."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    for candidate in (text, *re.findall(r"\{.*\}", text, flags=re.S)):
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    return None
