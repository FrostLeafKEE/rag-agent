"""Agent 编排图（LangGraph）：路由 → 拆解/改写/多查询 → 检索 ⇄ 反思 → 组装。

图只负责"决策"，LLM 流式生成由 service 驱动（保持 SSE 契约）。
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    assemble,
    assess,
    decompose,
    multi_query,
    plan_tools,
    pre_rewrite,
    refuse,
    retrieve,
    rewrite_query,
    route_intent,
    term_expand,
)
from app.agent.state import QAState


def _route_after_intent(state: QAState) -> str:
    """非问答意图直接拒答/澄清；问答类先进工具规划再拆解。"""
    if state["intent"] in ("chat", "clarify"):
        return "refuse"
    return "plan_tools"


def _route_after_decompose(state: QAState) -> str:
    """已拆解为子问题 → 直接检索；单查询 → 主动改写 + 多查询。"""
    if state.get("sub_questions"):
        return "retrieve"
    return "pre_rewrite"


def _route_after_assess(state: QAState) -> str:
    return "rewrite_query" if state["rewrite"] else "assemble"


def build_graph():
    builder = StateGraph(QAState)
    builder.add_node("route_intent", route_intent)
    builder.add_node("plan_tools", plan_tools)
    builder.add_node("decompose", decompose)
    builder.add_node("pre_rewrite", pre_rewrite)
    builder.add_node("multi_query", multi_query)
    builder.add_node("term_expand", term_expand)
    builder.add_node("retrieve", retrieve)
    builder.add_node("assess", assess)
    builder.add_node("rewrite_query", rewrite_query)
    builder.add_node("assemble", assemble)
    builder.add_node("refuse", refuse)

    builder.add_edge(START, "route_intent")
    builder.add_conditional_edges("route_intent", _route_after_intent)
    builder.add_edge("plan_tools", "decompose")
    builder.add_conditional_edges("decompose", _route_after_decompose)
    builder.add_edge("pre_rewrite", "multi_query")
    builder.add_edge("multi_query", "term_expand")
    builder.add_edge("term_expand", "retrieve")
    builder.add_edge("retrieve", "assess")
    builder.add_conditional_edges("assess", _route_after_assess)
    builder.add_edge("rewrite_query", "retrieve")
    builder.add_edge("assemble", END)
    builder.add_edge("refuse", END)
    return builder.compile()


agent_graph = build_graph()
