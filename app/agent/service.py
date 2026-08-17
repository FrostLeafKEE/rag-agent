"""问答编排服务：LangGraph 图（路由/检索/CRAG 反思）→ LLM 流式生成 → 引用溯源。

SSE 契约保持 P0 兼容：meta → delta* → citations → done；新增 intent 事件（前端可忽略）。
P0 的线性流程已由 agent_graph 取代（可回退：service 层不感知图内部实现）。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from app.agent.graph import agent_graph
from app.agent.state import QAState
from app.llm.gateway import get_llm
from app.observability.tracing import trace_qa
from app.retrieval.base import RetrievedChunk
from app.security.redaction import get_engine

logger = logging.getLogger(__name__)

# 流式帧缓冲尾长：> 最长脱敏模式（身份证 18 位），防跨帧漏脱
_PENDING_TAIL = 32


async def _stream_redacted(source: AsyncIterator[str], engine) -> AsyncIterator[str]:
    """流式 delta 出口脱敏（FR-36）：帧间保留尾部缓冲，模式跨帧也能命中。"""
    pending = ""
    async for delta in source:
        pending += delta
        if len(pending) > _PENDING_TAIL:
            head, pending = pending[:-_PENDING_TAIL], pending[-_PENDING_TAIL:]
            if head:
                yield engine.redact(head)[0]
    if pending:
        yield engine.redact(pending)[0]


def _to_citation(chunk: RetrievedChunk) -> dict:
    return {
        "index": 0,  # 由调用方补齐
        "doc_id": chunk.doc_id,
        "chunk_index": chunk.chunk_index,
        "section_path": chunk.section_path,
        "content": chunk.content[:500],
        "score": chunk.score,
    }


async def stream_answer(
    question: str,
    history: list[dict] | None = None,
    departments: list[str] | None = None,
    top_k: int = 6,
    output_format: str = "",
) -> AsyncIterator[dict]:
    """问答流：产出 {"type": "meta"|"intent"|"delta"|"citations"} 事件。"""
    async with trace_qa(question, departments, top_k) as trace:
        result: QAState = await agent_graph.ainvoke(
            {
                "question": question,
                "history": history or [],
                "departments": departments,
                "top_k": top_k,
                "format": output_format,
            }
        )

        yield {"type": "meta", "retrieved": len(result.get("chunks") or [])}
        yield {"type": "intent", "intent": result.get("intent", "qa")}

        # 非问答意图：直接输出拒答/澄清文本（不调生成 LLM，省成本）
        if result.get("refusal"):
            yield {"type": "delta", "text": result["refusal"]}
            yield {"type": "citations", "citations": []}
            return

        chunks = result["chunks"]
        trace.retrieval(question, chunks)

        # 生成出口脱敏（FR-36）：delta 逐帧 + 引用预览内容（trace 仍保留）
        engine = get_engine()
        llm = get_llm()
        raw_stream = trace.llm_stream(llm, result["messages"])
        stream = _stream_redacted(raw_stream, engine) if engine.enabled else raw_stream
        async for delta in stream:
            yield {"type": "delta", "text": delta}

        citations = []
        for i, chunk in enumerate(chunks, start=1):
            citation = _to_citation(chunk)
            citation["index"] = i
            if engine.enabled:
                citation["content"] = engine.redact(citation["content"])[0]
            citations.append(citation)
        yield {"type": "citations", "citations": citations}
