from typing import Any, Dict, List, Optional, Literal, TypedDict

Role = Literal["system", "user", "assistant", "tool"]

class Message(TypedDict, total=False):
    role: Role
    content: str
    tool_call_id: Optional[str]
    name: Optional[str]
    metadata: Optional[Dict[str, Any]]

class ToolSpec(TypedDict, total=False):
    name: str
    description: str
    inputs_schema: Dict[str, Any]
