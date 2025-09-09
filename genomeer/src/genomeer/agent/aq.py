from typing import List, Dict, Any

def check_quality(executions: List[Dict[str, Any]]) -> List[str]:
    issues = []
    for e in executions:
        if e["tool"] == "gc_content" and e["output"]["length"] == 0:
            issues.append("Empty sequence provided to gc_content.")
    return issues
