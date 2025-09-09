from typing import List, Dict, Any

def explain(executions: List[Dict[str, Any]]) -> str:
    lines = ["Execution summary:"]
    for e in executions:
        lines.append(f"- Step {e['step']} ({e['tool']}): {e['output']}")
    return "\n".join(lines)
