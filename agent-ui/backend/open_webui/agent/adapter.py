import asyncio, json, time
from typing import AsyncIterator, Dict, Any, Optional, Union
from fastapi.responses import StreamingResponse
from genomeer.agent import BioAgent


def _iso_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

# ---------------------------------------------------------------------
# Bleuprint
# ---------------------------------------------------------------------
class AgentProviderBase:
    """
    Base wrapper around BioAgent. Subclasses implement how to format
    run() and run_stream() outputs.
    """

    def __init__(
        self,
        path: str = "./data",
        llm: str = "gpt-oss:20b",
        source: str = "Custom",
        use_tool_retriever: bool = True,
        timeout_seconds: int = 600,
        base_url: str = "http://10.52.88.30:11434/v1",
        api_key: Optional[str] = None,
    ):
        self.agent = BioAgent(
            path,
            llm,
            source,
            use_tool_retriever,
            timeout_seconds,
            base_url,
            api_key,
        )
        self.model_name = llm

    @staticmethod
    def _extract_prompt(payload: Union[str, bytes, Dict[str, Any]]) -> str:
        """Get the user prompt from OpenAI-style payloads or simple dicts."""
        if isinstance(payload, (str, bytes)):
            try:
                payload = json.loads(payload)
            except Exception:
                return payload.decode() if isinstance(payload, bytes) else str(payload)

        msgs = payload.get("messages")
        if isinstance(msgs, list) and msgs:
            for m in reversed(msgs):
                if m.get("role") == "user" and "content" in m:
                    return m["content"]
            return msgs[-1].get("content", "")

        return payload.get("prompt") or payload.get("input") or ""

    # Methods subclasses must implement
    async def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    async def run_stream(self, payload: Dict[str, Any]) -> StreamingResponse:
        raise NotImplementedError


# ---------------------------------------------------------------------
# Ollama Adapter
# ---------------------------------------------------------------------
class OllamaBioAgent(AgentProviderBase):
    """
    BioAgent provider that speaks Ollama-style JSON streaming.
    """

    async def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        prompt = self._extract_prompt(payload)

        def _blocking_call():
            return self.agent.go(prompt)

        _log, final = await asyncio.get_running_loop().run_in_executor(None, _blocking_call)

        return {
            "model": payload.get("model", self.model_name),
            "created_at": _iso_utc(),
            "message": {"role": "assistant", "content": final},
            "done": True,
        }

    async def run_stream(self, payload: Dict[str, Any]) -> StreamingResponse:
        prompt = self._extract_prompt(payload)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes] = asyncio.Queue()
        model_name = payload.get("model", self.model_name)

        def _producer():
            try:
                for step in self.agent.go_stream(prompt):
                    delta_text = step.get("output", "")
                    chunk = {
                        "model": model_name,
                        "created_at": _iso_utc(),
                        "message": {"role": "assistant", "content": delta_text},
                        "done": False,
                    }
                    queue.put_nowait((json.dumps(chunk) + "\n").encode("utf-8"))
            finally:
                end = {
                    "model": model_name,
                    "created_at": _iso_utc(),
                    "done": True,
                }
                queue.put_nowait((json.dumps(end) + "\n").encode("utf-8"))

        loop.run_in_executor(None, _producer)

        async def _agen() -> AsyncIterator[bytes]:
            while True:
                chunk = await queue.get()
                yield chunk
                try:
                    obj = json.loads(chunk.decode().strip() or "{}")
                    if obj.get("done") is True:
                        break
                except Exception:
                    pass

        return StreamingResponse(_agen(), media_type="text/event-stream")
