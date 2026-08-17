"""会话 API（FR-30/31）：会话列表/创建/消息/删除 + 消息反馈。"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db import get_session
from app.models import ChatMessage, ChatSession, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


class SessionCreate(BaseModel):
    title: str = Field(default="新会话", max_length=200)


class FeedbackRequest(BaseModel):
    feedback: str = Field(pattern="^(up|down)$")


async def _get_owned_session(session_id: int, user: User, session: AsyncSession) -> ChatSession:
    sess = await session.get(ChatSession, session_id)
    if sess is None or sess.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")
    return sess


@router.get("")
async def list_sessions(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = await session.scalars(
        select(ChatSession)
        .where(ChatSession.user_id == user.id)
        .order_by(ChatSession.updated_at.desc())
        .limit(50)
    )
    return {"items": [s.to_dict() for s in rows]}


@router.post("", status_code=201)
async def create_session(
    body: SessionCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    sess = ChatSession(user_id=user.id, title=body.title)
    session.add(sess)
    await session.commit()
    await session.refresh(sess)
    return sess.to_dict()


@router.get("/{session_id}/messages")
async def list_messages(
    session_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_owned_session(session_id, user, session)
    rows = await session.scalars(
        select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id)
    )
    return {"items": [m.to_dict() for m in rows]}


@router.delete("/{session_id}")
async def delete_session(
    session_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    sess = await _get_owned_session(session_id, user, session)
    await session.execute(delete(ChatMessage).where(ChatMessage.session_id == session_id))
    await session.delete(sess)
    await session.commit()
    return {"deleted": session_id}


@router.post("/{session_id}/messages/{message_id}/feedback")
async def set_feedback(
    session_id: int,
    message_id: int,
    body: FeedbackRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_owned_session(session_id, user, session)
    msg = await session.get(ChatMessage, message_id)
    if msg is None or msg.session_id != session_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "消息不存在")
    msg.feedback = body.feedback
    await session.commit()
    logger.info("消息反馈：%s → %s（user=%s）", message_id, body.feedback, user.username)
    return {"message_id": message_id, "feedback": body.feedback}


async def save_qa_messages(
    user: User,
    session_id: int | None,
    question: str,
    answer: str,
    citations: list[dict],
) -> tuple[ChatSession, list[ChatMessage]]:
    """保存一轮问答到会话；无会话时自动创建（标题取问题前 20 字）。

    使用独立短会话：SSE 流结束后调用，不占用请求级长连接。
    """
    from app.db import session_factory

    async with session_factory() as session:
        sess = None
        if session_id is not None:
            sess = await session.get(ChatSession, session_id)
            if sess is None or sess.user_id != user.id:
                sess = None
        if sess is None:
            sess = ChatSession(user_id=user.id, title=question[:20])
            session.add(sess)
            await session.flush()

        user_msg = ChatMessage(session_id=sess.id, role="user", content=question)
        assistant_msg = ChatMessage(
            session_id=sess.id,
            role="assistant",
            content=answer,
            refs_json=json.dumps(citations, ensure_ascii=False),
        )
        session.add_all([user_msg, assistant_msg])
        await session.commit()
        return sess, [user_msg, assistant_msg]
