import os
import json
import asyncio
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from pathlib import Path

from .db import get_db
from .models import User, ChatSession, Message, ProviderConfig, UserModel
from .config import system_default
from .auth import get_current_user

from genomeer.agent.v2 import BioAgent

router = APIRouter()

UPLOAD_DIR = os.path.abspath("./uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

class SessionCreateBody(BaseModel):
    title: Optional[str] = None
    model: Optional[str] = None

@router.post("/sessions")
def create_session(body: SessionCreateBody, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # default model falls back to user's provider config
    cfg = db.query(ProviderConfig).filter(ProviderConfig.user_id == user.id).first()
    default_model = (cfg.default_model if cfg else "gpt-oss:20b")
    sess = ChatSession(user_id=user.id, title=body.title or "New Chat", model=body.model or default_model)
    db.add(sess); db.commit(); db.refresh(sess)
    return {"id": sess.id, "title": sess.title, "model": sess.model}

@router.get("/sessions")
def list_sessions(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sessions = (
        db.query(ChatSession)
        .filter(ChatSession.user_id == user.id)
        .order_by(ChatSession.created_at.desc())
        .all()
    )
    return [{"id": s.id, "title": s.title, "model": s.model, "created_at": s.created_at.isoformat()} for s in sessions]

class SessionModelUpdate(BaseModel):
    model: str

@router.post("/sessions/{session_id}/model")
def update_session_model(session_id: int, body: SessionModelUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sess = db.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.user_id == user.id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    sess.model = body.model
    db.commit()
    return {"id": sess.id, "model": sess.model}

class ChatBody(BaseModel):
    message: str
    stream: Optional[bool] = True

def _mk_agent_for_user(db: Session, user: User, model_name: str) -> BioAgent:
    # fallback provider config
    cfg = db.query(ProviderConfig).filter(ProviderConfig.user_id == user.id).first()

    # 1 - user-specific model (exact name match)
    um = db.query(UserModel).filter(
        UserModel.user_id == user.id,
        UserModel.name == model_name
    ).first()

    if um:
        source = um.source
        base_url = um.base_url or (cfg.base_url if cfg else None)
        api_key  = um.api_key  or (cfg.api_key  if cfg else None)
    else:
        # 2 -system default (conf.json)
        sys = system_default()
        if sys.get("model") == model_name:
            source = sys.get("source") or (cfg.source if cfg else "Ollama")
            base_url = sys.get("base_url") or (cfg.base_url if cfg else None)
            api_key  = sys.get("api_key")  or (cfg.api_key  if cfg else None)
        else:
            # 3 - fallback provider config only
            source = cfg.source if cfg else os.getenv("GENOMEER_MODEL_SOURCE") or "Ollama"
            base_url = cfg.base_url or os.getenv("OPENAI_COMPAT_BASE_URL")
            api_key  = cfg.api_key  or os.getenv("OPENAI_COMPAT_API_KEY")

    agent = BioAgent(
        path="./data",
        llm=model_name,
        source=source,
        use_tool_retriever=True,
        timeout_seconds=600,
        base_url=base_url,
        api_key=api_key,
        interaction_mode="auto",
    )
    return agent

@router.post("/sessions/{session_id}/messages")
async def chat(session_id: int, body: ChatBody, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sess = db.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.user_id == user.id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    # Persist user message
    m_user = Message(session_id=sess.id, role="user", content=body.message)
    db.add(m_user); db.commit()

    # attachments (client can supply override header, optional)
    try:
        meta = await request.json()
    except Exception:
        meta = {}
    attachments = meta.get("__attachments_override__") or []

    agent = _mk_agent_for_user(db, user, sess.model)

    if body.stream:
        async def _streamer():
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[bytes] = asyncio.Queue()

            def _send(obj: Dict[str, Any]):
                queue.put_nowait((json.dumps(obj) + "\n").encode("utf-8"))

            def _producer():
                try:
                    for evt in agent.go_stream(body.message, mode="prod", attachments=attachments, session_id=str(sess.id)):
                        etype = evt.get("type")
                        text = evt.get("text", "")
                        _send({"type": etype, "text": text})
                finally:
                    _send({"type": "done"})

            loop.run_in_executor(None, _producer)

            while True:
                chunk = await queue.get()
                yield chunk
                try:
                    obj = json.loads(chunk.decode().strip() or "{}")
                    if obj.get("type") == "done":
                        break
                except Exception:
                    pass

        return StreamingResponse(_streamer(), media_type="text/event-stream")
    else:
        log, final = agent.go(body.message, mode="prod", attachments=attachments, session_id=str(sess.id))
        m_assist = Message(session_id=sess.id, role="assistant", content=final or "")
        db.add(m_assist); db.commit()
        return {"message": final}

@router.post("/upload")
async def upload(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    dest = Path(UPLOAD_DIR) / file.filename
    data = await file.read()
    dest.write_bytes(data)
    return {"path": str(dest.resolve())}

