"""模型网关：OpenAI 兼容 chat/completions 的统一入口（FR-44）。

LLM 通过 .env 配置（LLM_BASE_URL/LLM_API_KEY/LLM_MODEL）切换云端或本地服务；
所有 Agent 编排与生成逻辑只依赖本模块，不直接接触 HTTP 细节。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """LLM 调用失败。"""


class LLMGateway:
    """OpenAI 兼容网关：支持同步与流式两种调用。"""

    def __init__(self, base_url: str, api_key: str, model: str, temperature: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._temperature = temperature

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, messages: list[dict], *, stream: bool = False, **kwargs: Any) -> dict:
        return {
            "model": self._model,
            "messages": messages,
            "temperature": kwargs.pop("temperature", self._temperature),
            "stream": stream,
            **kwargs,
        }

    def complete(self, messages: list[dict], **kwargs: Any) -> str:
        """非流式补全，返回完整回复文本。"""
        from app.observability.metrics import QA_LLM_CALLS

        QA_LLM_CALLS.inc()
        resp = httpx.post(
            f"{self._base_url}/chat/completions",
            headers=self._headers(),
            json=self._payload(messages, **kwargs),
            timeout=120.0,
        )
        if resp.status_code != 200:
            raise LLMError(f"LLM 接口返回 {resp.status_code}: {resp.text[:300]}")
        return resp.json()["choices"][0]["message"]["content"]

    async def stream(self, messages: list[dict], **kwargs: Any) -> AsyncIterator[str]:
        """流式补全（SSE），逐段产出文本。"""
        from app.observability.metrics import QA_LLM_CALLS

        QA_LLM_CALLS.inc()
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=30.0, read=120.0, write=60.0, pool=30.0)
        ) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                headers=self._headers(),
                json=self._payload(messages, stream=True, **kwargs),
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    raise LLMError(f"LLM 接口返回 {resp.status_code}: {body[:300]}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        import json

                        delta = json.loads(data)["choices"][0]["delta"].get("content")
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if delta:
                        yield delta


def get_llm() -> LLMGateway:
    settings = get_settings()
    return LLMGateway(
        settings.llm_base_url,
        settings.llm_api_key,
        settings.llm_model,
        settings.llm_temperature,
    )
