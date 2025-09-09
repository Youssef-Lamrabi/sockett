from typing import Dict, Any, List
from ..agent.orchestrator import run_turn
from ..config import settings

def handle_chat(payload: Dict[str, Any]) -> Dict[str, Any]:
    # Expected minimal payload: {"messages":[...], "model":"llama3.1:70b"}
    messages: List[Dict[str, Any]] = payload.get("messages", [])
    model = payload.get("model", settings.model)
    result = run_turn(messages, model=model)
    # Respond in an OpenWebUI-friendly shape (assistant message)
    return {
        "assistant_message": {
            "role": "assistant",
            "content": result["answer"],
            "metadata": {
                "plan": result["plan"],
                "executions": result["executions"],
                "issues": result["issues"]
            }
        }
    }
