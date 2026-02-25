### Agent Copilot UI

- FastAPI backend with SQLite auth (JWT)
- Chat sessions with per-session model selection
- Streaming and non-streaming chat to your BioAgent
- File uploads; agent receives absolute paths
- Static UI: left logs, right chat

Run:

```bash
python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8911 --reload
```

Open: http://localhost:8911

Notes:
- Configure `OPENAI_COMPAT_BASE_URL` and `OPENAI_COMPAT_API_KEY` if needed.
- DB path: `./agent_copilot.sqlite3`. Uploads: `./uploads`.
