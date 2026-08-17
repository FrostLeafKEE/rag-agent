"""Langfuse 可观测接入（PRD FR-41）：问答链路全链路 trace。

使用方式（service 层）：
    async with trace_qa(question, departments, top_k) as t:
        chunks = retrieve(...)
        t.retrieval(query, chunks)                          # 检索 span
        async for delta in t.llm_stream(llm, messages):     # LLM generation span（流式）
            yield delta

实现要点：asynccontextmanager 的 contextvar 栈不跨任务传递，子 span 无法靠
"当前 observation" 自动嵌套，因此用显式 parent_span_id 关联（TraceContext）。

未配置 LANGFUSE_PUBLIC_KEY/SECRET_KEY 时自动降级为 noop（零外部调用）。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langfuse import Langfuse

from app.config import get_settings

logger = logging.getLogger(__name__)

_client: Langfuse | None = None
_client_lock = threading.Lock()  # FIX P2-18：并发首请求防重复初始化


def _get_client() -> Langfuse | None:
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:  # 双检锁：等待期间可能已被其他线程初始化
            return _client
        settings = get_settings()
        if settings.langfuse_public_key and settings.langfuse_secret_key:
            _client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host,
            )
            logger.info("Langfuse trace 已启用：%s", settings.langfuse_host)
        else:
            logger.info("未配置 Langfuse key，trace 降级为 noop")
    return _client


def _trace_context(trace_id: str, parent_span_id: str | None = None) -> dict:
    ctx = {"trace_id": trace_id}
    if parent_span_id:
        ctx["parent_span_id"] = parent_span_id
    return ctx


class QATrace:
    """问答链路 trace 句柄。"""

    trace_id: str | None = None

    def retrieval(self, query: str, results: list) -> None:  # noqa: ANN001
        """记录检索结果（doc_id + section_path 摘要）。"""

    async def llm_stream(self, llm, messages: list[dict]) -> AsyncIterator[str]:  # noqa: ANN001
        """LLM 流式生成，同时记录 generation span。"""
        async for piece in llm.stream(messages):
            yield piece


class _NoopQATrace(QATrace):
    pass


class _LangfuseQATrace(QATrace):
    def __init__(self, client: Langfuse, trace_id: str, qa_span) -> None:  # noqa: ANN001
        self._client = client
        self.trace_id = trace_id
        self._qa_span = qa_span

    def retrieval(self, query: str, results: list) -> None:  # noqa: ANN001
        summary = [
            {"doc_id": r.doc_id, "section": r.section_path, "score": r.score} for r in results
        ]
        span = self._client.start_observation(  # type: ignore  # Langfuse stub 与 kwargs 传参不符（4.x overload 签名限制）
            name="retrieval",
            trace_context=_trace_context(self.trace_id or "", self._qa_span.id),
            input={"query": query},
            output=summary,
            metadata={"hit_count": len(results)},
        )
        span.end()

    async def llm_stream(self, llm, messages: list[dict]) -> AsyncIterator[str]:  # noqa: ANN001
        settings = get_settings()
        answer_parts: list[str] = []
        generation = self._client.start_observation(  # type: ignore  # Langfuse stub 与 kwargs 传参不符（4.x overload 签名限制）
            name="llm",
            as_type="generation",
            trace_context=_trace_context(self.trace_id or "", self._qa_span.id),
            model=settings.llm_model,
            model_parameters={"temperature": settings.llm_temperature},
            input=messages,
        )
        async for delta in llm.stream(messages):
            answer_parts.append(delta)
            yield delta
        generation.update(output="".join(answer_parts))
        generation.end()


@asynccontextmanager
async def trace_qa(question: str, departments: list[str] | None, top_k: int):
    """进入问答 trace 上下文；未配置 key 时返回 noop。"""
    client = _get_client()
    if client is None:
        yield _NoopQATrace()
        return
    trace_id = client.create_trace_id()
    qa_span = client.start_observation(  # type: ignore  # Langfuse stub 与 kwargs 传参不符（4.x overload 签名限制）
        name="qa",
        trace_context=_trace_context(trace_id),
        input={"question": question, "departments": departments, "top_k": top_k},
    )
    try:
        yield _LangfuseQATrace(client, trace_id, qa_span)
    finally:
        qa_span.end()
        client.flush()
