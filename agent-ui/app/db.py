import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session

DB_PATH = os.getenv("AGENT_COPILOT_DB", os.path.abspath("./agent_copilot.sqlite3"))
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def init_db():
    from . import models  # ensure models imported
    Base.metadata.create_all(bind=engine)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

