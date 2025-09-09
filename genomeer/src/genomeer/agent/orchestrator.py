from typing import List, Dict, Any
from ..tools.registry import list_tools
from .planner import make_plan
from .executor import execute_plan
from .aq import check_quality
from .explainer import explain
from ..adapters.ollama_client import chat

def tool_specs():
    return [t.schema() for t in list_tools()]

def run_turn(messages: List[Dict[str, Any]], model: str) -> Dict[str, Any]:
    plan = make_plan(messages, tool_specs(), model=model)
    execs = execute_plan(plan)
    issues = check_quality(execs)
    explanation = explain(execs)

    # Derive final answer (LLM can fuse tool outputs)
    summary_prompt = messages + [
        {"role":"system","content":"Summarize tool results for the user:\n" + explanation}
    ]
    final_answer = chat(summary_prompt, model=model)

    return {
        "plan": plan,
        "executions": execs,
        "issues": issues,
        "answer": final_answer
    }
