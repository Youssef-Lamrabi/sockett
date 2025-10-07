from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from .db import get_db
from .models import User, ProviderConfig, UserModel
from .auth import get_current_user
from .config import system_default

allowed = {"OpenAI","AzureOpenAI","Anthropic","Ollama","Gemini","Bedrock","Groq","Custom"}
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
    allowed = {"OpenAI","AzureOpenAI","Anthropic","Ollama","Gemini","Bedrock","Groq","Custom"}
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
    return {"ok": True}


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
    return {
        "system_default": sys,
        "user_models": [
            {"id": m.id, "name": m.name, "source": m.source, "base_url": m.base_url}
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

@router.delete("/models/{mid}")
def delete_model(mid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    m = db.query(UserModel).filter(UserModel.id == mid, UserModel.user_id == user.id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(m); db.commit()
    return {"ok": True}