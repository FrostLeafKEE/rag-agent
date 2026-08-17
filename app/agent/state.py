"""Agent 图状态定义（P1 W1：意图路由 + CRAG 反思）。"""

from __future__ import annotations

from typing import TypedDict

from app.retrieval.base import RetrievedChunk

# 意图类型：知识问答 / 文档总结 / 闲聊拒答 / 需澄清
Intent = str  # qa | summary | chat | clarify


class QAState(TypedDict, total=False):
    # 输入
    question: str
    history: list[dict]
    departments: list[str] | None
    top_k: int
    format: str  # 输出格式："" | "json"（FR-26）
    # 路由
    intent: Intent
    # 检索与反思
    queries: list[str]  # 实际检索用查询列表（改写/多查询/子问题后的最终集合）
    chunks: list[RetrievedChunk]
    rewrite: bool  # 本轮是否触发重写
    rewrite_count: int
    rewritten_query: str
    sub_questions: list[str]  # 多跳拆解的子问题（FR-24）
    # 工具（FR-25）
    tool_results: list[dict]  # 工具调用结果 [{tool, args, ok, result|error}]，注入生成上下文
    # 输出
    messages: list[dict]  # 最终 LLM 消息（组装后）
    refusal: str  # 非问答意图时的拒答/澄清文本
    trace: dict  # 反思过程记录（供观测）
