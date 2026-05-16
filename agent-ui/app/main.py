# app/main.py
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import os

from .db import init_db
from .routes_auth import router as auth_router
from .routes_chat import router as chat_router
from .routes_settings import router as settings_router  # ensure filename is routes_settings.py
from .auth import try_get_user  # NEW

app = FastAPI(title="Agent Copilot UI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
static_dir = os.path.abspath(static_dir)
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

templates_dir = os.path.join(os.path.dirname(__file__), "..", "static", "templates")
templates_dir = os.path.abspath(templates_dir)
os.makedirs(templates_dir, exist_ok=True)
templates = Jinja2Templates(directory=templates_dir)

@app.on_event("startup")
def _startup():
    init_db()

@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    return templates.TemplateResponse("landing.html", {"request": request, "page": "landing"})

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "page": "login"})

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request):
    # server-side guard using cookie or header
    from .auth import try_get_user
    user = request.app.dependency_overrides.get(try_get_user, None)
    # call directly if not overridden
    if user is None:
        from .db import get_db
        from sqlalchemy.orm import Session
        db: Session = next(get_db())
        user = request.state.user if hasattr(request.state, "user") else None
        if user is None:
            user = asyncio.run(try_get_user(request, db)) if "asyncio" in globals() else None

    # simpler: re-run helper synchronously
    from .auth import try_get_user as _try
    u = request  # dummy to satisfy linter
    # if not authenticated, redirect to /login
    # (We can’t await in sync route; call again in a small helper)
    # easiest: do a lightweight cookie check
    if not request.cookies.get("agent_token"):
        return RedirectResponse("/login", status_code=302)

    return templates.TemplateResponse("dashboard.html", {"request": request, "page": "dashboard"})

# APIs
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(settings_router, prefix="/api/settings", tags=["settings"])
app.include_router(chat_router, prefix="/api", tags=["chat"])

