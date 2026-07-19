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
    _ensure_columns()


# create_all() only creates TABLES that don't exist yet — it never alters an
# already-existing table, so a column added to a model after first deploy
# (e.g. MessageLog.note) silently never appears on disk and every insert/
# select touching it then raises "OperationalError: no such column". This
# runs on every startup (cheap: one PRAGMA + conditional ALTER per entry,
# idempotent) so existing databases get patched up without a manual step.
# Add a line here whenever a new column is added to an existing table.
_MISSING_COLUMNS = [
    ("message_logs", "note", "TEXT"),
]


def _ensure_columns():
    with engine.begin() as conn:
        for table, column, coltype in _MISSING_COLUMNS:
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

