"""解析器单元测试（Markdown / 纯文本路径，不依赖外部模型）。"""

from pathlib import Path

import pytest

from app.ingestion.parser import (
    SUPPORTED_EXTENSIONS,
    MarkdownParser,
    ParseError,
    PlainTextParser,
    parse_document,
)


def test_markdown_heading_levels(tmp_path: Path) -> None:
    md = tmp_path / "sample.md"
    md.write_text("# 一级\n## 二级\n正文。\n### 三级\n| a | b |", encoding="utf-8")
    blocks = MarkdownParser().parse(md)
    assert [b.level for b in blocks if b.kind == "heading"] == [1, 2, 3]
    assert any(b.kind == "table" for b in blocks)


def test_plain_text_split_by_blank_lines(tmp_path: Path) -> None:
    txt = tmp_path / "sample.txt"
    txt.write_text("第一段。\n\n第二段。\n第三行。", encoding="utf-8")
    blocks = PlainTextParser().parse(txt)
    assert len(blocks) == 2
    assert blocks[0].text == "第一段。"


def test_parse_document_routes_by_extension(tmp_path: Path) -> None:
    md = tmp_path / "doc.md"
    md.write_text("# 标题\n正文。", encoding="utf-8")
    blocks = parse_document(md)
    assert blocks[0].kind == "heading"
    assert blocks[0].text == "标题"


def test_unsupported_extension(tmp_path: Path) -> None:
    bad = tmp_path / "doc.xyz"
    bad.write_text("x")
    with pytest.raises(ParseError):
        parse_document(bad)


def test_supported_extensions_cover_p0_formats() -> None:
    assert {".pdf", ".docx", ".pptx", ".md", ".txt", ".html"} <= SUPPORTED_EXTENSIONS
