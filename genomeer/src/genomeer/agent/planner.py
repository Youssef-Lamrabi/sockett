from typing import List, Dict, Any
from ..adapters.ollama_client import chat

SYSTEM = "You are a planning module for a metagenomics assistant. Output a JSON plan with ordered steps and the tool names + inputs."

def make_plan(messages: List[Dict[str, Any]], tools_spec: List[Dict[str, Any]], model: str) -> Dict[str, Any]:
    prompt = [
        {"role":"system","content":SYSTEM},
        *messages,
        {"role":"system","content":f"Available tools: {tools_spec}"}
    ]
    text = chat(prompt, model=model)  # returns assistant text
    # Expect JSON in the assistant text; robustify later
    import json, re
    m = re.search(r"\{.*\}$", text.strip(), re.S)
    return json.loads(m.group(0)) if m else {"steps":[]}
