# agent_min.py
import os, re, glob
from typing import TypedDict, Literal, Any, List, Dict
from langgraph.graph import StateGraph, START, END
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, BaseMessage

# Reuse your llm + runners
from genomeer.utils.llm import get_llm, SourceType
from genomeer.utils.helper import run_python_code, run_r_code, run_bash_script, run_cli_command, run_with_timeout

# ---------- STATE ----------
class Step(TypedDict):
    title: str
    status: Literal["todo","done","blocked"]
    notes: str

class AgentState(TypedDict):
    messages: List[BaseMessage]
    next_step: Literal["planner","qa","orchestrator","input_guard","generator","ensure_env","executor","observer","end"]
    plan: List[Step]
    current_idx: int
    manifest: Dict[str, Any]     # arbitrary scratchpad (paths, params, outputs)
    pending_code: str | None
    last_result: str | None
    missing: List[str] | None
    env_name: str
    env_ready: bool

# ---------- PROMPTS ----------
GLOBAL_SYSTEM = """You are a compact, reliable meta-genomics assistant that works in a node-graph.
You have access to Python, R, and shell (bash/CLI). Do not roleplay tools; only produce code in GENERATOR.
"""

PLANNER_PROMPT = """You are the PLANNER.
Given the user's goal, produce a TODO checklist as bullet points with empty checkboxes, one step per line:
- [ ] step 1
- [ ] step 2
- [ ] step 3
Keep steps crisp and executable. Then add a final routing tag on its own line:
<next:QA> if a single-step Q&A is enough,
or <next:ORCHESTRATOR> if we must run tools/code.
Only return the checklist and the <next:...> tag. No extra commentary."""

QA_PROMPT = """You are QA.
- If `route_hint == "ask_for_missing"`, ask the user *only* for the missing items, concisely, as a short numbered list.
- If `route_hint == "finalize"`, summarize results clearly and answer the user’s original question.
"""

INPUT_GUARD_PROMPT = """You are INPUT_GUARD. For the CURRENT step, list what inputs are REQUIRED and which ones are PRESENT.
Return one of:
<MISSING>
- item 1
- item 2
</MISSING>
or
<OK/>
Use domain common sense. Be conservative: only mark OK if everything needed is available."""

GENERATOR_PROMPT = """You are GENERATOR. Produce code ONLY.
Rules:
- Output strictly in: <execute env='{ENV_NAME}'>...code...</execute>
- No text outside the tag. No explanations.
- Use Python by default. For R, start with `#!R`; for Bash/scripts, start with `#!BASH`; for single CLI, use `#!CLI`.
- Keep code minimal and actually runnable given the CURRENT step and MANIFEST information.
"""

OBSERVER_PROMPT = """You are OBSERVER. Summarize the execution result in 3-6 lines:
- What was run
- Key outputs / files / metrics
- Errors (if any) and next action
Return either:
<STATUS:done>short summary...</STATUS>
or
<STATUS:blocked>short error summary...</STATUS>
"""

# ---------- SIMPLE PARSERS ----------
RX_NEXT = re.compile(r"<next:(QA|ORCHESTRATOR)>", re.I)
RX_EXEC = re.compile(r"<execute[^>]*>(.*?)</execute>", re.S | re.I)
RX_ENV  = re.compile(r"<execute\s+env=['\"]?([\w\-\.]+)['\"]?>", re.I)
RX_MISSING = re.compile(r"<MISSING>(.*?)</MISSING>", re.S | re.I)
RX_OK = re.compile(r"<OK\s*/\s*>", re.I)
RX_STATUS = re.compile(r"<STATUS:(done|blocked)>(.*?)</STATUS>", re.S | re.I)

def parse_checklist_and_route(text: str):
    m = RX_NEXT.search(text or "")
    route = m.group(1).upper() if m else "ORCHESTRATOR"
    # steps = lines starting with "- [ ]" (todo only at first)
    steps = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("- [ ]"):
            title = line[5:].strip()
            if title:
                steps.append({"title": title, "status": "todo", "notes": ""})
    return steps, ("qa" if route=="QA" else "orchestrator")

def parse_execute(text: str):
    code = None
    env = "base"
    m = RX_EXEC.search(text or "")
    if m:
        code = m.group(1).strip()
        em = RX_ENV.search(text or "")
        if em:
            env = em.group(1)
    return env, code

def parse_missing_ok(text: str):
    if RX_OK.search(text or ""):
        return [], True
    mm = RX_MISSING.search(text or "")
    if not mm:
        # treat as missing if we couldn't parse
        return ["Could not parse INPUT_GUARD response."], False
    items = [x.strip("- ").strip() for x in mm.group(1).strip().splitlines() if x.strip()]
    return items, False

def parse_status(text: str):
    m = RX_STATUS.search(text or "")
    if not m:
        return "blocked", "Could not parse OBSERVER status."
    return m.group(1), m.group(2).strip()

# ---------- VALIDATORS (example: alignment, generic fallback) ----------
def detect_alignment_step(title: str) -> bool:
    t = title.lower()
    return any(k in t for k in ["align", "alignment", "bwa", "minimap", "map reads"])

def validate_alignment(manifest: Dict[str,Any]) -> List[str]:
    missing = []
    reads = manifest.get("reads", {})
    ref   = manifest.get("reference", {})
    if not reads.get("r1"): missing.append("Reads R1 (FASTQ or accession)")
    if reads.get("paired") and not reads.get("r2"): missing.append("Reads R2 (paired)")
    if not (ref.get("fasta") or ref.get("accession")): missing.append("Reference (FASTA or accession)")
    if not manifest.get("read_type"): missing.append("Read type (short/long)")
    return missing

def compute_missing_for_step(step: Step, manifest: Dict[str,Any]) -> List[str]:
    title = step["title"]
    if detect_alignment_step(title):
        return validate_alignment(manifest)
    # add more per-step validators here
    return []  # default: no special requirements

# ---------- NODES ----------
class MinimalAgent:
    def __init__(self, llm_name: str | None = None, source: SourceType | None = None, timeout_seconds: int = 600):
        self.llm = get_llm(llm_name, source=source)
        self.timeout_seconds = timeout_seconds
        self.system_msg = SystemMessage(content=GLOBAL_SYSTEM)
        self._wire_graph()

    # PLANNER
    def planner(self, state: AgentState) -> AgentState:
        user_prompt = state["messages"][0].content
        resp = self.llm.invoke([self.system_msg, HumanMessage(content=PLANNER_PROMPT), HumanMessage(content=user_prompt)])
        steps, route = parse_checklist_and_route(resp.content)
        state["plan"] = steps
        state["current_idx"] = 0
        state["messages"].append(AIMessage(content=resp.content))
        state["next_step"] = route
        return state

    # QA
    def qa(self, state: AgentState) -> AgentState:
        route_hint = state["manifest"].get("route_hint")
        payload = state["manifest"].get("qa_payload","")
        msgs = [self.system_msg, HumanMessage(content=QA_PROMPT)]
        if route_hint == "ask_for_missing":
            msgs.append(HumanMessage(content=f"Ask user for these missing items only:\n{payload}"))
        elif route_hint == "finalize":
            msgs.append(HumanMessage(content=f"Summarize and answer:\n{payload}"))
        else:
            msgs.append(HumanMessage(content=payload or "Answer clearly."))
        resp = self.llm.invoke(msgs)
        state["messages"].append(AIMessage(content=resp.content))
        # If we asked for inputs, we wait for user reply outside the graph runner.
        # For demo: end after finalize; otherwise stay orchestrated.
        if route_hint == "finalize":
            state["next_step"] = "end"
        else:
            state["next_step"] = "orchestrator"
        return state

    # ORCHESTRATOR
    def orchestrator(self, state: AgentState) -> AgentState:
        # find next todo
        idx = state["current_idx"]
        plan = state["plan"]
        while idx < len(plan) and plan[idx]["status"] != "todo":
            idx += 1
        state["current_idx"] = idx
        if idx >= len(plan):
            # All done → Planner (to render final checklist) → QA finalize
            state["manifest"]["route_hint"] = "finalize"
            state["manifest"]["qa_payload"] = "All steps completed. Provide a clean final answer."
            state["next_step"] = "planner"
            return state
        # otherwise go check inputs
        state["next_step"] = "input_guard"
        return state

    # INPUT_GUARD
    def input_guard(self, state: AgentState) -> AgentState:
        step = state["plan"][state["current_idx"]]
        # (A) deterministic validator (recommended)
        missing = compute_missing_for_step(step, state["manifest"])
        if missing:
            state["missing"] = missing
            state["manifest"]["route_hint"] = "ask_for_missing"
            state["manifest"]["qa_payload"] = "\n".join(f"- {m}" for m in missing)
            state["next_step"] = "qa"
            return state

        # (B) optional LLM guard (useful when you don’t have a deterministic rule)
        guard_text = f"CURRENT STEP:\n{step['title']}\n\nMANIFEST:\n{state['manifest']}"
        resp = self.llm.invoke([self.system_msg, HumanMessage(content=INPUT_GUARD_PROMPT), HumanMessage(content=guard_text)])
        items, ok = parse_missing_ok(resp.content)
        if not ok:
            state["missing"] = items
            state["manifest"]["route_hint"] = "ask_for_missing"
            state["manifest"]["qa_payload"] = "\n".join(f"- {m}" for m in items)
            state["next_step"] = "qa"
        else:
            state["next_step"] = "generator"
        return state

    # GENERATOR (code only)
    def generator(self, state: AgentState) -> AgentState:
        step = state["plan"][state["current_idx"]]
        env_name = state["env_name"]
        prompt = GENERATOR_PROMPT.replace("{ENV_NAME}", env_name)
        content = f"CURRENT STEP:\n{step['title']}\n\nMANIFEST:\n{state['manifest']}"
        resp = self.llm.invoke([self.system_msg, HumanMessage(content=prompt), HumanMessage(content=content)])
        env, code = parse_execute(resp.content)
        state["pending_code"] = code
        state["env_name"] = env or env_name
        state["messages"].append(AIMessage(content=resp.content))
        state["next_step"] = "ensure_env"
        return state

    # ENSURE_ENV (minimal / stub OK)
    def ensure_env(self, state: AgentState) -> AgentState:
        # MVP: mark ready; replace with your real env manager later
        state["env_ready"] = True
        state["next_step"] = "executor"
        return state

    # EXECUTOR
    def executor(self, state: AgentState) -> AgentState:
        code = (state.get("pending_code") or "").strip()
        if not code:
            state["last_result"] = "No code produced by GENERATOR."
            state["next_step"] = "observer"
            return state

        # Detect language from shebang
        timeout =  state["manifest"].get("timeout_seconds", 600)
        if code.startswith("#!R"):
            out = run_with_timeout(run_r_code, args=[code.replace("#!R","",1)], kwargs={"env_name": state["env_name"]}, timeout=timeout)
        elif code.startswith("#!BASH"):
            out = run_with_timeout(run_bash_script, args=[code.replace("#!BASH","",1)], kwargs={"env_name": state["env_name"]}, timeout=timeout)
        elif code.startswith("#!CLI"):
            cmd = code.replace("#!CLI","",1).strip().replace("\n"," ")
            out = run_with_timeout(run_cli_command, args=[cmd], kwargs={"env_name": state["env_name"]}, timeout=timeout)
        else:
            out = run_with_timeout(run_python_code, args=[code], kwargs={"env_name": state["env_name"]}, timeout=timeout)

        # bound size
        if out and len(out) > 12000:
            out = out[:12000] + "\n...<truncated>"
        state["last_result"] = out or ""
        state["next_step"] = "observer"
        return state

    # OBSERVER
    def observer(self, state: AgentState) -> AgentState:
        step = state["plan"][state["current_idx"]]
        payload = f"CURRENT STEP: {step['title']}\n\nRESULT:\n{state['last_result']}"
        resp = self.llm.invoke([self.system_msg, HumanMessage(content=OBSERVER_PROMPT), HumanMessage(content=payload)])
        status, summary = parse_status(resp.content)
        step["notes"] = summary
        step["status"] = "done" if status == "done" else "blocked"
        state["messages"].append(AIMessage(content=resp.content))
        # If blocked → go back to input_guard on same step; else advance to orchestrator
        if status == "blocked":
            state["next_step"] = "input_guard"
        else:
            state["current_idx"] += 1
            state["next_step"] = "orchestrator"
        return state

    # ---------- GRAPH WIRING ----------
    def _wire_graph(self):
        g = StateGraph(AgentState)
        g.add_node("planner", self.planner)
        g.add_node("qa", self.qa)
        g.add_node("orchestrator", self.orchestrator)
        g.add_node("input_guard", self.input_guard)
        g.add_node("generator", self.generator)
        g.add_node("ensure_env", self.ensure_env)
        g.add_node("executor", self.executor)
        g.add_node("observer", self.observer)

        g.add_edge(START, "planner")
        g.add_edge("planner", "qa")
        g.add_edge("planner", "orchestrator")
        g.add_edge("orchestrator", "input_guard")
        g.add_edge("input_guard", "qa")
        g.add_edge("input_guard", "generator")
        g.add_edge("generator", "ensure_env")
        g.add_edge("ensure_env", "executor")
        g.add_edge("executor", "observer")
        g.add_edge("observer", "orchestrator")
        g.add_edge("qa", END)

        self.app = g.compile()

    # ---------- RUN ----------
    def run(self, user_text: str):
        init: AgentState = {
            "messages": [HumanMessage(content=user_text)],
            "next_step": "planner",
            "plan": [],
            "current_idx": 0,
            "manifest": {"timeout_seconds": self.timeout_seconds},
            "pending_code": None,
            "last_result": None,
            "missing": None,
            "env_name": "base",
            "env_ready": False,
        }
        # simple driver: step until END
        s = init
        while True:
            node = s["next_step"]
            if node == "planner":      s = self.planner(s)
            elif node == "qa":         s = self.qa(s)
            elif node == "orchestrator": s = self.orchestrator(s)
            elif node == "input_guard": s = self.input_guard(s)
            elif node == "generator":  s = self.generator(s)
            elif node == "ensure_env": s = self.ensure_env(s)
            elif node == "executor":   s = self.executor(s)
            elif node == "observer":   s = self.observer(s)
            elif node == "end": break
            else: raise RuntimeError(f"unknown node {node}")
        return s
