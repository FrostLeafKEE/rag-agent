"""分块器单元测试。"""

from app.ingestion.chunker import chunk_document
from app.ingestion.models import ParsedBlock


def _md_blocks(text: str) -> list[ParsedBlock]:
    blocks: list[ParsedBlock] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            blocks.append(ParsedBlock(text=line.lstrip("# "), level=level, kind="heading"))
        else:
            blocks.append(ParsedBlock(text=line))
    return blocks


def test_headings_start_new_chunks_and_track_section_path() -> None:
    blocks = _md_blocks(
        "# 产品概述\n这是概述正文。\n## 功能特性\n特性一说明。\n## 部署要求\n要求说明。"
    )
    chunks = chunk_document(blocks, doc_id="doc1", chunk_size=800, overlap=0)
    assert len(chunks) == 3
    assert chunks[0].section_path == "产品概述"
    assert chunks[1].section_path == "产品概述 > 功能特性"
    assert chunks[2].section_path == "产品概述 > 部署要求"


def test_heading_text_enters_chunk_content() -> None:
    """标题必须进入块内容，否则标题文本不可被检索（标量字段不参与召回）。"""
    blocks = _md_blocks("# 部署要求\n正文内容。")
    chunks = chunk_document(blocks, doc_id="doc1", chunk_size=800, overlap=0)
    assert chunks[0].content.startswith("部署要求")
    assert "正文内容" in chunks[0].content


def test_chunk_size_limit_splits_long_text() -> None:
    blocks = [ParsedBlock(text="字" * 2000)]
    chunks = chunk_document(blocks, doc_id="doc1", chunk_size=800, overlap=100)
    assert len(chunks) >= 3
    assert all(len(c.content) <= 800 for c in chunks)


def test_overlap_keeps_tail_context() -> None:
    blocks = [ParsedBlock(text="甲" * 700 + "乙" * 700 + "丙" * 700)]
    chunks = chunk_document(blocks, doc_id="doc1", chunk_size=800, overlap=100)
    assert len(chunks) == 3
    # 第二块开头应包含上一块尾部 100 字（重叠语境）
    assert chunks[1].content.startswith("乙" * 100)
    assert chunks[2].content.startswith("丙" * 100)


def test_table_block_kept_intact() -> None:
    table = "| 名称 | 端口 |\n| 部署 | 部署说明 |"
    blocks = [
        ParsedBlock(text="# 配置表", level=1, kind="heading"),
        ParsedBlock(text=table, kind="table"),
    ]
    chunks = chunk_document(blocks, doc_id="doc1", chunk_size=50, overlap=0)
    # 标题与表格同块（chunk_size 足够大）；表格不得被拆散
    assert len(chunks) == 1
    assert "配置表" in chunks[0].content
    assert table in chunks[0].content


def test_subheading_under_current_chapter() -> None:
    blocks = _md_blocks("# A\n正文一。\n## B\n正文二。\n### C\n正文三。\n## D\n正文四。")
    chunks = chunk_document(blocks, doc_id="doc1", chunk_size=800, overlap=0)
    assert len(chunks) == 4
    assert chunks[1].section_path == "A > B"
    assert chunks[2].section_path == "A > B > C"
    assert chunks[3].section_path == "A > D"  # B 的子章节被弹出


def test_empty_input() -> None:
    assert chunk_document([], doc_id="doc1") == []


def test_invalid_overlap() -> None:
    try:
        chunk_document([], doc_id="doc1", chunk_size=100, overlap=100)
    except ValueError:
        return
    raise AssertionError("overlap >= chunk_size 应抛 ValueError")
