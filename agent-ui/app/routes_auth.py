# app/routes_auth.py
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from .db import get_db
from .models import User
from .auth import get_password_hash, verify_password, create_access_token, get_current_user

ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7
router = APIRouter()

class RegisterBody(BaseModel):
    email: EmailStr
    name: str
    password: str

@router.post("/register")
def register(body: RegisterBody, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(email=body.email, name=body.name, password_hash=get_password_hash(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "email": user.email, "name": user.name}

@router.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(status_code=400, detail="Incorrect email or password")

    token = create_access_token({"sub": str(user.id)})

    # Return token AND set a SameSite=Lax HttpOnly cookie for server-rendered pages
    resp = JSONResponse({"access_token": token, "token_type": "bearer"})
    # secure=True if you serve over HTTPS
    resp.set_cookie(
        key="agent_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        secure=False
    )
    return resp

@router.post("/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("agent_token")
    return resp

@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return {"id": current_user.id, "email": current_user.email, "name": current_user.name}


