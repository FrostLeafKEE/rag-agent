"""文档解析器：按扩展名路由到对应实现。

优先级：
  - Markdown / 纯文本：内建解析（零依赖、快）
  - PDF：Docling（布局感知，质量最高）；未装或失败时降级 PyMuPDF（启发式标题）
  - DOCX / PPTX / HTML：Docling（依赖 docling 包，首次调用会下载布局模型）
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from pathlib import Path

from app.ingestion.models import ParsedBlock

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".md", ".markdown", ".txt", ".html", ".htm"}


class ParseError(Exception):
    """解析失败（格式不支持、依赖缺失等）。"""


class DocumentParser(ABC):
    @abstractmethod
    def parse(self, path: Path) -> list[ParsedBlock]:
        """解析文档为结构化块，保持文档阅读顺序。"""


# ---------- Markdown / 纯文本 ----------

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


class MarkdownParser(DocumentParser):
    """Markdown：按 # 层级识别标题。"""

    def parse(self, path: Path) -> list[ParsedBlock]:
        blocks: list[ParsedBlock] = []
        for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if m := _MD_HEADING_RE.match(line):
                level = len(m.group(1))
                blocks.append(ParsedBlock(text=m.group(2).strip(), level=level, kind="heading"))
            elif line.startswith("|") and "|" in line[1:]:
                blocks.append(ParsedBlock(text=line, kind="table"))
            else:
                blocks.append(ParsedBlock(text=line))
        return blocks


class PlainTextParser(DocumentParser):
    """纯文本：按空行分段。"""

    def parse(self, path: Path) -> list[ParsedBlock]:
        content = path.read_text(encoding="utf-8", errors="replace")
        blocks = [
            ParsedBlock(text=paragraph.strip())
            for paragraph in re.split(r"\n\s*\n", content)
            if paragraph.strip()
        ]
        return blocks


# ---------- PDF ----------


class OcrEngine:
    """PaddleOCR 懒加载单例（FR-10）：首次调用下载模型，之后复用。

    依赖较重（paddlepaddle），仅扫描件 PDF 触发；不可用时返回空文本（降级）。
    """

    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            from paddleocr import PaddleOCR

            cls._instance = PaddleOCR(
                lang="ch",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                enable_mkldnn=False,  # paddle 3.x PIR 执行器与 oneDNN 不兼容
            )
        return cls._instance

    @classmethod
    def recognize(cls, image_path: Path) -> str:
        """识别图片中的中文文本。"""
        results = cls.get().predict(str(image_path))
        lines = []
        for res in results:
            lines.extend(res.get("rec_texts", []))
        return "\n".join(lines)


class PyMuPDFParser(DocumentParser):
    """文本型 PDF：PyMuPDF 提取，按字号/加粗启发式识别标题；扫描件走 OCR。"""

    OCR_MIN_TEXT = 20  # 整页文本低于此长度视为疑似扫描件

    def parse(self, path: Path) -> list[ParsedBlock]:
        import fitz  # PyMuPDF

        blocks: list[ParsedBlock] = []
        with fitz.open(path) as doc:
            for page in doc:
                page_text = page.get_text()
                if len(page_text.strip()) < self.OCR_MIN_TEXT:
                    blocks.extend(self._ocr_page(page))
                    continue
                blocks.extend(self._text_blocks(page))
        return blocks

    def _text_blocks(self, page) -> list[ParsedBlock]:  # noqa: ANN001
        blocks: list[ParsedBlock] = []
        for line_dict in page.get_text("dict")["blocks"]:
            if line_dict["type"] != 0:  # 跳过图片块
                continue
            for line in line_dict["lines"]:
                spans = [s for s in line["spans"] if s["text"].strip()]
                if not spans:
                    continue
                text = "".join(s["text"].strip() for s in spans)
                size = max(s["size"] for s in spans)
                bold = any("Bold" in s["font"] for s in spans)
                level = 1 if bold and size >= 12 else 0
                blocks.append(
                    ParsedBlock(
                        text=text,
                        level=level,
                        kind="heading" if level else "text",
                        page=page.number + 1,
                    )
                )
        return blocks

    def _ocr_page(self, page) -> list[ParsedBlock]:  # noqa: ANN001
        """扫描页：渲染为图片 → PaddleOCR 识别。"""
        import tempfile
        import uuid

        tmp_path = Path(tempfile.gettempdir()) / f"ocr_{uuid.uuid4().hex}.png"
        try:
            pix = page.get_pixmap(dpi=200)
            pix.save(str(tmp_path))
            text = OcrEngine.recognize(tmp_path)
        except Exception:
            logger.warning("OCR 失败（第 %d 页），跳过", page.number + 1, exc_info=True)
            return []
        finally:
            tmp_path.unlink(missing_ok=True)
        blocks = [
            ParsedBlock(text=line.strip(), page=page.number + 1)
            for line in text.splitlines()
            if line.strip()
        ]
        logger.info("OCR 识别第 %d 页：%d 行", page.number + 1, len(blocks))
        return blocks


class DoclingParser(DocumentParser):
    """布局感知解析（Docling）：表格保真、标题层级、页码。

    依赖较重（torch），首次调用会下载布局模型；仅在显式需要时实例化。
    """

    def __init__(self) -> None:
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.document_converter import DocumentConverter
        except ImportError as e:  # pragma: no cover - 依赖缺失
            raise ParseError("Docling 未安装，无法解析该格式。请先 `uv add docling`") from e
        self._converter = DocumentConverter(
            allowed_formats=[
                InputFormat.PDF,
                InputFormat.DOCX,
                InputFormat.PPTX,
                InputFormat.HTML,
            ]
        )

    def parse(self, path: Path) -> list[ParsedBlock]:
        result = self._converter.convert(path)
        blocks: list[ParsedBlock] = []
        for item, level in result.document.iterate_items():
            if item.label.value == "table":  # type: ignore  # docling stub 缺 NodeItem.label
                blocks.append(ParsedBlock(text=item.export_to_markdown(), kind="table"))  # type: ignore  # docling stub 缺 export_to_markdown
            elif item.label.value in ("title", "section_heading"):  # type: ignore  # docling stub 缺 NodeItem.label
                text = " ".join(item.text.split())  # type: ignore  # docling stub 缺 NodeItem.text
                blocks.append(
                    ParsedBlock(
                        text=text,
                        level=max(1, min(level, 6)),
                        kind="heading",
                    )
                )
            else:
                text = " ".join(item.text.split())  # type: ignore  # docling stub 缺 NodeItem.text
                if text:
                    blocks.append(ParsedBlock(text=text))
        return blocks


# ---------- 路由 ----------

_PARSERS: dict[str, DocumentParser] = {}


def _register(ext: str, parser: DocumentParser) -> None:
    _PARSERS[ext] = parser


_register(".md", MarkdownParser())
_register(".markdown", MarkdownParser())
_register(".txt", PlainTextParser())


def parse_document(path: Path) -> list[ParsedBlock]:
    """按扩展名路由解析；PDF 优先 Docling，失败降级 PyMuPDF。"""
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ParseError(f"不支持的格式：{ext}（支持：{sorted(SUPPORTED_EXTENSIONS)}）")

    if ext in _PARSERS:
        return _PARSERS[ext].parse(path)

    if ext == ".pdf":
        try:
            return DoclingParser().parse(path)
        except ParseError:
            raise
        except Exception:
            logger.warning("Docling 解析失败，降级 PyMuPDF：%s", path, exc_info=True)
            return PyMuPDFParser().parse(path)

    if ext in {".docx", ".pptx", ".html", ".htm"}:
        try:
            return DoclingParser().parse(path)
        except ParseError as e:
            raise ParseError(f"{ext} 需要 Docling：{e}") from e

    raise ParseError(f"不支持的格式：{ext}")
