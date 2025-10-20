import os
from dotenv import load_dotenv
from typing import List, Dict

import httpx
import logging


load_dotenv()
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_ENABLED = os.getenv("OLLAMA_ENABLED", "true").lower() in {"1", "true", "yes"}

logger = logging.getLogger(__name__)


def build_qg_prompt(content: str) -> str:
    return (
        "You are an expert in metagenomics. Given the following document chunk, "
        "generate 3 diverse high-quality question-answer pairs grounded in the text. "
        "Each answer must be directly supported by the chunk. Return as JSON lines with keys 'question' and 'answer'.\n\n"
        f"CHUNK:\n{content[:4000]}\n\n"
        "Output 3 lines, each a compact JSON object."
    )


def call_ollama(prompt: str) -> str:
    url = f"{OLLAMA_BASE_URL}/api/generate"
    payload = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}
    with httpx.Client(timeout=60) as client:
        r = client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        if "response" not in data:
            raise RuntimeError(f"Ollama invalid response keys: {list(data.keys())}")
        return data.get("response", "")


def parse_jsonl_lines(text: str) -> List[Dict]:
    import json

    items: List[Dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except Exception:
            # try naive split
            if line.startswith("{") and line.endswith("}") and '"question"' in line and '"answer"' in line:
                try:
                    items.append(json.loads(line))
                except Exception:
                    continue
            else:
                continue
    return items


def generate_qas_for_chunk(content: str) -> List[Dict[str, str]]:
    prompt = build_qg_prompt(content)
    if not OLLAMA_ENABLED:
        # Fallback stub generation (ensures pipeline continues in dev)
        return [
            {"question": "What is this chunk about?", "answer": content[:180] + ("..." if len(content) > 180 else "")},
        ]
    try:
        raw = call_ollama(prompt)
        items = parse_jsonl_lines(raw)
        results: List[Dict[str, str]] = []
        for it in items:
            q = str(it.get("question", "")).strip()
            a = str(it.get("answer", "")).strip()
            if q and a:
                results.append({"question": q, "answer": a})
        if not results:
            # As a last resort, create a single QA from the chunk
            results = [{"question": "Summarize the key point.", "answer": content[:200]}]
        return results[:3]
    except Exception as e:
        logger.exception("Ollama generation failed: %s", e)
        # Fallback stub to avoid 0 QAs silently
        return [
            {"question": "What does this text discuss?", "answer": content[:200]},
        ]


def check_ollama_health() -> dict:
    if not OLLAMA_ENABLED:
        return {"enabled": False}
    try:
        with httpx.Client(timeout=5) as client:
            r = client.get(f"{OLLAMA_BASE_URL}/api/tags")
            r.raise_for_status()
            tags = r.json().get("models", [])
            return {"enabled": True, "reachable": True, "models": [m.get("name") for m in tags]}
    except Exception as e:
        return {"enabled": True, "reachable": False, "error": str(e)}


