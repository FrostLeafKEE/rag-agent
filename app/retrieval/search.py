"""检索高层入口：混合检索（稠密+稀疏+RRF）→ 精排 → 权限过滤。

供问答 API / Agent 编排调用的唯一检索接口。
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.ingestion.embedder import get_embedder
from app.ingestion.indexer import MilvusIndexer
from app.retrieval.base import RetrievedChunk, build_filter
from app.retrieval.hybrid import rrf_fuse
from app.retrieval.reranker import NoopReranker, get_reranker

logger = logging.getLogger(__name__)


def search(
    query: str,
    top_k: int = 10,
    departments: list[str] | None = None,
    use_rerank: bool = True,
) -> list[RetrievedChunk]:
    """检索入口：query → 混合召回 → RRF → 精排。

    departments 传入用户可见部门列表（权限下推）；None 表示不限制。
    """
    settings = get_settings()
    embedder = get_embedder()
    indexer = MilvusIndexer(settings.milvus_uri, settings.milvus_collection, embedder.dim)
    indexer.ensure_collection()

    filter_expr = build_filter(departments)
    dense_hits = indexer.search_dense(embedder.embed_one(query), top_k, filter_expr)
    sparse_hits = indexer.search_sparse(query, top_k, filter_expr)
    fused = rrf_fuse(dense_hits, sparse_hits, top_k=top_k)

    if use_rerank:
        reranker = get_reranker()
        if not isinstance(reranker, NoopReranker):
            try:
                fused = reranker.rerank(query, fused)
            except Exception:
                # 精排失败不阻断问答：降级返回 RRF 融合结果
                logger.warning("rerank 失败，降级使用融合结果", exc_info=True)

    from app.observability.metrics import QA_RETRIEVED

    QA_RETRIEVED.set(len(fused))
    return fused
