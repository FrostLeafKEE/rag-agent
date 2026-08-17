"""精排（Reranker）：对融合后的候选块二次排序（PRD FR-16）。

实现选择：
  - RERANK_API_KEY 配置 → SiliconFlowReranker（BAAI/bge-reranker-v2-m3）
  - 未配置 → NoopReranker（保持融合顺序），保证链路在无 key 时也可用
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx

from app.config import get_settings
from app.retrieval.base import RetrievedChunk

logger = logging.getLogger(__name__)


class RerankerError(Exception):
    """重排调用失败。"""


class Reranker(ABC):
    @abstractmethod
    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        """按与 query 的相关性降序返回（原地更新 score 并重排序）。"""


class SiliconFlowReranker(Reranker):
    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model

    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        if not chunks:
            return chunks
        resp = httpx.post(
            f"{self._base_url}/rerank",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "query": query,
                "documents": [c.content for c in chunks],
                "top_n": top_k or len(chunks),
            },
            timeout=60.0,
        )
        if resp.status_code != 200:
            raise RerankerError(f"rerank 接口返回 {resp.status_code}: {resp.text[:300]}")
        results = sorted(resp.json()["results"], key=lambda r: r["relevance_score"], reverse=True)
        reranked = [chunks[r["index"]] for r in results]
        for chunk, r in zip(reranked, results, strict=True):
            chunk.score = round(float(r["relevance_score"]), 6)
        return reranked


class NoopReranker(Reranker):
    """未配置重排服务时直接返回融合结果。"""

    def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        return chunks[:top_k] if top_k else chunks


def get_reranker() -> Reranker:
    settings = get_settings()
    if settings.rerank_api_key:
        return SiliconFlowReranker(
            settings.rerank_base_url, settings.rerank_api_key, settings.rerank_model
        )
    logger.info("未配置 RERANK_API_KEY，跳过精排（NoopReranker）")
    return NoopReranker()
