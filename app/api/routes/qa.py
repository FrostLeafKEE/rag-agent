"""问答 API：POST /api/v1/qa/ask（SSE 流式，FR-27/28）。

需要 Bearer token 认证；检索权限由服务端按用户角色/部门决定（FR-33），
客户端不可指定部门范围（防止提权）。

请求体：
{
  "question": "问题",
  "history": [{"role": "user", "content": "..."}],   # 可选，多轮上下文
  "top_k": 6                                          # 可选，检索条数
}

SSE 事件：meta → delta* → citations → done；出错时 error。
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.service import stream_answer
from app.api.deps import get_current_user, user_visible_departments
from app.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


class HistoryItem(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=1000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[HistoryItem] = Field(
        default_factory=list, max_length=20
    )  # 上下文上限，防 token 滥用
    top_k: int = Field(default=6, ge=1, le=20)
    format: str = Field(default="", pattern="^(|json)$")  # 结构化输出（FR-26）
    session_id: int | None = None  # 续接会话（FR-30）


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/qa/ask")
async def ask(
    request: AskRequest,
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    # 注意：不声明会话依赖——SSE 流式期间依赖的收尾在响应发送后才执行，
    # 声明请求级 session 会全程占用连接池连接（高并发下池耗尽）
    async def event_stream():
        answer_parts: list[str] = []
        citations: list[dict] = []
        # RBAC 统一可见范围：super_admin→全库；admin→负责部门；user→本部门
        departments = await user_visible_departments(user)
        try:
            async for item in stream_answer(
                request.question,
                history=[h.model_dump() for h in request.history],
                departments=departments,
                top_k=request.top_k,
                output_format=request.format,
            ):
                if item["type"] == "delta":
                    answer_parts.append(item["text"])
                elif item["type"] == "citations":
                    citations = item["citations"]
                yield _sse(item["type"], item)

            # 会话持久化（FR-30）：独立短会话落库，流结束后返回 session 事件
            from app.api.routes.sessions import save_qa_messages

            sess, _ = await save_qa_messages(
                user,
                request.session_id,
                request.question,
                "".join(answer_parts),
                citations,
            )
            yield _sse("session", {"session_id": sess.id})
            yield _sse("done", {})
        except Exception:
            logger.exception("问答处理失败")
            yield _sse("error", {"message": "服务内部错误，请稍后重试"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
