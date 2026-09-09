"""OpenAI-compatible LLM client with retry — shared by LLM-backed components.

Owns the "call a model" concern (endpoint / auth / timeout / retry / provenance)
so LLM-as-judge metrics and any future LLM-backed component (multi-aspect
judges, LLM dataset builders, ...) **compose** an ``LLMClient`` instead of
re-implementing HTTP + retry per metric.

Layering:
- ``LLMConfig``  — endpoint + key + model + timeout + retry knobs (``from_env``)
- ``ChatResult`` — assistant text + ``model`` provenance (which model answered)
- ``LLMClient``  — the call: OpenAI-compatible ``/chat/completions`` + retry,
  with an optional shared ``asyncio.Semaphore`` to cap in-flight concurrency
  (e.g. many judges × many samples hitting one endpoint → avoid 429s)

Robustness is split by layer: the client *raises* on persistent failure;
callers (e.g. ``BaseLLMJudge.score``) decide how to degrade (judge → None).
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class LLMConfig:
    """Endpoint + key + model for an OpenAI-compatible chat completion API.

    Resolve from env via ``from_env``; pass empty values explicitly for tests.
    The default env var names (``EVAL_JUDGE_BASE`` / ``_KEY`` / ``_MODEL``) keep
    LLM secrets out of yaml; pass other names for non-judge uses.
    """

    base_url: str
    api_key: str
    model: str
    timeout_s: float = 60.0
    # 瞬时失败重试：并发调模型时偶发超时 / 429 / 5xx 不应让调用方直接失败。
    # 总尝试 = max_retries + 1。
    max_retries: int = 2
    retry_backoff_s: float = 1.0  # 指数退避基数：第 i 次重试前 sleep backoff * 2**i

    @classmethod
    def from_env(
        cls,
        *,
        base_env: str = "EVAL_JUDGE_BASE",
        key_env: str = "EVAL_JUDGE_KEY",
        model_env: str = "EVAL_JUDGE_MODEL",
        timeout_s: float = 60.0,
        max_retries: int = 2,
        retry_backoff_s: float = 1.0,
    ) -> LLMConfig:
        return cls(
            base_url=os.environ.get(base_env, "").rstrip("/"),
            api_key=os.environ.get(key_env, ""),
            model=os.environ.get(model_env, ""),
            timeout_s=timeout_s,
            max_retries=max_retries,
            retry_backoff_s=retry_backoff_s,
        )

    def ready(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


@dataclass(frozen=True)
class ChatResult:
    """One completion: the assistant text + which model produced it (provenance)."""

    text: str
    model: str


class LLMClient:
    """Async OpenAI-compatible chat client with retry on transient failures.

    Construct one and share it across metrics to reuse config and (optionally) a
    concurrency cap. Retries timeouts / network errors / 429 / 5xx up to
    ``config.max_retries`` times with exponential backoff; other 4xx are not
    retried (they won't fix themselves). On exhaustion the last error raises.
    """

    def __init__(
        self, config: LLMConfig, *, semaphore: asyncio.Semaphore | None = None
    ):
        self.config = config
        # 可选全局并发闸：多个 judge × 多 sample 并发判分时，限制对单个端点的在途请求数。
        self._semaphore = semaphore

    @classmethod
    def from_env(
        cls, *, semaphore: asyncio.Semaphore | None = None, **env_kwargs
    ) -> LLMClient:
        return cls(LLMConfig.from_env(**env_kwargs), semaphore=semaphore)

    def ready(self) -> bool:
        return self.config.ready()

    async def complete(self, system: str, user: str) -> ChatResult:
        """Convenience: a single system+user turn."""
        return await self.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )

    async def chat(self, messages: list[dict]) -> ChatResult:
        """Call ``/chat/completions`` with retry. Raises on persistent failure."""
        attempts = max(1, self.config.max_retries + 1)
        async with httpx.AsyncClient(timeout=self.config.timeout_s) as client:
            for i in range(attempts):
                try:
                    return await self._post(client, messages)
                except Exception as exc:
                    if i + 1 < attempts and _is_retryable(exc):
                        await asyncio.sleep(self.config.retry_backoff_s * (2**i))
                        continue
                    raise
        raise RuntimeError("unreachable")  # pragma: no cover

    async def _post(
        self, client: httpx.AsyncClient, messages: list[dict]
    ) -> ChatResult:
        """A single attempt. The optional semaphore caps concurrent in-flight
        calls (released during backoff so retries don't hog slots)."""
        if self._semaphore is not None:
            async with self._semaphore:
                return await self._post_once(client, messages)
        return await self._post_once(client, messages)

    async def _post_once(
        self, client: httpx.AsyncClient, messages: list[dict]
    ) -> ChatResult:
        resp = await client.post(
            f"{self.config.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.config.model,
                "messages": messages,
                "temperature": 0,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        return ChatResult(text=text, model=data.get("model") or self.config.model)


def _is_retryable(exc: Exception) -> bool:
    """Transient errors worth retrying: timeouts, network/transport errors,
    429, and 5xx. Other 4xx (bad request / auth) won't fix on retry."""
    if isinstance(exc, httpx.TimeoutException | httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False
