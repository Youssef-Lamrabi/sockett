import asyncio, threading, json, os, re
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, field_validator
from pathlib import Path

from .db import get_db
from .models import User, ChatSession, Message, ProviderConfig, UserModel, MessageLog
from .config import system_default, bio_hint_default
from .auth import get_current_user

from genomeer.agent.v2 import BioAgent

router = APIRouter()
INFLIGHT: dict[tuple[int, int], threading.Event] = {}
AGENTS: dict[tuple[int,int], BioAgent] = {}
AGENTS_LOCK = threading.RLock()
UPLOAD_DIR = os.path.abspath("./uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# C-2: 5 GB max upload — FASTQ/metagenomics files can be large
MAX_UPLOAD_SIZE: int = int(os.getenv("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024 * 1024)))


def _mk_agent_for_user(db: Session, user: User, model_name: str, interaction_mode: str = "auto") -> BioAgent:
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
            # 3 - fallback: provider config → env → conf.json → hardcoded Ollama default
            _sys = system_default()
            source   = (cfg.source   if cfg else None) or os.getenv("GENOMEER_MODEL_SOURCE") or _sys.get("source") or "Ollama"
            base_url = (cfg.base_url if cfg else None) or os.getenv("OPENAI_COMPAT_BASE_URL") or _sys.get("base_url") or "http://localhost:11434/v1"
            api_key  = (cfg.api_key  if cfg else None) or os.getenv("OPENAI_COMPAT_API_KEY")  or _sys.get("api_key")

    # Optional secondary LLM for bio_hint node — activated only if conf.json
    # defines `bio_hint_model`. Falls back to the main provider's creds when
    # bio_hint_source/base_url/api_key are not specified. Construction errors
    # are silently downgraded to None so the agent still works.
    bio_hint_llm = None
    _bh = bio_hint_default()
    if _bh.get("model"):
        try:
            from genomeer.utils.llm import get_llm
            bio_hint_llm = get_llm(
                model=_bh["model"],
                source=(_bh.get("source") or source),
                base_url=(_bh.get("base_url") or base_url),
                api_key=(_bh.get("api_key") or api_key),
            )
        except Exception as _bh_err:
            print(f"[bio_hint] failed to construct secondary LLM, disabling: {_bh_err}")
            bio_hint_llm = None

    agent = BioAgent(
        path="./data",
        llm=model_name,
        source=source,
        use_tool_retriever=False,
        timeout_seconds=300,
        base_url=base_url,
        api_key=api_key,
        auto_start_artifacts=False,
        interaction_mode=interaction_mode,
        bio_hint_llm=bio_hint_llm,
    )
    return agent

def _derive_title_from_text(text: str, max_len: int =40) -> str:
    t = (text or "").strip()
    # strip code fences & collapse whitespace/newlines
    t = re.sub(r"```[\s\S]*?```", "", t)
    t = re.sub(r"[\r\n]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" \"'")
    if not t:
        return "New Chat"
    return (t[:max_len - 1] + "…") if len(t) > max_len else t

def get_or_create_agent(db, user, sess, effective_mode: str) -> BioAgent:
    key = (user.id, sess.id)
    agent = AGENTS.get(key)
    if agent:
        return agent
    agent = _mk_agent_for_user(db, user, sess.model, interaction_mode=effective_mode)
    AGENTS[key] = agent
    return agent

def evict_agent(user_id: int, session_id: int) -> bool:
    """Remove cached agent (and try to cleanly shutdown if supported)."""
    with AGENTS_LOCK:
        agent = AGENTS.pop((user_id, session_id), None)
    try:
        if agent and hasattr(agent, "shutdown"):
            agent.shutdown()
    except Exception:
        pass
    return agent is not None

def cancel_and_evict(user_id: int, session_id: int) -> bool:
    """Cancel in-flight work, then evict the cached agent."""
    ev = INFLIGHT.get((user_id, session_id))
    if ev:
        ev.set()
    return evict_agent(user_id, session_id)


class SessionCreateBody(BaseModel):
    title: Optional[str] = None
    model: Optional[str] = None
    interaction_mode: Optional[str] = "auto"  # 'auto' | 'feedback'
    
    @field_validator("interaction_mode")
    @classmethod
    def _v_mode(cls, v):
        v = (v or "auto").lower()
        if v not in {"auto","feedback"}:
            raise ValueError("interaction_mode must be 'auto' or 'feedback'")
        return v

@router.post("/sessions")
def create_session(body: SessionCreateBody, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # default model falls back to user's provider config
    cfg = db.query(ProviderConfig).filter(ProviderConfig.user_id == user.id).first()
    default_model = (cfg.default_model if cfg else "gpt-oss:20b")
    sess = ChatSession(
        user_id=user.id,
        title=body.title or "New Chat",
        model=body.model or default_model,
        interaction_mode=body.interaction_mode or "auto"
    )
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
    changed = (sess.model != body.model)
    sess.model = body.model
    db.commit()

    if changed:
        cancel_and_evict(user.id, sess.id)

    return {"id": sess.id, "model": sess.model}

class SessionModeUpdate(BaseModel):
    interaction_mode: str
    @field_validator("interaction_mode")
    @classmethod
    def _v_mode(cls, v):
        v = (v or "auto").lower()
        if v not in {"auto","feedback"}:
            raise ValueError("interaction_mode must be 'auto' or 'feedback'")
        return v

@router.post("/sessions/{session_id}/mode")
def update_session_mode(session_id: int, body: SessionModeUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sess = db.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.user_id == user.id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    changed = (sess.interaction_mode != body.interaction_mode)
    sess.interaction_mode = body.interaction_mode
    db.commit()

    if changed:
        cancel_and_evict(user.id, sess.id)

    return {
        "id": sess.id,
        "interaction_mode": sess.interaction_mode,
        "note": "Mode will apply on the next message."
    }

class AttachmentIn(BaseModel):
    path: str
    name: Optional[str] = None
    mime: Optional[str] = None
    size: Optional[int] = None

class ChatBody(BaseModel):
    message: str
    stream: Optional[bool] = True
    interaction_mode: Optional[str] = None
    attachments: Optional[List[AttachmentIn]] = None

@router.post("/sessions/{session_id}/messages")
async def chat(session_id: int, body: ChatBody, request: Request,
               db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sess = db.query(ChatSession).filter(ChatSession.id == session_id,
                                        ChatSession.user_id == user.id).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    # --- auto-title on FIRST user message (when title is still default) ---
    old_title = sess.title or ""
    new_title = None
    if not old_title or old_title.strip() == "New Chat":
        proposed = _derive_title_from_text(body.message)
        if proposed and proposed != old_title:
            sess.title = proposed
            db.commit()
            new_title = proposed
    # -----------------------------------------------------------------------

    # Save user message
    m_user = Message(session_id=sess.id, role="user", content=body.message)
    db.add(m_user); db.commit()

    # attachments hook (optional)
    attachments = body.attachments or []

    effective_mode = (body.interaction_mode or sess.interaction_mode or "auto").lower()
    agent = get_or_create_agent(db, user, sess, effective_mode)


    if body.stream:
        cancel_event = threading.Event()
        INFLIGHT[(user.id, sess.id)] = cancel_event

        assistant_parts: list[str] = []   # ← collect what the user actually sees
        saved_logs: list[dict] = []       # ← blocks for the right pane (tag/body)

        async def _streamer():
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[bytes] = asyncio.Queue()

            def _send(obj: Dict[str, Any]):
                # Thread-safe: the producer runs in a thread (run_in_executor), and
                # asyncio.Queue.put_nowait is NOT thread-safe. Use call_soon_threadsafe
                # to schedule the put on the event loop. Fixes silent stream drops.
                payload = (json.dumps(obj) + "\n").encode("utf-8")
                loop.call_soon_threadsafe(queue.put_nowait, payload)

            def _producer():
                try:
                    if new_title:
                        _send({"type": "meta", "session_id": sess.id, "session_title": new_title})

                    for evt in agent.go_stream(
                        body.message,
                        mode="prod",
                        attachments=attachments,
                        session_id=str(sess.id),
                        cancel_event=cancel_event
                    ):
                        if cancel_event.is_set():
                            break

                        # -------- capture assistant-visible content ----------
                        try:
                            typ = (evt or {}).get("type")
                            if typ == "message":
                                txt = (evt.get("text") or "")
                                if txt: assistant_parts.append(txt)
                            elif typ == "block":
                                tag = str(evt.get("tag") or "").upper()
                                if tag in {"SOLUTION","FINAL","ANSWER","SUMMARY","REVIEW"}:
                                    raw = evt.get("text") or ""
                                    # strip <TAG> ... </TAG>
                                    inner = re.sub(r"^<[^>]+>", "", raw)
                                    inner = re.sub(r"</[^>]+>$", "", inner).strip()
                                    if inner: assistant_parts.append(inner)
                                
                                # Save loggable blocks for history
                                # These are the ones your right panel renderer understands.
                                LOGGABLE = {"EXECUTE","OBSERVE","LOGS","THINK","STATUS","NEXT"}
                                if tag in LOGGABLE:
                                    raw = evt.get("text") or ""
                                    body_txt = raw
                                    if tag in {"EXECUTE","OBSERVE","LOGS","THINK","NEXT"}:
                                        # store inner content only (no <TAG> wrappers)
                                        body_txt = re.sub(r"^<[^>]+>", "", raw)
                                        body_txt = re.sub(r"</[^>]+>$", "", body_txt).strip()
                                    elif tag == "STATUS":
                                        # normalize to 'running|done|...' without angle brackets
                                        m = re.search(r"<\s*status\s*:\s*([^>]+)>", raw, flags=re.I)
                                        body_txt = (m.group(1) if m else raw).strip()
                                    saved_logs.append({"tag": tag, "body": body_txt})

                        except Exception:
                            pass
                        # -----------------------------------------------------

                        _send(evt)
                except Exception as e:
                    _send({"type": "error", "text": str(e)})
                finally:
                    _send({"type": "done"})
                    INFLIGHT.pop((user.id, sess.id), None)

            loop.run_in_executor(None, _producer)

            try:
                while True:
                    if await request.is_disconnected():
                        cancel_event.set()
                        break
                    chunk = await queue.get()
                    yield chunk

                    # stop when producer says "done"
                    try:
                        obj = json.loads(chunk.decode().strip() or "{}")
                        if obj.get("type") == "done":
                            break
                    except Exception:
                        pass
            except asyncio.CancelledError:
                cancel_event.set()
                raise
            finally:
                cancel_event.set()
                # ------------ persist assistant reply for history ------------
                final_text = "\n".join(p for p in assistant_parts if p).strip()
                if final_text:
                    # db.add(Message(session_id=sess.id, role="assistant", content=final_text))
                    # db.commit()
                    m = Message(session_id=sess.id, role="assistant", content=final_text)
                    db.add(m); db.commit(); db.refresh(m)
                    # persist logs in original order
                    for i, L in enumerate(saved_logs):
                        db.add(MessageLog(message_id=m.id, tag=L["tag"], body=L["body"], ord=i))
                    db.commit()
                # ------------------------------------------------------------

        return StreamingResponse(_streamer(), media_type="application/x-ndjson")

    # non-stream path (unchanged)
    log, final = agent.go(body.message, mode="prod", attachments=attachments, session_id=str(sess.id))
    m_assist = Message(session_id=sess.id, role="assistant", content=final or "")
    db.add(m_assist); db.commit()
    return {"message": final}


@router.post("/sessions/{session_id}/cancel")
def cancel_run(session_id: int, user: User = Depends(get_current_user)):
    ev = INFLIGHT.get((user.id, session_id))
    if ev:
        ev.set()
        return {"ok": True, "canceled": True}
    return {"ok": True, "canceled": False}

@router.post("/upload")
async def upload(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    # C-1: sanitize filename — strip any directory component (prevents path traversal)
    raw_name = file.filename or "upload"
    safe_name = os.path.basename(raw_name)
    if not safe_name:
        safe_name = "upload"

    dest = Path(UPLOAD_DIR) / safe_name

    # C-1: verify the resolved path stays inside UPLOAD_DIR
    dest_real   = os.path.realpath(dest)
    upload_real = os.path.realpath(UPLOAD_DIR)
    if not (dest_real == upload_real or dest_real.startswith(upload_real + os.sep)):
        raise HTTPException(status_code=400, detail="Invalid filename: path traversal detected.")

    # C-2: read file content and enforce size limit before writing to disk
    data = await file.read()
    if len(data) > MAX_UPLOAD_SIZE:
        limit_gb = MAX_UPLOAD_SIZE // (1024 ** 3)
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum allowed size is {limit_gb} GB."
        )

    dest.write_bytes(data)
    return {"path": str(dest.resolve())}


from fastapi import HTTPException

@router.get("/sessions/{session_id}")
def get_session(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sess = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == user.id)
        .first()
    )
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "id": sess.id,
        "title": sess.title,
        "model": sess.model,
        "interaction_mode": sess.interaction_mode,
        "created_at": sess.created_at.isoformat(),
    }

@router.get("/sessions/{session_id}/messages")
def get_messages(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sess = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == user.id)
        .first()
    )
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    msgs = (
        db.query(Message)
        .filter(Message.session_id == session_id)
        .order_by(Message.created_at.asc())
        .all()
    )
    # return [
    #     {
    #         "id": m.id,
    #         "role": m.role,
    #         "content": m.content,
    #         "created_at": m.created_at.isoformat(),
    #         # "attachments": [],  # add later if you persist them
    #     }
    #     for m in msgs
    # ]
    out = []
    for m in msgs:
        item = {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "created_at": m.created_at.isoformat(),
        }
        if m.role == "assistant":
            item["logs"] = [{"tag": L.tag, "body": L.body} for L in (m.logs or [])]
        out.append(item)
    return out
