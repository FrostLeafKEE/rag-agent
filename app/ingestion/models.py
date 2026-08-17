"""摄入管道数据模型。

解析产物 ParsedBlock → 分块产物 DocumentChunk。
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class ParsedBlock:
    """解析器输出的结构化块：一个标题、一段正文或一个表格。"""

    text: str
    level: int = 0  # 标题层级：0=正文，1=一级标题，依此类推
    kind: str = "text"  # text | heading | table
    page: int | None = None


@dataclass
class DocumentChunk:
    """写入向量库的检索单元。"""

    doc_id: str
    chunk_index: int
    content: str
    page: int | None = None
    section_path: str = ""  # 章节路径，如 "3. 技术选型 > 3.1 运行时"
    department: str = ""  # 权限标签，检索时参与 filter
    updated_at: int = field(default_factory=lambda: int(datetime.now(UTC).timestamp()))
