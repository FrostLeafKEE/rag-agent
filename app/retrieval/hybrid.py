"""混合检索融合：稠密 + 稀疏 → 倒数排名融合（RRF，ADR-04）。

RRF 将两条召回列表按排名融合，避免稠密/稀疏得分不可比的问题：
score(chunk) = Σ_路 1 / (k + rank_路(chunk))，k 取 60。
"""

from __future__ import annotations

from app.retrieval.base import RetrievedChunk

RRF_K = 60


def rrf_fuse(
    dense_hits: list[dict],
    sparse_hits: list[dict],
    top_k: int = 10,
) -> list[RetrievedChunk]:
    """融合两条 Milvus 命中列表，按 RRF 分降序返回 top_k。"""
    scores: dict[int, float] = {}
    chunks: dict[int, RetrievedChunk] = {}

    for rank, hit in enumerate(dense_hits, start=1):
        chunk = RetrievedChunk.from_milvus_hit(hit)
        chunks[chunk.chunk_id] = chunk
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (RRF_K + rank)

    for rank, hit in enumerate(sparse_hits, start=1):
        chunk = RetrievedChunk.from_milvus_hit(hit)
        if chunk.chunk_id in chunks:
            chunks[chunk.chunk_id].department = chunk.department
        else:
            chunks[chunk.chunk_id] = chunk
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (RRF_K + rank)

    fused = sorted(chunks.values(), key=lambda c: scores[c.chunk_id], reverse=True)
    for chunk in fused:
        chunk.score = round(scores[chunk.chunk_id], 6)
    return fused[:top_k]
