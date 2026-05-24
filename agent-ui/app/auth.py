# app/auth.py
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from passlib.context import CryptContext
from .db import get_db
from .models import User
from sqlalchemy.orm import Session

_DEFAULT_SECRET = "dev-secret-change-me"
SECRET_KEY = os.getenv("AGENT_COPILOT_SECRET", _DEFAULT_SECRET)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7


def validate_secret_key() -> None:
    """Raise RuntimeError at startup if the JWT secret is missing or still set to the insecure default."""
    if not SECRET_KEY or SECRET_KEY == _DEFAULT_SECRET:
        raise RuntimeError(
            "\n\n[SECURITY ERROR] JWT secret key is not configured or is set to the insecure default.\n"
            "Set the AGENT_COPILOT_SECRET environment variable to a strong random value before starting.\n"
            "Example (Linux/macOS):\n"
            "  export AGENT_COPILOT_SECRET=$(python -c \"import secrets; print(secrets.token_hex(32))\")\n"
            "Example (Windows):\n"
            "  $env:AGENT_COPILOT_SECRET = python -c \"import secrets; print(secrets.token_hex(32))\"\n"
        )

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

# Existing header-only dependency (kept for API routes using Authorization header)
async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise credentials_exception
    return user

# NEW: Try header Bearer first; if missing, try the HttpOnly cookie "agent_token"
def _decode_user_from_token(token: str, db: Session) -> Optional[User]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            return None
        return db.query(User).filter(User.id == user_id).first()
    except JWTError:
        return None

async def get_current_user_cookie_or_header(request: Request, db: Session = Depends(get_db)) -> User:
    token = None
    auth = request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1]
    else:
        token = request.cookies.get("agent_token")

    user = _decode_user_from_token(token, db) if token else None
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user

# NEW: soft version for HTML endpoints that should redirect instead of 401 JSON
async def try_get_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    token = None
    auth = request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1]
    else:
        token = request.cookies.get("agent_token")
    if not token:
        return None
    return _decode_user_from_token(token, db)
