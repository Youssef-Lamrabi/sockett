import httpx, orjson
from ..config import settings

def chat(messages, model=None) -> str:
    model = model or settings.model
    payload = {"model": model, "messages": messages, "stream": False}
    with httpx.Client(timeout=120) as c:
        r = c.post(f"{settings.ollama_host}/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()
    return data.get("message", {}).get("content", "")
