"""业务数据模型（SQLAlchemy）。P0/P1：users、documents、sessions、messages；P2：ingested_files、admin_departments。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16), default="user")  # super_admin | admin | user
    department: Mapped[str] = mapped_column(String(64), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "department": self.department,
            "is_active": self.is_active,
        }


class AdminDepartment(Base):
    """部门管理员的负责部门集合（RBAC 扩展）：admin 仅可管理这些部门的文档。

    普通用户仍用 User.department 单部门；super_admin 不限（None）。
    """

    __tablename__ = "admin_departments"
    __table_args__ = (UniqueConstraint("user_id", "department", name="uq_admin_dept"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    department: Mapped[str] = mapped_column(String(64))


class IngestionReport(Base):
    """摄入质量报告（数据清洗与质量门禁）：每篇文档最近一次摄入的统计。

    doc_id 唯一（重摄入覆盖更新，与 documents 语义一致）；
    noise_reasons 为 JSON 字符串 {"reason": 次数}，记录每种丢弃原因。
    """

    __tablename__ = "ingestion_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    total_blocks: Mapped[int] = mapped_column(Integer, default=0)  # 清洗前 chunk 总数
    filtered_blocks: Mapped[int] = mapped_column(Integer, default=0)  # 规则清洗丢弃数
    dedup_skipped: Mapped[int] = mapped_column(Integer, default=0)  # 内容去重跳过数
    avg_chunk_length: Mapped[float] = mapped_column(Float, default=0.0)  # 保留块平均长度
    empty_pages: Mapped[int] = mapped_column(Integer, default=0)  # 全块被过滤的页数
    noise_reasons: Mapped[str] = mapped_column(Text, default="{}")  # JSON 原因计数
    llm_cleaned: Mapped[int] = mapped_column(Integer, default=0)  # LLM 清洗成功数
    llm_fallback: Mapped[int] = mapped_column(Integer, default=0)  # LLM 清洗回退数
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Document(Base):
    """文档元数据（PRD FR-03）：摄入状态与权限标签，关联 Milvus 中的 chunks。"""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    source_name: Mapped[str] = mapped_column(String(256), default="")  # 原始文件名
    department: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(
        String(16), default="uploading"
    )  # uploading|indexed|failed|disabled
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(512), default="")
    uploaded_by: Mapped[str] = mapped_column(String(64), default="")
    # 清洗后全文 sha1（内容级去重：解决"A.docx 与 A.pdf 同内容"；B-Tree 索引加速查重）
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "doc_id": self.doc_id,
            "title": self.title,
            "source_name": self.source_name,
            "department": self.department,
            "status": self.status,
            "chunk_count": self.chunk_count,
            "error": self.error,
            "uploaded_by": self.uploaded_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class AuditLog(Base):
    """审计日志（FR-34）：登录/上传/删除/越权等敏感事件。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user: Mapped[str] = mapped_column(String(64), default="")  # 操作人（匿名为空）
    action: Mapped[str] = mapped_column(
        String(32), index=True
    )  # register|login|upload|delete|denied|user_admin
    resource: Mapped[str] = mapped_column(String(128), default="")
    detail: Mapped[str] = mapped_column(String(512), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user": self.user,
            "action": self.action,
            "resource": self.resource,
            "detail": self.detail,
            "ip": self.ip,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ChatSession(Base):
    """问答会话（FR-30）：标题 + 消息序列。"""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(200), default="新会话")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ChatMessage(Base):
    """会话消息（含引用 JSON 与用户反馈，FR-31）。"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    refs_json: Mapped[str] = mapped_column(Text, default="")  # citations JSON
    feedback: Mapped[str] = mapped_column(String(8), default="")  # "" | up | down
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "refs": self.refs_json,
            "feedback": self.feedback,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class IngestedFile(Base):
    """Connector 已摄入文件指纹（FR-08，P2 W3）：指纹未变则跳过，变化则重新摄入。

    path_key = sha1(绝对路径)；fingerprint = sha1(size:mtime_ns)。
    """

    __tablename__ = "ingested_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(1024))
    path_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    mtime_ns: Mapped[int] = mapped_column(BigInteger, default=0)  # 纳秒时间戳超 int32，用 BIGINT
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    department: Mapped[str] = mapped_column(String(64), default="")
    doc_id: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
