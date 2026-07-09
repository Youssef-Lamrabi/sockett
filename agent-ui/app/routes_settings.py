from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from .db import get_db
from .models import User, ProviderConfig, UserModel, ChatSession
from .auth import get_current_user
from .config import system_default

allowed = {"OpenAI","AzureOpenAI","Anthropic","Ollama","Gemini","Bedrock","Groq","DeepSeek","Custom"}
router = APIRouter()

class ProviderBody(BaseModel):
    source: str
    base_url: str | None = None
    api_key: str | None = None
    default_model: str

@router.get("/provider")
def get_provider(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    cfg = db.query(ProviderConfig).filter(ProviderConfig.user_id == user.id).first()
    if not cfg:
        # lazy default on first read
        cfg = ProviderConfig(
            user_id=user.id,
            source="Ollama",
            base_url=None,
            api_key=None,
            default_model="gpt-oss:20b"
        )
        db.add(cfg); db.commit(); db.refresh(cfg)
    return {
        "source": cfg.source,
        "base_url": cfg.base_url,
        "api_key": "***" if cfg.api_key else None,  # don't leak secret back
        "default_model": cfg.default_model
    }

@router.post("/provider")
def save_provider(body: ProviderBody, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    allowed = {"OpenAI","AzureOpenAI","Anthropic","Ollama","Gemini","Bedrock","Groq","DeepSeek","Custom"}
    if body.source not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported source '{body.source}'")
    cfg = db.query(ProviderConfig).filter(ProviderConfig.user_id == user.id).first()
    if not cfg:
        cfg = ProviderConfig(user_id=user.id)
        db.add(cfg)
    cfg.source = body.source
    cfg.base_url = body.base_url
    # Only replace api_key if provided (so we can keep masked on client)
    if body.api_key is not None and body.api_key.strip() != "***":
        cfg.api_key = body.api_key.strip() or None
    cfg.default_model = body.default_model
    cfg.updated_at = datetime.utcnow()
    db.commit()

    # FIX: invalidate all cached agents for this user so the next request
    # picks up the newly saved provider config instead of serving a stale
    # agent that was built with the old credentials/source/base_url.
    try:
        from .routes_chat import cancel_and_evict
        sessions = db.query(ChatSession).filter(ChatSession.user_id == user.id).all()
        for sess in sessions:
            cancel_and_evict(user.id, sess.id)
    except Exception:
        pass  # cache invalidation is best-effort; never fail the save

    return {"ok": True}


class TestBody(BaseModel):
    source: str
    model: str
    base_url: str | None = None
    api_key: str | None = None

@router.post("/test")
def test_provider(body: TestBody, user: User = Depends(get_current_user)):
    """Build the LLM from the given credentials and do a tiny 1-token ping so the user can
    verify a model works BEFORE saving/using it. Never raises to the client — returns
    {ok, message}. Bounded by a 25s timeout so a wrong/unreachable host can't hang the request."""
    if body.source not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported source '{body.source}'")
    if not (body.model or "").strip():
        return {"ok": False, "message": "Model name is required."}
    import concurrent.futures as _cf
    try:
        from genomeer.utils.llm import get_llm
        llm = get_llm(
            model=body.model.strip(),
            source=body.source,
            base_url=(body.base_url or None),
            api_key=(body.api_key or None),
            temperature=0,
        )
        with _cf.ThreadPoolExecutor(max_workers=1) as ex:
            ex.submit(lambda: llm.invoke("ping")).result(timeout=25)
        return {"ok": True, "message": f"Connection OK — {body.source} / {body.model} responded."}
    except _cf.TimeoutError:
        return {"ok": False, "message": "Timed out after 25s — is the URL/host reachable?"}
    except Exception as e:
        return {"ok": False, "message": (str(e) or type(e).__name__)[:300]}


class ModelBody(BaseModel):
    name: str
    source: str
    base_url: str | None = None
    api_key: str | None = None

@router.get("/models")
def list_models(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sys = system_default()  # {model, source, base_url, api_key}
    items = (
        db.query(UserModel)
        .filter(UserModel.user_id == user.id)
        .order_by(UserModel.created_at.asc())
        .all()
    )
    cfg = db.query(ProviderConfig).filter(ProviderConfig.user_id == user.id).first()
    default_model = (cfg.default_model if (cfg and cfg.default_model) else None) \
        or ((sys or {}).get("model"))
    # SECURITY: never send the raw API key to the browser. Expose only whether a key
    # is configured (has_key) so the UI can show a lock indicator.
    sys_safe = None
    if sys and sys.get("model"):
        sys_safe = {
            "model": sys.get("model"),
            "source": sys.get("source"),
            "base_url": sys.get("base_url"),
            "has_key": bool(sys.get("api_key")),
        }
    return {
        "system_default": sys_safe,
        "default_model": default_model,
        "user_models": [
            {"id": m.id, "name": m.name, "source": m.source,
             "base_url": m.base_url, "has_key": bool(m.api_key)}
            for m in items
        ],
    }

@router.post("/models")
def add_model(body: ModelBody, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if body.source not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported source '{body.source}'")
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Model name required")
    # Optional: prevent duplicate names per user
    exists = db.query(UserModel).filter(UserModel.user_id==user.id, UserModel.name==body.name.strip()).first()
    if exists:
        raise HTTPException(status_code=400, detail="Model already exists")
    m = UserModel(
        user_id=user.id,
        name=body.name.strip(),
        source=body.source,
        base_url=(body.base_url or None),
        api_key=(body.api_key or None),
    )
    db.add(m); db.commit(); db.refresh(m)
    return {"id": m.id}

class DefaultBody(BaseModel):
    name: str

@router.post("/models/default")
def set_default_model(body: DefaultBody, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Set the user's default model. Must be the system default or one of the user's own
    models — so the default can never point at something that can't be built."""
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Model name required")
    sys = system_default() or {}
    valid = {sys.get("model")} if sys.get("model") else set()
    valid |= {m.name for m in db.query(UserModel).filter(UserModel.user_id == user.id).all()}
    if name not in valid:
        raise HTTPException(status_code=400, detail="Unknown model — add it first")
    cfg = db.query(ProviderConfig).filter(ProviderConfig.user_id == user.id).first()
    if not cfg:
        cfg = ProviderConfig(user_id=user.id)
        db.add(cfg)
    cfg.default_model = name
    cfg.updated_at = datetime.utcnow()
    db.commit()
    # Evict cached agents so the next request uses the new default.
    try:
        from .routes_chat import cancel_and_evict
        for sess in db.query(ChatSession).filter(ChatSession.user_id == user.id).all():
            cancel_and_evict(user.id, sess.id)
    except Exception:
        pass
    return {"ok": True, "default_model": name}


@router.post("/models/{mid}/test")
def test_saved_model(mid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Ping a SAVED model using its stored credentials (the key stays server-side and is
    never returned). Same bounded, never-raising contract as /test."""
    m = db.query(UserModel).filter(UserModel.id == mid, UserModel.user_id == user.id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Not found")
    import concurrent.futures as _cf
    try:
        from genomeer.utils.llm import get_llm
        llm = get_llm(
            model=m.name,
            source=m.source,
            base_url=(m.base_url or None),
            api_key=(m.api_key or None),
            temperature=0,
        )
        with _cf.ThreadPoolExecutor(max_workers=1) as ex:
            ex.submit(lambda: llm.invoke("ping")).result(timeout=25)
        return {"ok": True, "message": f"Connection OK — {m.source} / {m.name} responded."}
    except _cf.TimeoutError:
        return {"ok": False, "message": "Timed out after 25s — is the URL/host reachable?"}
    except Exception as e:
        return {"ok": False, "message": (str(e) or type(e).__name__)[:300]}


@router.delete("/models/{mid}")
def delete_model(mid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    m = db.query(UserModel).filter(UserModel.id == mid, UserModel.user_id == user.id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(m); db.commit()

    # FIX: evict cached agents whose session model name matched the deleted UserModel,
    # so the next request doesn't reuse an agent built with now-deleted credentials.
    try:
        from .routes_chat import cancel_and_evict
        sessions = db.query(ChatSession).filter(ChatSession.user_id == user.id).all()
        for sess in sessions:
            cancel_and_evict(user.id, sess.id)
    except Exception:
        pass

    return {"ok": True}