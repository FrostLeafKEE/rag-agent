"""提示词工程：系统提示词与上下文组装（引用标注、拒答规则，PRD FR-27~29）。"""

from __future__ import annotations

from app.retrieval.base import RetrievedChunk

SYSTEM_PROMPT = """你是企业知识库问答助手，帮助员工从内部文档中获取准确信息。

规则：
1. 只能依据【知识库上下文】中的内容回答，禁止编造文档里没有的信息。
2. 回答中引用来源时，在对应句子末尾用 [n] 标注（n 是上下文的编号，如 [1][2]）。
3. 若上下文不足以回答问题，明确说明"知识库中未找到相关依据"，不要猜测。
4. 与知识库无关的问题（闲聊、时事等）礼貌告知只能回答知识库相关问题。
5. 直接回答问题的核心：先给结论，再补充必要细节；只输出与问题直接相关的内容，
   不展开背景、推论、实现状态或文档结构等无关信息；回答尽量简短（通常 1~3 句）。
6. 用中文回答。

【知识库上下文】
{context}"""


def build_context(chunks: list[RetrievedChunk]) -> str:
    """把检索结果组装为带编号的上下文块（编号即回答中的引用标记）。"""
    if not chunks:
        return "（无检索结果）"
    blocks = []
    for i, chunk in enumerate(chunks, start=1):
        source = f"{chunk.doc_id}"
        if chunk.section_path:
            source += f" › {chunk.section_path}"
        blocks.append(f"[{i}] 来源：{source}\n{chunk.content}")
    return "\n\n".join(blocks)
