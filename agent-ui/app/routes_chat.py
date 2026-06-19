import asyncio, threading, json, os, re, mimetypes, tempfile
from datetime import datetime
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Request, Query
from fastapi.responses import StreamingResponse, FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, field_validator
from pathlib import Path

from .db import get_db
from .models import User, ChatSession, Message, ProviderConfig, UserModel, MessageLog
from .config import system_default, bio_hint_default
from .auth import get_current_user, get_current_user_cookie_or_header

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
        # Pass the user's display name so the QA node can address them by
        # name in greetings ("Hi <first_name>!"). Falls back to email-prefix.
        user_name=(user.name or (user.email or "").split("@")[0] or None),
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
    # BioAgent expects list[str] of absolute paths. body.attachments is
    # List[AttachmentIn] (Pydantic). Convert here so _stage_attachments
    # can shutil.copy2() them. Without this conversion, os.path.basename()
    # raises TypeError on the Pydantic object and the staging silently fails.
    _attachment_objs = body.attachments or []
    attachments = [a.path for a in _attachment_objs if getattr(a, "path", None)]

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
                                
                                # Save blocks for history replay. Two groups:
                                #  - right-pane logs (EXECUTE/OBSERVE/LOGS/THINK/STATUS): store inner text.
                                #  - left-pane CHAT CARDS (RUNNING/DESCRIPTION/MISSING/NEXT): store the
                                #    RAW tagged text so the frontend rebuilds the SAME cards on refresh.
                                #    Fixes the "UI changes / Step+Missing cards disappear after reload" bug,
                                #    and NEXT→Unknown (NEXT was being stripped of its <next:..> wrapper so
                                #    the chip could no longer parse the target node).
                                LOGGABLE = {"EXECUTE","OBSERVE","LOGS","THINK","STATUS","NEXT",
                                            "RUNNING","DESCRIPTION","MISSING"}
                                if tag in LOGGABLE:
                                    raw = evt.get("text") or ""
                                    if tag in {"EXECUTE","OBSERVE","LOGS","THINK"}:
                                        # store inner content only (no <TAG> wrappers)
                                        body_txt = re.sub(r"^<[^>]+>", "", raw)
                                        body_txt = re.sub(r"</[^>]+>$", "", body_txt).strip()
                                    elif tag == "STATUS":
                                        # normalize to 'running|done|...' without angle brackets
                                        m = re.search(r"<\s*status\s*:\s*([^>]+)>", raw, flags=re.I)
                                        body_txt = (m.group(1) if m else raw).strip()
                                    else:
                                        # NEXT / RUNNING / DESCRIPTION / MISSING → keep RAW tagged text
                                        # so the frontend can replay them through the live card renderer.
                                        body_txt = raw
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


# ─── Rename a session (PATCH for an idempotent partial update) ──────────────
class SessionTitleUpdate(BaseModel):
    title: str

    @field_validator("title")
    @classmethod
    def _v_title(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("title must be non-empty")
        # Soft cap to avoid runaway titles
        return v[:200]


@router.patch("/sessions/{session_id}")
def rename_session(
    session_id: int,
    body: SessionTitleUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sess = db.query(ChatSession).filter(
        ChatSession.id == session_id, ChatSession.user_id == user.id
    ).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    sess.title = body.title
    db.commit()
    return {"id": sess.id, "title": sess.title}


# ─── Delete a session (cascade: messages + logs via SQLA relationship) ──────
@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sess = db.query(ChatSession).filter(
        ChatSession.id == session_id, ChatSession.user_id == user.id
    ).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    deleted_title = sess.title or "Chat"
    # Cancel any in-flight run and evict the cached agent for this session
    try: cancel_and_evict(user.id, session_id)
    except Exception: pass
    db.delete(sess)
    db.commit()
    return {"ok": True, "id": session_id, "title": deleted_title}

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


# ============================================================================
# Workspace files browser — lists uploads + generated outputs of the session's
# run directory so the UI can show a file explorer panel and preview content.
# ============================================================================
# CRITICAL: must match the default used by genomeer.agent.v2.utils.tempdir
# (`tempfile.gettempdir()` -> "/tmp" on Linux). A different default here would
# have the backend look in the WRONG directory and the workspace panel would
# always show "No files yet" while the agent actually wrote files elsewhere.
_BIOAGENT_TMP_DIR = os.environ.get("BIOAGENT_TMP_DIR", tempfile.gettempdir())
_WORKSPACE_SKIP_DIRS = {"__pycache__", ".cache", ".ipynb_checkpoints", ".mamba", ".micromamba"}
_WORKSPACE_PREVIEW_MAX_BYTES = 5 * 1024 * 1024  # 5 MB hard cap for served previews


def _session_run_dir(session_id: int) -> Path:
    """Return the agent's run workdir path for a given session id.

    The agent calls `run_workdir(prefix="run", session_id=str(sess.id))` which
    produces `{BIOAGENT_TMP_DIR}/run-{session_id}` (see tempdir.run_workdir).
    """
    return Path(_BIOAGENT_TMP_DIR) / f"run-{session_id}"


def _is_workspace_skipped(name: str) -> bool:
    """Hide dot-files and known caches from the workspace listing."""
    if not name:
        return True
    if name.startswith("."):
        return True
    if name in _WORKSPACE_SKIP_DIRS:
        return True
    return False


def _walk_workspace_files(root: Path, exclude_subdir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """List files under root (recursive), excluding caches and `exclude_subdir`.

    Returned dict shape: {name, rel_path, size, mtime (iso)}. Sorted mtime desc.
    """
    out: List[Dict[str, Any]] = []
    if not root.is_dir():
        return out
    root_real = root.resolve()
    ex_real = exclude_subdir.resolve() if exclude_subdir else None
    for r, dirs, files in os.walk(root_real):
        # Skip caches and any in-place excluded subdir
        r_path = Path(r)
        if ex_real is not None:
            try:
                r_path.relative_to(ex_real)
                # we're inside the excluded subdir → skip entirely
                continue
            except ValueError:
                pass
        dirs[:] = [d for d in dirs if not _is_workspace_skipped(d)]
        for fname in files:
            if _is_workspace_skipped(fname):
                continue
            fp = r_path / fname
            try:
                st = fp.stat()
            except OSError:
                continue
            try:
                rel = fp.relative_to(root_real)
            except ValueError:
                continue
            out.append({
                "name": fname,
                "rel_path": str(rel).replace("\\", "/"),
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                "_mtime_raw": st.st_mtime,
            })
    out.sort(key=lambda f: f["_mtime_raw"], reverse=True)
    for f in out:
        f.pop("_mtime_raw", None)
    return out


def _resolve_session_or_404(db: Session, user: User, session_id: int) -> ChatSession:
    sess = db.query(ChatSession).filter(
        ChatSession.id == session_id, ChatSession.user_id == user.id
    ).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    return sess


@router.get("/sessions/{session_id}/files")
def list_session_files(
    session_id: int,
    show: str = Query(
        "success",
        description=(
            "Filter for `generated` files: 'success' = only files produced by "
            "completed (status=done) steps; 'all' = include files from "
            "currently-running or blocked steps too. Default 'success'. "
            "Uploads are always returned regardless."
        ),
        regex="^(success|all)$",
    ),
    db: Session = Depends(get_db),
    # Accept Bearer header OR HttpOnly cookie — needed for native browser
    # navigations like <a href download> which do NOT send Authorization.
    user: User = Depends(get_current_user_cookie_or_header),
):
    """List uploaded + generated files for the session's run workspace.

    Response:
        {
          "run_dir":  "/tmp/bioagent/run-12",
          "uploads":  [ {name, rel_path, size, mtime, step_status?}, ... ],
          "generated":[ {name, rel_path, size, mtime, step_status?}, ... ],
          "hidden_count": <int>   # generated files filtered out by `show=success`
        }
    Empty arrays if the run hasn't started yet.

    Per-file step ownership comes from .genomeer_file_status.json (written by
    the agent's observer). If that side-channel file is missing or unreadable,
    `step_status` is absent and NO filtering is applied (legacy behavior).
    """
    _resolve_session_or_404(db, user, session_id)
    run_dir = _session_run_dir(session_id)
    if not run_dir.is_dir():
        return {"run_dir": str(run_dir), "uploads": [], "generated": [], "hidden_count": 0}
    uploads_dir = run_dir / "uploads"
    uploads = _walk_workspace_files(uploads_dir) if uploads_dir.is_dir() else []
    # Rewrite uploads' rel_path to be relative to RUN_DIR (so click path is consistent)
    for u in uploads:
        u["rel_path"] = f"uploads/{u['rel_path']}"
    generated = _walk_workspace_files(run_dir, exclude_subdir=uploads_dir)

    # ── Workspace status side-channel ──────────────────────────────────────
    # The agent writes .genomeer_file_status.json after every observer pass.
    # We use it to (a) annotate each file with its producing step + status
    # and (b) hide files from blocked/running steps when show=success.
    # Missing/corrupt file → legacy behavior (no filter, no annotations).
    status_map: Dict[str, Dict[str, Any]] = {}
    try:
        status_path = run_dir / ".genomeer_file_status.json"
        if status_path.is_file():
            import json as _json
            with open(status_path, "r", encoding="utf-8") as _fh:
                _doc = _json.load(_fh)
            _files = _doc.get("files") if isinstance(_doc, dict) else None
            if isinstance(_files, dict):
                status_map = {
                    str(k): (v if isinstance(v, dict) else {})
                    for k, v in _files.items()
                }
    except Exception:
        status_map = {}

    def _annotate(items: List[Dict[str, Any]]) -> None:
        for f in items:
            meta = status_map.get(f.get("rel_path") or "")
            if isinstance(meta, dict) and meta:
                if "step_idx" in meta:
                    f["step_idx"] = meta["step_idx"]
                if "step_title" in meta:
                    f["step_title"] = meta["step_title"]
                if "step_status" in meta:
                    f["step_status"] = meta["step_status"]

    _annotate(uploads)
    _annotate(generated)

    hidden_count = 0
    if show == "success" and status_map:
        # Hide files whose producing step is "running" or "blocked".
        # Files with no metadata (= existed before any observation OR status
        # file is older than current state) stay visible to avoid losing
        # legitimate outputs on a partial-state race.
        kept = []
        for f in generated:
            st = f.get("step_status")
            if st in ("running", "blocked"):
                hidden_count += 1
                continue
            kept.append(f)
        generated = kept

    return {
        "run_dir": str(run_dir),
        "uploads": uploads,
        "generated": generated,
        "hidden_count": hidden_count,
    }


@router.get("/sessions/{session_id}/files/raw")
def get_session_file_raw(
    session_id: int,
    path: str = Query(..., description="rel_path returned by /files (must stay inside the run dir)"),
    db: Session = Depends(get_db),
    # Cookie-or-Bearer auth: <a href download> and <img src> trigger native
    # navigations/requests that cannot attach an Authorization header.
    user: User = Depends(get_current_user_cookie_or_header),
):
    """Serve a single file from the session's run workspace for preview/download.

    Path-traversal safe: the resolved absolute path must be a descendant of the
    session run dir; any escape attempt returns 400.
    Files larger than _WORKSPACE_PREVIEW_MAX_BYTES are still served as
    `FileResponse` (download), but the UI caps the preview client-side.
    """
    _resolve_session_or_404(db, user, session_id)
    run_dir = _session_run_dir(session_id).resolve()
    if not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="Run workspace not found")
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise HTTPException(status_code=400, detail="Invalid path")
    try:
        requested = (run_dir / path).resolve()
    except (OSError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid path")
    try:
        requested.relative_to(run_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path traversal denied")
    if not requested.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    mime, _ = mimetypes.guess_type(str(requested))
    return FileResponse(
        str(requested),
        media_type=mime or "application/octet-stream",
        filename=requested.name,
    )
