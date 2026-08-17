"""Prometheus 指标（FR-42）：请求量/延迟分位 + 检索命中 + 摄入任务状态。"""

from __future__ import annotations

import time

from fastapi import Request
from prometheus_client import Counter, Gauge, Histogram
from prometheus_client.exposition import CONTENT_TYPE_LATEST, generate_latest

HTTP_REQUESTS = Counter("rag_http_requests_total", "HTTP 请求数", ["method", "path", "status"])
HTTP_LATENCY = Histogram(
    "rag_http_latency_seconds",
    "HTTP 请求延迟",
    ["method", "path"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
QA_RETRIEVED = Gauge("rag_qa_retrieved_chunks", "问答检索命中块数（最近一次）")
QA_LLM_CALLS = Counter("rag_llm_calls_total", "LLM 调用次数（含路由/改写/生成）")
INGESTION_TASKS = Gauge("rag_ingestion_tasks", "摄入任务数（按状态）", ["status"])
EMBEDDING_CALLS = Counter("rag_embedding_calls_total", "嵌入 API 调用批次")


async def metrics_middleware(request: Request, call_next):
    """请求指标中间件：计数 + 延迟。

    label 用路由模板而非实际路径（FIX P2-14）——动态路径（会话/文档 id）
    进 label 会造成高基数撑爆 Prometheus 存储；无路由的请求归并 unknown。
    """
    start = time.perf_counter()
    response = await call_next(request)
    latency = time.perf_counter() - start
    route = request.scope.get("route")
    path = route.path if route is not None else "unknown"
    HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
    HTTP_LATENCY.labels(request.method, path).observe(latency)
    return response


def metrics_response():
    """/metrics 端点响应。"""
    from fastapi import Response

    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
