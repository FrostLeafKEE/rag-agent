"""结构感知分块：按标题层级切分，表格整块保留，超长正文按窗口切。

策略（PRD FR-11/FR-12）：
  1. 遇到 1~2 级标题 → 强制开启新块，并维护章节路径（section_path）；
  2. 表格 → 并入当前块（不跨块拆分，保证表格完整性）；
  3. 正文累积到 chunk_size（字符）即切块，相邻块保留 overlap 重叠；
  4. 任一标题级别降级时也切块（如 3.1 → 3.2），避免块内混杂多章节。
"""

from __future__ import annotations

import logging

from app.ingestion.models import DocumentChunk, ParsedBlock

logger = logging.getLogger(__name__)

_TABLE_MIN_LEN = 8  # 过短的行视为噪声，不按表格处理


def chunk_document(
    blocks: list[ParsedBlock],
    doc_id: str,
    chunk_size: int = 800,
    overlap: int = 100,
    department: str = "",
) -> list[DocumentChunk]:
    """把解析块流切分为检索单元。blocks 必须保持文档顺序。"""
    if overlap >= chunk_size:
        raise ValueError("overlap 必须小于 chunk_size")

    chunks: list[DocumentChunk] = []
    current: list[str] = []
    current_len = 0
    section_stack: list[tuple[int, str]] = []  # (level, title) 栈，用于章节路径
    current_page: int | None = None

    def section_path() -> str:
        return " > ".join(t for _, t in section_stack)

    def emit(content: str) -> None:
        """直接把内容作为独立 chunk 输出（超长段落切窗用）。"""
        chunks.append(
            DocumentChunk(
                doc_id=doc_id,
                chunk_index=len(chunks),
                content=content,
                page=current_page,
                section_path=section_path(),
                department=department,
            )
        )

    def flush() -> None:
        nonlocal current, current_len
        if not current:
            return
        text = "\n".join(current)
        emit(text)
        # 保留 overlap：取上一块尾部若干字符作为新块开头，维持语境
        if overlap and len(text) > overlap:
            tail = text[-overlap:]
            current = [tail]
            current_len = len(tail)
        else:
            current = []
            current_len = 0

    for block in blocks:
        if block.kind == "heading":
            flush()
            # 章节栈维护：弹出所有不低于当前级别的栈顶
            while section_stack and section_stack[-1][0] >= block.level:
                section_stack.pop()
            section_stack.append((block.level, block.text))
            current_page = block.page
            # 标题进入新块内容（仅存 section_path 的话标题不可检索——标量字段不参与召回）
            current = [block.text]
            current_len = len(block.text) + 1
            continue

        if block.kind == "table" and len(block.text) >= _TABLE_MIN_LEN:
            if current_len + len(block.text) + 1 > chunk_size and current:
                flush()
            current.append(block.text)
            current_len += len(block.text) + 1
            if block.page:
                current_page = block.page
            continue

        # 正文段落：累积，超限则切块
        paragraph = block.text.strip()
        if not paragraph:
            continue
        if block.page:
            current_page = block.page

        if current_len + len(paragraph) + 1 > chunk_size and current:
            flush()

        # 单段超长（长于 chunk_size）：独立切窗，窗长 chunk_size、步长 chunk_size-overlap
        if len(paragraph) > chunk_size:
            flush()  # 清空累积（含 overlap 尾部）
            start = 0
            while start + chunk_size < len(paragraph):
                emit(paragraph[start : start + chunk_size])
                start += chunk_size - overlap
            paragraph = paragraph[start:]
            if not paragraph:
                continue

        current.append(paragraph)
        current_len += len(paragraph) + 1

    flush()
    return chunks
