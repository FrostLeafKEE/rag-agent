"""Agent 图节点实现（P1）。

- route_intent：LLM 结构化输出识别意图（FR-21）
- decompose：多跳拆解——"对比/兼容/关系"类问题拆成子问题（FR-24）
- pre_rewrite：主动 query 改写（检索前一次，提升召回，FR-17）
- multi_query：多查询扩展——生成 N 个检索变体（FR-19）
- retrieve：按 queries 列表检索并合并去重
- assess：CRAG 置信评估（FR-23）——低置信触发重写重检索
- rewrite_query：CRAG 查询重写
- assemble：组装生成消息（含上下文编号、结构化输出指令）
- refuse：闲聊拒答 / 澄清追问

提示词注意：含 JSON 示例花括号时必须用 {{}} 转义（str.format 会解析花括号）。
"""

from __future__ import annotations

import asyncio
import json
import logging

from app.agent.state import QAState
from app.agent.tools import tool_registry
from app.config import get_settings
from app.llm.gateway import get_llm
from app.llm.prompts import SYSTEM_PROMPT, build_context
from app.retrieval.search import search as run_search
from app.retrieval.terms import expand_terms

logger = logging.getLogger(__name__)

ROUTE_PROMPT = """判断用户问题的意图，只输出 JSON（不要输出其他内容）：
{{"intent": "qa"|"summary"|"chat"|"clarify", "reason": "一句话说明"}}

- qa：向企业知识库查询事实/信息（默认，拿不准就选这个）
- summary：要求总结某个文档或主题
- chat：与知识库无关的闲聊、时事、个人问题等
- clarify：问题含糊不清、缺少必要信息，需要向用户追问

问题：{question}"""

DECOMPOSE_PROMPT = """判断问题是否需要拆分成多个子问题分别检索后才能回答。
典型需要拆分：对比/兼容性/关系类（"A 与 B 的…"）、需要跨主题的信息组合。
只输出 JSON（不要输出其他内容）：
{{"decompose": true|false, "sub_questions": ["子问题1", "子问题2"]}}
不需要拆分时 sub_questions 为空数组。子问题要自包含、可直接检索。

问题：{question}"""

PRE_REWRITE_PROMPT = (
    "把下面的问题改写成更适合在文档库中检索的表述"
    "（补全指代、展开缩写、去掉口语），只输出改写后的文本本身，不要任何解释或标点包裹。\n\n"
    "原问题：{question}\n对话历史：{history}\n改写后："
)

MULTI_QUERY_PROMPT = (
    "针对下面的问题，从不同角度生成 {count} 个检索式"
    "（覆盖同义词、不同表述、子主题），只输出 JSON 数组（不要输出其他内容）。\n\n"
    "问题：{question}"
)

TOOL_PLAN_PROMPT = """判断是否需要调用工具来回答这个问题（仅结构化数据查询类问题）。
可用工具：
{tools}

规则：
- 仅当问题需要查询结构化数据（统计/状态/表单等，文档正文无法覆盖）时才选择工具，并给出参数；
- 其余情况 tool 必须为 null，不要为了调用而调用；
- 只输出 JSON（不要输出其他内容）：
{{"tool": "工具名"|null, "args": {{...}}, "reason": "一句话"}}

问题：{question}"""

CRAG_REWRITE_PROMPT = (
    "根据首次检索结果不理想的情况，改写查询以便重新检索（换关键词、调整表述），"
    "只输出改写后的文本本身，不要任何解释。\n\n"
    "原问题：{question}\n对话历史：{history}\n改写后："
)

REFUSAL_CHAT = (
    "抱歉，我只能回答企业知识库相关的问题（如产品、制度、运维、技术文档等）。"
    "如果有内部文档方面的问题，欢迎提问。"
)
REFUSAL_CLARIFY = (
    "这个问题有点模糊，我需要再确认一下：您具体想了解哪个方面？"
    "可以补充说明一下背景或您想查询的文档类型。"
)

JSON_OUTPUT_INSTRUCTION = (
    "\n\n【输出要求】回答必须以合法 JSON 输出（数组或对象，与问题对应的结构），"
    "不要用 markdown 代码块包裹，不要输出 JSON 以外的内容。"
)


def _history_text(state: QAState) -> str:
    history = state.get("history") or []
    return "；".join(f"{h['role']}: {h['content'][:80]}" for h in history[-4:]) or "无"


def _extract_json(raw: str) -> str:
    """从 LLM 输出中收紧到 JSON 主体（兼容 ```json 包裹/前后解释文本）。"""
    for opening, closing in (("{", "}"), ("[", "]")):
        start, end = raw.find(opening), raw.rfind(closing)
        if start != -1 and end > start:
            return raw[start : end + 1]
    return raw


async def _complete_json(llm, prompt: str) -> dict | None:
    """调 LLM 并解析 JSON；失败返回 None。"""
    try:
        raw = await asyncio.to_thread(
            llm.complete, [{"role": "user", "content": prompt}], temperature=0
        )
        return json.loads(_extract_json(raw))
    except Exception:
        logger.warning("LLM JSON 解析失败：%s", prompt[:40])
        return None


async def _complete_text(llm, prompt: str) -> str:
    try:
        raw = await asyncio.to_thread(
            llm.complete, [{"role": "user", "content": prompt}], temperature=0
        )
        return raw.strip().strip('"').strip("「」")
    except Exception:
        return ""


async def route_intent(state: QAState) -> QAState:
    data = await _complete_json(get_llm(), ROUTE_PROMPT.format(question=state["question"]))
    intent = (data or {}).get("intent", "qa")
    if intent not in ("qa", "summary", "chat", "clarify"):
        intent = "qa"
    return {**state, "intent": intent, "rewrite_count": 0, "trace": {"rewrites": []}}


async def plan_tools(state: QAState) -> QAState:
    """工具规划（FR-25）：问答/总结意图先判断是否需调用工具，结果注入生成上下文。

    工具调用失败不阻断主流程：以 {ok: false, error} 记入 tool_results 供观测。
    """
    settings = get_settings()
    tool_results: list[dict] = []
    if settings.agent_tools_enabled and state["intent"] in ("qa", "summary"):
        if tool_registry.all():
            prompt = TOOL_PLAN_PROMPT.format(
                tools=tool_registry.describe(), question=state["question"]
            )
            data = await _complete_json(get_llm(), prompt)
            tool_name = (data or {}).get("tool")
            args = (data or {}).get("args")
            if tool_name and isinstance(tool_name, str):
                args = args if isinstance(args, dict) else {}
                result = await tool_registry.call(tool_name, args)
                tool_results.append({"tool": tool_name, "args": args, **result})
                state["trace"]["tools"] = tool_results
    return {**state, "tool_results": tool_results}


async def decompose(state: QAState) -> QAState:
    """多跳拆解：需要拆分的设置子问题；否则原问题单查询。"""
    settings = get_settings()
    if not settings.multi_hop_enabled:
        return {**state, "sub_questions": [], "queries": [state["question"]]}
    data = await _complete_json(get_llm(), DECOMPOSE_PROMPT.format(question=state["question"]))
    sub = (data or {}).get("sub_questions") or []
    if data and data.get("decompose") and sub:
        state["trace"]["decomposed"] = sub
        return {**state, "sub_questions": sub, "queries": sub}
    return {**state, "sub_questions": [], "queries": [state["question"]]}


async def pre_rewrite(state: QAState) -> QAState:
    """主动改写（仅单查询路径；已拆解的多跳路径跳过）。"""
    settings = get_settings()
    if not settings.query_rewrite_enabled:
        return state
    rewritten = await _complete_text(
        get_llm(),
        PRE_REWRITE_PROMPT.format(question=state["question"], history=_history_text(state)),
    )
    if rewritten:
        state["trace"]["pre_rewrite"] = rewritten
        return {**state, "queries": [rewritten], "rewritten_query": rewritten}
    return state


async def multi_query(state: QAState) -> QAState:
    """多查询扩展：基于当前查询生成 N 个变体。"""
    settings = get_settings()
    if not settings.multi_query_enabled:
        return state
    base = state["queries"][0]
    data = await _complete_json(
        get_llm(), MULTI_QUERY_PROMPT.format(question=base, count=settings.multi_query_count)
    )
    variants = data if isinstance(data, list) else []
    variants = [
        v for v in variants if isinstance(v, str) and v.strip()
    ][: settings.multi_query_count]
    if variants:
        state["trace"]["multi_queries"] = variants
        return {**state, "queries": [base, *variants]}
    return state


async def retrieve(state: QAState) -> QAState:
    """按 queries 列表逐条检索，按 chunk_id 去重合并。"""
    queries = state.get("queries") or [state.get("rewritten_query") or state["question"]]
    merged: list = []
    seen: set[int] = set()
    for query in queries:
        chunks = await asyncio.to_thread(
            run_search, query, top_k=state["top_k"], departments=state.get("departments")
        )
        for chunk in chunks:
            if chunk.chunk_id not in seen:
                seen.add(chunk.chunk_id)
                merged.append(chunk)
    return {**state, "chunks": merged}


async def term_expand(state: QAState) -> QAState:
    """术语表变体（P2 W5）：主查询命中别名时，把替换后的变体并入查询集合。

    变体与原文一起去检索（召回互补），总查询数上限 6 条控制成本。
    """
    settings = get_settings()
    if not settings.term_expand_enabled:
        return state
    queries = state.get("queries") or [state["question"]]
    base = queries[0]
    variants = expand_terms(base, settings.term_config)
    if not variants:
        return state
    state["trace"]["term_variants"] = variants
    merged: list[str] = []
    for q in [*queries, *variants]:
        if q not in merged:
            merged.append(q)
    return {**state, "queries": merged[:6]}


async def assess(state: QAState) -> QAState:
    """CRAG 评估：无结果或 top1 分过低 → 重写重检索；超轮数则降级用现有结果。"""
    settings = get_settings()
    chunks = state["chunks"]
    needs_rewrite = (not chunks) or (chunks[0].score < settings.crag_min_score)
    can_rewrite = state["rewrite_count"] < settings.crag_max_rewrites
    if needs_rewrite and can_rewrite:
        state["trace"]["rewrites"].append(
            {
                "round": state["rewrite_count"] + 1,
                "reason": "低置信",
                "top_score": chunks[0].score if chunks else None,
            }
        )
        return {**state, "rewrite": True}
    return {**state, "rewrite": False}


async def rewrite_query(state: QAState) -> QAState:
    rewritten = await _complete_text(
        get_llm(),
        CRAG_REWRITE_PROMPT.format(question=state["question"], history=_history_text(state)),
    )
    return {
        **state,
        "queries": [rewritten or state["question"]],
        "rewritten_query": rewritten or state["question"],
        "rewrite_count": state["rewrite_count"] + 1,
    }


async def assemble(state: QAState) -> QAState:
    system = SYSTEM_PROMPT.format(context=build_context(state["chunks"]))
    ok_tools = [t for t in state.get("tool_results") or [] if t.get("ok")]
    if ok_tools:
        blocks = [json.dumps(t["result"], ensure_ascii=False) for t in ok_tools]
        system += (
            "\n\n【结构化数据】（来自工具查询结果，可据此回答，无需标注来源编号）\n"
            + "\n\n".join(blocks)
        )
    if state.get("format") == "json":
        system += JSON_OUTPUT_INSTRUCTION
    messages: list[dict] = [{"role": "system", "content": system}]
    if state.get("history"):
        messages.extend(state["history"][-8:])
    messages.append({"role": "user", "content": state["question"]})
    return {**state, "messages": messages}


async def refuse(state: QAState) -> QAState:
    text = REFUSAL_CLARIFY if state["intent"] == "clarify" else REFUSAL_CHAT
    return {**state, "refusal": text, "chunks": []}
