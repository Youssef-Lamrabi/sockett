from typing import Dict, Any, List
from ..tools.registry import get_tool

def execute_plan(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    results = []
    for idx, step in enumerate(plan.get("steps", []), start=1):
        tool_name = step.get("tool")
        inputs = step.get("inputs", {})
        tool = get_tool(tool_name)
        out = tool.run(**inputs)
        results.append({"step": idx, "tool": tool_name, "inputs": inputs, "output": out})
    return results
