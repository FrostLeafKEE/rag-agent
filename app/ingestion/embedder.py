"""嵌入抽象：通过配置切换实现（ADR-02/03）。

实现选择（app/config.py 的 embedding_* 配置）：
  - EMBEDDING_BASE_URL 有值 → OpenAICompatibleEmbedder（走 OpenAI 兼容 /embeddings 接口，
    如 SiliconFlow 的 BGE-M3、阿里云 DashScope 等）
  - 否则 → FakeEmbedder（确定性占位向量，仅用于管道联调，不可用于真实检索）

本地部署 BGE-M3（sentence-transformers，~2.3GB 模型）按需添加：
  安装 `uv add sentence-transformers` 后实现 LocalBGE3Embedder 并在 get_embedder 中按配置启用。
"""

from __future__ import annotations

import hashlib
import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_DIM = 1024  # BGE-M3 稠密向量维度


class EmbedderError(Exception):
    """嵌入调用失败。"""


class Embedder:
    dim: int = DEFAULT_DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


class OpenAICompatibleEmbedder(Embedder):
    """OpenAI 兼容 /embeddings 接口（SiliconFlow / DashScope 等）。"""

    BATCH_SIZE = 32  # 分批提交，避免单请求超输入上限

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._client = httpx.Client(timeout=60.0)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[start : start + self.BATCH_SIZE]
            resp = self._client.post(
                f"{self._base_url}/embeddings",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self._model, "input": batch},
            )
            if resp.status_code != 200:
                raise EmbedderError(f"嵌入接口返回 {resp.status_code}: {resp.text[:300]}")
            data = resp.json()["data"]
            batch_vectors = [item["embedding"] for item in sorted(data, key=lambda i: i["index"])]
            vectors.extend(batch_vectors)
        if vectors:
            self.dim = len(vectors[0])
        return vectors


class FakeEmbedder(Embedder):
    """确定性伪向量：相同文本产生相同向量，用于管道联调与测试（不可用于真实检索）。"""

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(self.dim)]


def get_embedder() -> Embedder:
    """根据配置返回嵌入实现。"""
    settings = get_settings()
    if settings.embedding_base_url and settings.embedding_api_key:
        return OpenAICompatibleEmbedder(
            settings.embedding_base_url, settings.embedding_api_key, settings.embedding_model
        )
    logger.warning(
        "未配置 EMBEDDING_BASE_URL/EMBEDDING_API_KEY，使用 FakeEmbedder（仅联调，不可用于真实检索）"
    )
    return FakeEmbedder()
