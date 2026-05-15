"""High-level AI orchestration.

`chat`: persistent chat session with tool use.
`build_widget_only`: one-shot widget builder for the AI page.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..adapters.llm import get_llm
from ..ai.prompts import SYSTEM_NFL_ASSISTANT
from ..ai.tools import ToolRunner, get_tool_definitions
from ..ai.widget_builder import build_widget_from_prompt
from ..logging_config import get_logger
from ..models.chat import ChatMessage, ChatSession
from ..schemas.widget import WidgetSpec
from .cost_service import BudgetExceeded, check_budget, estimate_tokens, ledger

log = get_logger(__name__)


async def chat(
    db: Session,
    user_id: uuid.UUID,
    user_message: str,
    session_id: uuid.UUID | None = None,
    enable_tools: bool = True,
) -> dict[str, Any]:
    # Hard budget gate — refuses before spending tokens.
    check_budget(str(user_id))

    session = _get_or_create_session(db, user_id, session_id, user_message)
    history = _load_history(db, session.id)
    history.append({"role": "user", "content": user_message})
    db.add(ChatMessage(session_id=session.id, role="user", content=user_message))
    db.commit()

    llm = get_llm()
    runner = ToolRunner(db, user_id)

    if enable_tools:
        result = await llm.chat_with_tools(
            messages=history,
            tools=get_tool_definitions(),
            tool_runner=runner,
            system=SYSTEM_NFL_ASSISTANT,
        )
        content = result["content"]
        transcript = result["messages"]
    else:
        content = await llm.chat(history, system=SYSTEM_NFL_ASSISTANT)
        transcript = history + [{"role": "assistant", "content": content}]

    db.add(ChatMessage(session_id=session.id, role="assistant", content=content))
    db.commit()

    # Bill the call. We don't have native token counts from the OpenAI-compatible
    # response by default, so estimate from message lengths. Replace with real
    # `usage` numbers when the provider returns them.
    input_chars = sum(len((m.get("content") or "")) for m in history)
    output_chars = len(content or "")
    ledger.record(
        user_id=str(user_id),
        input_tokens=estimate_tokens(" " * input_chars),
        output_tokens=estimate_tokens(" " * output_chars),
    )

    return {
        "session_id": session.id,
        "content": content,
        "transcript": transcript,
        "widget": runner.last_widget.model_dump() if runner.last_widget else None,
    }


async def build_widget_only(prompt: str) -> WidgetSpec:
    return await build_widget_from_prompt(prompt)


def _get_or_create_session(
    db: Session, user_id: uuid.UUID, session_id: uuid.UUID | None, first_message: str
) -> ChatSession:
    if session_id:
        existing = db.query(ChatSession).filter(
            ChatSession.id == session_id, ChatSession.user_id == user_id
        ).first()
        if existing:
            return existing
    title = (first_message[:60] + "…") if len(first_message) > 60 else first_message
    sess = ChatSession(user_id=user_id, title=title or "New chat")
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return sess


def _load_history(db: Session, session_id: uuid.UUID) -> list[dict[str, Any]]:
    msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    out: list[dict[str, Any]] = []
    for m in msgs:
        if m.role in ("user", "assistant"):
            out.append({"role": m.role, "content": m.content or ""})
    return out
