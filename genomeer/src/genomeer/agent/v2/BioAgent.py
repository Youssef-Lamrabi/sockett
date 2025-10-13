# -----------------------------------------------
# LIBRARY
# -----------------------------------------------
from pathlib import Path
import glob, inspect, os, re, threading, types, traceback
from collections.abc import Generator
from typing import Any, List, Dict
from typing_extensions import TypedDict, Literal, Annotated
import shutil
from uuid import uuid4

from dotenv import load_dotenv, find_dotenv
from langgraph.graph.message import add_messages
from langgraph.graph import END, START, StateGraph
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage 
from langgraph.checkpoint.memory import MemorySaver

from genomeer.config import settings
from genomeer.tools.software.resources import data_lake_dict, library_content_dict, runtime_envs_dicts
from genomeer.utils.llm import SourceType, get_llm
from genomeer.utils.helper import (
    pretty_print,
    run_r_code,
    run_bash_script,
    run_cli_command,
    run_python_code,
    run_with_timeout,
    function_to_api_schema,
    read_module2api,
    textify_api_dict,
)
from genomeer.model.retriever import ToolRetriever
from genomeer.tools.registry import ToolRegistry
from genomeer.utils.stream.shared import REGISTRY
from genomeer.agent.v2.utils import instructions
from genomeer.agent.v2.utils.state_graph import StateGraphHelper
from genomeer.model.feedback import FeedbackParser

# -----------------------------------------------
# UTILS
# -----------------------------------------------
dotenv_path = find_dotenv()
if dotenv_path:
    load_dotenv(dotenv_path, override=False)
    print(f"Loaded environment variables from {dotenv_path}")

from genomeer.agent.v2.utils.tempdir import run_workdir

class Step(TypedDict):
    title: str
    status: Literal["todo","done","blocked"]
    notes: str
    
class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages] # List[BaseMessage]
    next_step: Literal[
        "qa",
        "planner",
        "orchestrator",
        "input_guard",
        "generator",
        "ensure_env",
        "executor",
        "observer",
        "diagnostics",
        "finalizer",
        "end"
    ]
    plan: List[Step]
    current_idx: int
    manifest: Dict[str, Any]
    pending_code: str | None
    last_prompt: str | None
    last_result: str | None
    missing: List[str] | None
    env_name: str
    env_ready: bool
    run_id: str
    run_temp_dir: str
    retry_counts: Dict[int, int]
    diagnostic_mode: bool
    diagnostic_code: str | None
    diagnostic_observation: str | None
    
    
# -----------------------------------------------
# CORE AGENT CLASS
# -----------------------------------------------
class BioAgent:
    def __init__(
        self,
        path: str | None = None,
        llm: str | None = None,
        source: SourceType | None = None,
        use_tool_retriever: bool | None = None,
        timeout_seconds: int | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        expected_data_lake_files: list | None = None,
        auto_start_artifacts: bool = False,
        artifacts_host: str = "127.0.0.1",
        artifacts_port: int = 8910,
        artifacts_prefix: str = "/api/v1/artifacts",
        interaction_mode: str = "auto"
    ):
        """
        Agent initalization
        Args:
            path: Path to the data
            llm: LLM to use for the agent
            source (str): Source provider: "OpenAI", "AzureOpenAI", "Anthropic", "Ollama", "Gemini", "Bedrock", or "Custom"
            use_tool_retriever: If True, use a tool retriever
            timeout_seconds: Timeout for code execution in seconds
            base_url: Base URL for custom model serving (e.g., "http://localhost:8000/v1")
            api_key: API key for the custom LLM
        """
        # Use settings values for unspecified parameters
        if path is None:
            path = settings.path
        if llm is None:
            llm = settings.llm
        if source is None:
            source = settings.source
        if use_tool_retriever is None:
            use_tool_retriever = settings.use_tool_retriever
        if timeout_seconds is None:
            timeout_seconds = settings.timeout_seconds
        if base_url is None:
            base_url = settings.base_url
        if api_key is None:
            api_key = settings.api_key if settings.api_key else "EMPTY"

        # display configuration in a nice, readable format
        print("\n" + "=" * 50)
        print("BioAgent_v1 CONFIGURATION")
        print("=" * 50)

        # get the actual LLM values that will be used by the agent
        agent_llm = llm if llm is not None else settings.llm
        agent_source = source if source is not None else settings.source

        # show default config (database LLM)
        print("DEFAULT CONFIG :")
        config_dict = settings.to_dict()
        for key, value in config_dict.items():
            if value is not None:
                print(f"  {key.replace('_', ' ').title()}: {value}")

        # show agent-specific LLM if different from default
        if agent_llm != settings.llm or agent_source != settings.source:
            print("\n🤖 AGENT LLM (Constructor Override):")
            print(f"  LLM Model: {agent_llm}")
            if agent_source is not None:
                print(f"  Source: {agent_source}")
            if base_url is not None:
                print(f"  Base URL: {base_url}")
            if api_key is not None and api_key != "EMPTY":
                print(f"  API Key: {'*' * 8 + api_key[-4:] if len(api_key) > 8 else '***'}")
        print("=" * 50 + "\n")


        # [helper] to import tools-mapper, llm
        self.path = os.path.join(path, "bioagent_data")
        self.module2api = read_module2api()
        self.llm = get_llm(
            llm,
            stop_sequences=["</execute>", "</solution>"],
            source=source,
            base_url=base_url,
            api_key=api_key,
            config=settings,
        )
        
        self.use_tool_retriever = use_tool_retriever
        if self.use_tool_retriever:
            self.tool_registry = ToolRegistry(self.module2api)
            self.retriever = ToolRetriever()

        # per-env streaming installers
        self._install_iters = {}
        self.log_registry = REGISTRY
        self._install_threads = {}
        
        # Interaction mode
        self.interaction_mode = interaction_mode

        # Add timeout parameter
        self.timeout_seconds = timeout_seconds
        self.configure()
        
        # [DEV-ONLY] logs
        # self._set_debug_log("/home/biolab-office-1/DATALAB/2025/Genomeer/genomeer/src/genomeer/agent/v2/agent_debug.log")
        self._set_debug_log("./agent_debug.log")
        
        # CONSTANTS
        self.MAX_STEP_RETRIES = 3          # retries before diagnostics
        self.MAX_DIAG_ROUNDS_PER_STEP = 2  # how many times we allow re-entering diagnostics for the same step
        
        # Artifact server
        self.artifacts_base_url = os.getenv("PUBLIC_ARTIFACTS_URL", "http://localhost:8910/api/v1/artifacts")
        if auto_start_artifacts:
            self._start_artifacts_server_in_bg(host=artifacts_host, port=artifacts_port, prefix=artifacts_prefix)


    # LOGS UTILS [DEV-ONLY]
    def _set_debug_log(self, path: str | None = None):
        """Call once to set a log file. If None, uses ./bioagent_debug.log"""
        self.debug_log_path = path or os.path.abspath("./bioagent_debug.log")
        os.makedirs(os.path.dirname(self.debug_log_path), exist_ok=True)
        with open(self.debug_log_path, "w", encoding="utf-8") as f:
            f.write("\n===== NEW SESSION =====\n")

    def _log(self, title: str, body: str = "", node: str | None = None, type: str = 'file'):
        """Append a structured block to the debug log."""
        line = ""
        if title == "ENTER NODE":
            line += "\n" + (">"*60)
        line += f"\n[{node or '-'}] {title}\n{body}\n" + ("-"*60) + "\n"
        if type == 'file':
            with open(getattr(self, "debug_log_path", os.path.abspath("./bioagent_debug.log")), "a", encoding="utf-8") as f:
                f.write(line)
        elif type == 'stdout':
            print(line)

    def _fmt_msgs(self, msgs):
        """Pretty format a LangChain message list for logging."""
        parts = []
        for m in msgs:
            role = getattr(m, "type", m.__class__.__name__)
            content = getattr(m, "content", str(m))
            parts.append(f"--- {role.upper()} ---\n{content}")
        return "\n".join(parts)

    
    # SYSTEM SETUP
    def _llm_invoke(self, node: str, purpose: str, msgs, verbose=True):
        """
        Central LLM call: logs the full prompt and the raw LLM text response.
        Use this instead of self.llm.invoke(...) everywhere.
        """
        if verbose: prompt_txt = self._fmt_msgs(msgs)
        if verbose: self._log(f"LLM REQUEST ({purpose})", prompt_txt, node=node)
        resp = self.llm.invoke(msgs)
        if verbose: self._log(f"LLM RESPONSE ({purpose})", getattr(resp, "content", str(resp)), node=node)
        return resp
    
    def _generate_system_prompt(
        self,
        tool_desc,
        data_lake_content,
        library_content_list,
        self_critic=False,
        is_retrieval=False,
        custom_tools=None,
        custom_data=None,
        custom_software=None,
    ):
        """Generate the system prompt based on the provided resources.
        Args:
            tool_desc: Dictionary of tool descriptions
            data_lake_content: List of data lake items
            library_content_list: List of libraries
            self_critic: Whether to include self-critic instructions
            is_retrieval: Whether this is for retrieval (True) or initial configuration (False)
            custom_tools: List of custom tools to highlight
            custom_data: List of custom data items to highlight
            custom_software: List of custom software items to highlight
        Returns:
            The generated system prompt
        """

        def format_item_with_description(name, description):
            """Format an item with its description in a readable way."""
            # Handle None or empty descriptions
            if not description:
                description = f"Data lake item: {name}"

            # Check if the item is already formatted (contains a colon)
            if isinstance(name, str) and ": " in name:
                return name

            # Wrap long descriptions to make them more readable
            max_line_length = 80
            if len(description) > max_line_length:
                wrapped_desc = []
                words = description.split()
                current_line = ""

                for word in words:
                    if len(current_line) + len(word) + 1 <= max_line_length:
                        if current_line:
                            current_line += " " + word
                        else:
                            current_line = word
                    else:
                        wrapped_desc.append(current_line)
                        current_line = word

                if current_line:
                    wrapped_desc.append(current_line)

                # Join with newlines and proper indentation
                formatted_desc = f"{name}:\n  " + "\n  ".join(wrapped_desc)
                return formatted_desc
            else:
                return f"{name}: {description}"

        # separate custom and default resources
        default_data_lake_content = []
        default_library_content_list = []

        # filter out custom items from default lists
        custom_data_names = set()
        custom_software_names = set()

        if custom_data:
            custom_data_names = {item.get("name") if isinstance(item, dict) else item for item in custom_data}
        if custom_software:
            custom_software_names = {item.get("name") if isinstance(item, dict) else item for item in custom_software}

        # separate default data lake items
        for item in data_lake_content:
            if isinstance(item, dict):
                name = item.get("name", "")
                if name not in custom_data_names:
                    default_data_lake_content.append(item)
            elif item not in custom_data_names:
                default_data_lake_content.append(item)

        # separate default library items
        for lib in library_content_list:
            if isinstance(lib, dict):
                name = lib.get("name", "")
                if name not in custom_software_names:
                    default_library_content_list.append(lib)
            elif lib not in custom_software_names:
                default_library_content_list.append(lib)

        # Format the default data lake content
        if isinstance(default_data_lake_content, list) and all(
            isinstance(item, str) for item in default_data_lake_content
        ):
            # simple list of strings - check if they already have descriptions
            data_lake_formatted = []
            for item in default_data_lake_content:
                # check if the item already has a description (contains a colon)
                if ": " in item:
                    data_lake_formatted.append(item)
                else:
                    description = self.data_lake_dict.get(item, f"Data lake item: {item}")
                    data_lake_formatted.append(format_item_with_description(item, description))
        else:
            # list with descriptions
            data_lake_formatted = []
            for item in default_data_lake_content:
                if isinstance(item, dict):
                    name = item.get("name", "")
                    description = self.data_lake_dict.get(name, f"Data lake item: {name}")
                    data_lake_formatted.append(format_item_with_description(name, description))
                # check if the item already has a description (contains a colon)
                elif isinstance(item, str) and ": " in item:
                    data_lake_formatted.append(item)
                else:
                    description = self.data_lake_dict.get(item, f"Data lake item: {item}")
                    data_lake_formatted.append(format_item_with_description(item, description))

        # format the default library content
        if isinstance(default_library_content_list, list) and all(
            isinstance(item, str) for item in default_library_content_list
        ):
            if (
                len(default_library_content_list) > 0
                and isinstance(default_library_content_list[0], str)
                and "," not in default_library_content_list[0]
            ):
                # simple list of strings
                libraries_formatted = []
                for lib in default_library_content_list:
                    description = self.library_content_dict.get(lib, f"Software library: {lib}")
                    libraries_formatted.append(format_item_with_description(lib, description))
            else:
                # already formatted string
                libraries_formatted = default_library_content_list
        else:
            # list with descriptions
            libraries_formatted = []
            for lib in default_library_content_list:
                if isinstance(lib, dict):
                    name = lib.get("name", "")
                    description = self.library_content_dict.get(name, f"Software library: {name}")
                    libraries_formatted.append(format_item_with_description(name, description))
                else:
                    description = self.library_content_dict.get(lib, f"Software library: {lib}")
                    libraries_formatted.append(format_item_with_description(lib, description))

        # format custom resources with highlighting
        custom_tools_formatted = []
        if custom_tools:
            for tool in custom_tools:
                if isinstance(tool, dict):
                    name = tool.get("name", "Unknown")
                    desc = tool.get("description", "")
                    module = tool.get("module", "custom_tools")
                    custom_tools_formatted.append(f"🔧 {name} (from {module}): {desc}")
                else:
                    custom_tools_formatted.append(f"🔧 {str(tool)}")

        custom_data_formatted = []
        if custom_data:
            for item in custom_data:
                if isinstance(item, dict):
                    name = item.get("name", "Unknown")
                    desc = item.get("description", "")
                    custom_data_formatted.append(f"📊 {format_item_with_description(name, desc)}")
                else:
                    desc = self.data_lake_dict.get(item, f"Custom data: {item}")
                    custom_data_formatted.append(f"📊 {format_item_with_description(item, desc)}")

        custom_software_formatted = []
        if custom_software:
            for item in custom_software:
                if isinstance(item, dict):
                    name = item.get("name", "Unknown")
                    desc = item.get("description", "")
                    custom_software_formatted.append(f"⚙️ {format_item_with_description(name, desc)}")
                else:
                    desc = self.library_content_dict.get(item, f"Custom software: {item}")
                    custom_software_formatted.append(f"⚙️ {format_item_with_description(item, desc)}")

        # Base prompt
        base_prompt = instructions.GLOBAL_SYSTEM       
        base_prompt = base_prompt.format(
            SELF_CRITIC_INSTRUCTION= '---\n'+instructions.SELF_CRITIC_INSTRUCTION.strip()+'\n---' if self_critic else ""
        )

        # Add custom resources section first (highlighted)
        has_custom_resources = any([custom_tools_formatted, custom_data_formatted, custom_software_formatted])
        custom_resources = ""
        if has_custom_resources:
            custom_resources = instructions.UTILS_CUSTOM_RESOURCES.format(
                custom_tools="CUSTOM TOOLS (USE THESE FIRST):" + "\n".join(custom_tools_formatted) if custom_tools_formatted else ""
            )
            custom_resources = custom_resources.format(
                custom_data="CUSTOM DATA (PRIORITIZE THESE DATASETS):" + "\n".join(custom_data_formatted) if custom_data_formatted else ""
            )
            custom_resources = custom_resources.format(
                custom_software="CUSTOM SOFTWARE (USE THESE LIBRARIES):" + "\n".join(custom_software_formatted) if custom_software_formatted else ""
            )

        # Add environment resources
        if is_retrieval:
            function_intro = "Based on your query, I've identified the following most relevant functions that you can use in your code:"
            data_lake_intro = "Based on your query, I've identified the following most relevant datasets:"
            library_intro = (
                "Based on your query, I've identified the following most relevant libraries that you can use:"
            )
            import_instruction = "IMPORTANT: When using any function, you MUST first import it from its module. For example:\nfrom [module_name] import [function_name]"
        else:
            function_intro = "In your code, you will need to import the function location using the following dictionary of functions:"
            data_lake_intro = "You can write code to understand the data, process and utilize it for the task. Here is the list of datasets:"
            library_intro = "The environment supports a list of libraries that can be directly used. Do not forget the import statement:"
            import_instruction = ""

        env_resources = instructions.UTILS_ENV_RESOURCES.format(**{
            "function_intro":function_intro,
            "tool_desc": textify_api_dict(tool_desc) if isinstance(tool_desc, dict) else tool_desc,
            "import_instruction": import_instruction,
            "data_lake_path": self.path + "/data_lake",
            "data_lake_intro": data_lake_intro,
            "data_lake_content": "\n".join(data_lake_formatted),
            "library_intro": library_intro,
            "library_content_formatted": "\n".join(libraries_formatted),
        })
        sys_prompt = base_prompt + custom_resources + env_resources
        return sys_prompt

    def configure(self, self_critic=False, test_time_scale_round=0):
        """Configure the agent with the initial system prompt and workflow.
        Args:
            self_critic: Whether to enable self-critic mode
            test_time_scale_round: Number of rounds for test time scaling
        """
        self.self_critic = self_critic
        data_lake_path = self.path + "/data_lake"
        data_lake_content = glob.glob(data_lake_path + "/*")
        data_lake_items = [x.split("/")[-1] for x in data_lake_content]
        
        self.data_lake_dict = data_lake_dict
        self.library_content_dict = library_content_dict
        tool_desc = {i: [x for x in j] for i, j in self.module2api.items()}

        # Prepare data lake items with descriptions
        data_lake_with_desc = []
        for item in data_lake_items:
            description = self.data_lake_dict.get(item, f"Data lake item: {item}")
            data_lake_with_desc.append({"name": item, "description": description})

        # Add custom data items if they exist
        if hasattr(self, "_custom_data") and self._custom_data:
            for name, info in self._custom_data.items():
                data_lake_with_desc.append({"name": name, "description": info["description"]})

        # Prepare library content list including custom software
        library_content_list = list(self.library_content_dict.keys())
        if hasattr(self, "_custom_software") and self._custom_software:
            for name in self._custom_software:
                if name not in library_content_list:
                    library_content_list.append(name)

        # Generate the system prompt for initial configuration (is_retrieval=False)
        # Prepare custom resources for highlighting
        custom_tools = []
        if hasattr(self, "_custom_tools") and self._custom_tools:
            for name, info in self._custom_tools.items():
                custom_tools.append(
                    {
                        "name": name,
                        "description": info["description"],
                        "module": info["module"],
                    }
                )

        custom_data = []
        if hasattr(self, "_custom_data") and self._custom_data:
            for name, info in self._custom_data.items():
                custom_data.append({"name": name, "description": info["description"]})

        custom_software = []
        if hasattr(self, "_custom_software") and self._custom_software:
            for name, info in self._custom_software.items():
                custom_software.append({"name": name, "description": info["description"]})

        self.system_prompt = self._generate_system_prompt(
            tool_desc=tool_desc,
            data_lake_content=data_lake_with_desc,
            library_content_list=library_content_list,
            self_critic=self_critic,
            is_retrieval=False,
            custom_tools=custom_tools if custom_tools else None,
            custom_data=custom_data if custom_data else None,
            custom_software=custom_software if custom_software else None,
        )
        
        
        # Define the nodes(functions)
        # -------------------------------------------------------------------------------
        def _planner(self, state: AgentState) -> AgentState:
            node = "planner"
            self._log("ENTER NODE", body=f"state keys: {list(state.keys())}", node=node)

            # ------ RESUME FAST-PATH ------
            manifest = state.get("manifest") or {}
            if manifest.get("route_hint") == "ask_for_missing":
                # user likely provided the missing info in the latest message.
                # we don’t re-plan; we jump back into the current step’s guard.
                self._log("RESUME", body="Pending missing inputs -> jump to input_guard", node=node)
                return {
                    # "next_step": "input_guard",
                    "next_step": "orchestrator",
                    "messages": [AIMessage(content="<observe>Resuming with your new inputs…</observe>")],
                }
            # -----------------------------
                
            user_prompt = state["messages"][-1].content
            msgs = [
                self.system_prompt,
                HumanMessage(content=instructions.PLANNER_PROMPT.format(temp_run_dir=state.get("run_temp_dir", ""))),
            ]
            
            # ------ Interactive mode ------
            if manifest.get("route_hint") == "await_user":
                user_text = ""
                for m in reversed(state["messages"]):
                    if getattr(m, "type", "") == "human":
                        user_text = (m.content or "").strip()
                        break
                lower = user_text.lower()
                # ------
                feedbackParser = FeedbackParser()
                fr = feedbackParser.parse(lower, llm=self.llm)
                # ------
                # approve = bool(re.match(r"^(y|yes|ok|okay|go|run|proceed|continue|looks good)\b", lower))
                # ------

                pause_kind  = manifest.get("pause_kind")
                resume_to   = manifest.get("resume_to", "orchestrator")
                resume_idx  = manifest.get("resume_step_idx", state.get("current_idx", 0))

                new_manifest = dict(manifest)
                # clear pause metadata
                for k in ("route_hint","qa_payload","resume_to","pause_kind"):
                    new_manifest.pop(k, None)

                # APPROVED -> jump where we intended
                if fr.approved:
                    self._log("RESUME", body=f"Approved. Jumping to '{resume_to}'.", node=node)
                    return {
                        "next_step": resume_to,
                        "manifest": new_manifest,
                        "current_idx": resume_idx,
                        "messages": [AIMessage(content="<observe>Resuming after your approval…</observe>")],
                    }

                # CORRECTION -> inject feedback & retry correct node
                feedback = user_text or "(no text)"
                if pause_kind == "after_planner":
                    msgs.append(HumanMessage(content=instructions.USER_FEEDBACK_PROMPT.format(
                        feedback=feedback
                    )))
                    # Re-plan with user feedback this run
                    self._log("RESUME", body="User provided plan corrections. Re-planning now.", node=node)

                elif pause_kind in ("after_generator", "after_observer"):
                    # Retry current step codegen using repair flow
                    new_manifest["repair_feedback"] = f"USER_FEEDBACK:\n{feedback}"
                    new_manifest["repair_step_idx"] = resume_idx
                    self._log("RESUME", body="User provided code/result feedback. Regenerating code.", node=node)
                    return {
                        "next_step": "generator",
                        "manifest": new_manifest,
                        "current_idx": resume_idx,
                        "messages": [AIMessage(content="<observe>Regenerating code with your feedback…</observe>")],
                    }
            
            
            msgs.append(HumanMessage(content=user_prompt))
            resp = self._llm_invoke(node, "plan_route", msgs)
            steps, route = StateGraphHelper.parse_checklist_and_route(resp.content)
            updates = {
                "plan": steps,
                "current_idx": 0,
                "next_step": route,
                "messages": [AIMessage(content=resp.content)],
                "last_prompt": user_prompt,
            }
            if manifest.get("route_hint") == "await_user":
                updates["manifest"] = new_manifest
            self._log("EXIT NODE", body=f"route={route}\nsteps={steps}", node=node)
            
            if route == "qa" or not steps:
                self._log("HITL: skip planner pause for QA", body=f"route={route}, steps={len(steps)}", node=node)
                return updates

            # ------ feedback replay mode check ------
            pause = self._maybe_pause(
                state,
                resume_to=route or "orchestrator",
                pause_kind="after_planner",
                prompt_text=(
                    "PLAN READY ✅\n\n"
                    "Proposed steps:\n"
                    + "\n".join(f"- {i+1}. {s['title']}" for i, s in enumerate(steps))
                ),
            )
            if pause:
                return {**updates, **pause}
            # --------------------------------------
            
            return updates

        def _qa(self, state: AgentState) -> AgentState:
            node = "qa"
            next_step = "end"
            self._log("ENTER NODE", body=f"route_hint={state['manifest'].get('route_hint')}", node=node)
            history = self._history_snippet(state["messages"])
            
            route_hint = state["manifest"].get("route_hint")
            payload = state["manifest"].get("qa_payload","")
            last_prompt = state["last_prompt"]
            msgs = [
                self.system_prompt, 
                HumanMessage(content=instructions.QA_PROMPT.format(
                    history=history
                ))
            ]
            if route_hint == "ask_for_missing":
                msgs.append(HumanMessage(content=f"Ask user for these missing items only:\n{payload}"))
                next_step = "end"
            elif route_hint == "finalize":
                msgs.append(HumanMessage(content=f"Summarize and answer:\n{payload}"))
                next_step = "end"
            elif route_hint == "await_user":
                msgs.append(HumanMessage(content="""Prompt the user to review the previous step’s output and either approve it or request corrections before proceeding. Do not repeat the output (already sent to user) —just ask the question.""")) 
            else:
                msgs.append(HumanMessage(content=payload or f"Please be generous and Answer clearly to the user's question or request: '{last_prompt}'"))
                next_step = "end" #orchestrator
            
            resp = self._llm_invoke(node, "qa", msgs)
            updates = {
                "next_step": next_step,
                "messages": [AIMessage(content=resp.content)],
            }
            self._log("EXIT NODE", body=f"next_step={state['next_step']}", node=node)
            return updates

        def _orchestrator(self, state: AgentState) -> AgentState:
            node = "orchestrator"
            self._log("ENTER NODE", body=f"current_idx={state.get('current_idx')}\nplan_len={len(state.get('plan', []))}", node=node)

            idx = state["current_idx"]
            plan = state["plan"]
            while idx < len(plan) and plan[idx]["status"] != "todo":
                idx += 1
            state["current_idx"] = idx
            
            if idx >= len(plan):
                # all steps are done -> hand off to FINALIZER
                # initially this was QA's responsibility, but we'll ease that up for this
                # new_manifest = {
                #     **state["manifest"],
                #     "route_hint": "finalize",
                #     "qa_payload": "All steps completed. Provide a clean final answer.",
                # }
                self._log("EXIT NODE", body=f"all_done=True -> next_step=finalizer", node=node)
                return {
                    "current_idx": idx,
                    "next_step": "finalizer",
                    "messages": [AIMessage(content="<observe>All steps complete. Finalizing…</observe>")],
                }
            
            # otherwise go check inputs
            self._log("EXIT NODE", body=f"all_done=False\ncurrent_idx={idx}\nnext_step=input_guard", node=node)
            return {
                "current_idx": idx,
                "next_step": "input_guard",
                "messages": [AIMessage(content=f"<running step={idx+1}/>\n<description>\n{plan[idx]['title']}\n</description>\n")],
            }
        
        def _input_guard(self, state: AgentState) -> AgentState:
            node = "input_guard"
            step = state["plan"][state["current_idx"]]
            current_step_title = step["title"].strip()
            user_goal = state.get("last_prompt") or (state["messages"][0].content if state.get("messages") else "")
            manifest = dict(state.get("manifest") or {})

            # current run storage home lsdir
            temp_dir = state.get("run_temp_dir", "")
            files = self._list_ctx_files(temp_dir)
            files_str = "\n".join(f"- {f['name']} ({f['ext']}, {f['size_bytes']} bytes)" for f in files) or "<none>"

            # step-scoped retrieval (tools/data/libs) before we call the validator
            # so the SYSTEM prompt only advertises the most relevant resources.
            if self.use_tool_retriever:
                step_query = f"{user_goal}\nCURRENT_STEP: {current_step_title}"
                try:
                    selected_resources_names = self._prepare_resources_for_retrieval(step_query)
                    if selected_resources_names:
                        self.update_system_prompt_with_selected_resources(selected_resources_names)
                        self._log("STEP-SCOPED RETRIEVAL", body=str(selected_resources_names), node=node)
                except Exception as e:
                    self._log("RETRIEVAL ERROR (non-fatal)", body=str(e), node=node)

            context_block = instructions.INPUT_VALIDATOR_CTX_PROMPT.format(
                user_goal=user_goal,
                current_step_title=current_step_title,
                temp_dir=temp_dir,
                files_str=files_str,
                observation_state=manifest.get("observations", [])
            ).strip()

            msgs = [
                self.system_prompt,
                HumanMessage(content=instructions.INPUT_VALIDATOR_PROMPT),
                HumanMessage(content=context_block),
            ]
            self._log("ENTER NODE", body=f"step_idx={state['current_idx']}\nstep_title={step['title']}", node=node)
            resp = self._llm_invoke(node, "input_guard_check", msgs)
            
            items, ok = StateGraphHelper.parse_missing_ok(resp.content)
            if not ok:
                new_manifest = {
                    **state["manifest"],
                    "route_hint": "ask_for_missing",
                    "qa_payload": "\n".join(f"- {m}" for m in items),
                }
                self._log("MISSING INPUTS", body="\n".join(items), node=node)
                self._log("EXIT NODE", body="next_step=qa (ask_for_missing)", node=node)
                return {
                    "messages": [AIMessage(content=resp.content)],
                    "missing": items,
                    "manifest": new_manifest,
                    "next_step": "qa",
                }
            else:
                new_manifest = {
                    **state["manifest"],
                    "input_state": {
                        "summary": [m for m in items],
                        "root_dir": temp_dir,
                        "files": files,
                        "guidance": (
                            "Use either the initial user prompt or the 'files' list "
                            "to decide what inputs to use for code generation in this step."
                        ),
                    }
                }
                self._log("INPUTS OK", body="No missing items", node=node)
                self._log("EXIT NODE", body="next_step=generator", node=node)
                return {
                    "manifest": new_manifest,
                    "next_step": "generator",
                    "messages": [AIMessage(content=resp.content)],
                }
        
        def _generator(self, state: AgentState) -> AgentState:
            node = "generator"
            step = state["plan"][state["current_idx"]]
            env_name = state["env_name"]
            
            # detect repair mode
            manifest = state.get("manifest", {}) or {}
            repair_feedback = manifest.get("repair_feedback")
            is_diagnostic = isinstance(repair_feedback, str) and repair_feedback.strip().upper().startswith("DIAGNOSTICS_REQUEST:")
            temp_dir = state.get("run_temp_dir", "")
            files = self._list_ctx_files(temp_dir)
            files_str = "\n".join(f"- {f['name']} ({f['ext']}, {f['size_bytes']} bytes)" for f in files) or "<none>"

            if repair_feedback:
                if is_diagnostic:
                    prompt = instructions.GENERATOR_PROMPT
                    content = instructions.GENERATOR_DIAGNOSTICS_MODE_PROMPT.format(
                        diagnostics_feedback=repair_feedback,
                    )
                else:
                    prompt = instructions.GENERATOR_PROMPT_REPAIR
                    content = instructions.GENERATOR_REPAIR_CTX_PROMPT.format(
                        user_goal=state['last_prompt'],
                        current_step_title=step['title'],
                        manifest=manifest.get("input_state"),
                        run_temp_dir=temp_dir,
                        repair_feedback=repair_feedback,
                        previous_code=(state.get("pending_code") or "").strip(),
                        last_result=(state.get("last_result") or "").strip(),
                        files_str=files_str,
                    )
            else:
                prompt = instructions.GENERATOR_PROMPT
                content = instructions.GENERATOR_CTX_PROMPT.format(
                    user_goal=state['last_prompt'],
                    current_step_title=step['title'],
                    manifest=state['manifest'].get("input_state"),
                    run_temp_dir=state['run_temp_dir'],
                )
            
            msgs = [
                self.system_prompt, 
                HumanMessage(content=prompt), 
                HumanMessage(content=content)
            ]
            
            self._log("ENTER NODE", body=f"step_idx={state['current_idx']}\nrepair_mode={bool(repair_feedback)}", node=node)
            resp = self._llm_invoke(node, "code_gen", msgs)
    
            sanitized_block = StateGraphHelper.sanitize_execute_block(resp.content)
            code, lang = StateGraphHelper.parse_execute(sanitized_block)
            
            code_key = "diagnostic_code" if is_diagnostic else "pending_code"
            updates = {
                code_key: code,
                "env_name": env_name,
                "next_step": "ensure_env",
                "messages": [AIMessage(content=sanitized_block)],
            }
            
            # clear repair metadata once we ave generated new code
            if repair_feedback:
                new_manifest = dict(manifest)
                new_manifest.pop("repair_feedback", None)
                new_manifest.pop("repair_step_idx", None)
                updates["manifest"] = new_manifest
            
            # MAYBE: return to observer from here if no code;
            self._log("GENERATED CODE", body=code or "<empty>", node=node)
            
            # ------ feedback replay mode check ------
            pause = self._maybe_pause(
                state,
                resume_to="ensure_env",
                pause_kind="after_generator",
                prompt_text=(
                    "CODE PROPOSED TO EXECUTE THIS STEP \n\n"
                    # "Approve with **yes** to run, or send edits/constraints to regenerate before running.\n\n"
                    f"{sanitized_block}"
                ),
            )
            if pause:
                return {**updates, **pause}
            # ----------------------------------------

            return updates
        
        def _ensure_env(self, state: AgentState) -> AgentState:
            # Todos: (MVP) mark ready; replace with env manager later
            # state["env_ready"] = True
            # state["next_step"] = "executor"
            # return state
            from genomeer.runtime.env_manager import load_registry, spec_path, create_or_update_env, env_prefix, has_env
            env_name = state.get("env_name")

            if has_env(env_name):
                prefix = str(env_prefix(env_name))
                return {
                    "env_ready": True,
                    "next_step": "executor",
                    "messages": [AIMessage(content=f"<observe>Environment '{env_name}' ready at {prefix}</observe>")],
                }
            
            # first visit: create a stream and announce
            entry = self._install_threads.get(env_name)
            if entry is None:
                sid, stream = self.log_registry.create()
                self._install_threads[env_name] = {"stream_id": sid, "stream": stream}
                return {
                    "next_step": "ensure_env",
                    "messages": [AIMessage(content=f"<subscribe>Installing '{env_name}'. Subscribe to logs with stream_id='{sid}'.</subscribe>")],
                }
            
            # second visit: run the installer synchronously and stream logs (blocking)
            try:
                reg = load_registry()
                rec = next((e for e in reg.get("envs", []) if e.get("name") == env_name), None)
                if not rec:
                    try: 
                        entry["stream"].close()
                    except Exception: 
                        pass
                    self._install_threads.pop(env_name, None)
                    return {
                        "next_step": "end",
                        "messages": [AIMessage(content=f"<observe>Error: Env '{env_name}' not found in registry.</observe>")],
                    }
                
                spec = spec_path(rec["spec"])
                channels = rec.get("channels")
                stream = entry["stream"]
                
                # Block until micromamba finishes; logs go to stream.push(...)
                create_or_update_env(env_name, spec, channels, stream)
                
                # Success: mark ready, close stream, cleanup
                stream.push(f"Environment '{env_name}' created.")
                try: 
                    stream.close()
                except Exception: 
                    pass
                self._install_threads.pop(env_name, None)
                
                prefix = str(env_prefix(env_name))
                return {
                    "env_ready": True,
                    "next_step": "executor",
                    "messages": [AIMessage(content=f"<observe>Environment '{env_name}' ready at {prefix}</observe>")]
                }
            except Exception as e:
                try: entry["stream"].push(f"ERROR: {e}\n")
                except Exception: pass
                try: entry["stream"].close()
                except Exception: pass
                self._install_threads.pop(env_name, None)
                return {
                    "next_step": "end",
                    "messages": [AIMessage(content=f"<observe>Env install failed: {e}</observe>")]
                }
        
        def _executor(self, state: AgentState) -> AgentState:
            node = "executor"
            code = (state.get("pending_code") or "").strip()
            diagnostic_code = (state.get("diagnostic_code") or "").strip()
            diagnostic_mode = state.get("diagnostic_mode")
            env = state["env_name"]
            timeout = state["manifest"].get("timeout_seconds", 600)
            last_result = ""

            self._log("ENTER NODE", body=f"env={env}\ntimeout={timeout}s\ncode_preview=\n{code[:500] or '<no code>'}", node=node)

            if not code or (diagnostic_mode and not diagnostic_code):
                updates = {
                    "next_step": "observer",
                    "last_result": "No code produced by GENERATOR.",
                    "messages": [AIMessage(content="No code produced by for this step.")],
                }
                self._log("NO CODE", body="Skipping execution", node=node)
                self._log("EXIT NODE", body="next_step=observer", node=node)
                return updates

            if diagnostic_mode:
                code = diagnostic_code
                
            try:
                if (code.strip().startswith("#!R") or code.strip().startswith("# R code") or code.strip().startswith("# R script")):
                    r_code = re.sub(r"^#!R|^# R code|^# R script", "", code, 1).strip()  # noqa: B034
                    out = run_with_timeout(
                        run_r_code, 
                        args=[r_code], 
                        kwargs={
                            "env_name": env,
                        }, 
                        timeout=timeout
                    )
                elif (code.strip().startswith("#!BASH") or code.strip().startswith("# Bash script") or code.strip().startswith("#!CLI")):
                    if code.strip().startswith("#!CLI"):
                        cli_command = re.sub(r"^#!CLI", "", code, 1).strip().replace("\n", " ")  # noqa: B034
                        out = run_with_timeout(
                            # PATCH: [EXECUTION ERROR] TypeError: 'NoneType' object is not subscriptable
                            run_bash_script, #run_cli_command, 
                            args=[cli_command], 
                            kwargs={
                                "env_name": env,
                            }, 
                            timeout=timeout
                        )
                    else:
                        bash_script = re.sub(r"^#!BASH|^# Bash script", "", code, 1).strip()  # noqa: B034
                        out = run_with_timeout(
                            run_bash_script, 
                            args=[bash_script], 
                            kwargs={
                                "env_name": env,
                            },
                            timeout=timeout
                        )
                else:
                    # Inject custom functions into the Python execution environment
                    self._inject_custom_functions_to_repl() #  TODOs: PRORITY-CHECK
                    code = re.sub(r"^\s*#!PY\s*\r?\n", "", code, count=1)
                    out = run_with_timeout(
                        run_python_code, 
                        args=[code], 
                        kwargs={
                            "env_name": env,
                        },
                        timeout=timeout
                    )

                # bound size
                if out and len(out) > 12000:
                    out = out[:12000] + "\n...<truncated>"
                    
                last_result = out or ""
                self._log("EXECUTION RESULT", body=last_result[:2000], node=node)
            except Exception as e:
                tb = traceback.format_exc()
                last_result = f"[EXECUTION ERROR] {type(e).__name__}: {e}\n"
                last_result += f"traceback: {tb}"
                self._log("EXECUTION ERROR", body=last_result, node=node)
                
            self._log("EXIT NODE", body="next_step=observer", node=node)
            result_key = "diagnostic_observation" if diagnostic_mode else "last_result"
            updates = {
                "next_step": "observer", #end
                result_key: last_result,
                "messages": [AIMessage(content=f"<observe>Code Execution output:  '{last_result}'</observe>")],
            }
            return updates
        
        def _observer(self, state: AgentState) -> AgentState:
            node = "observer"
            step = state["plan"][state["current_idx"]]
            diagnostic_mode = state.get("diagnostic_mode")
            
            if diagnostic_mode:
                payload = instructions.OBSERVER_DIAGNOSTIC_CTX_PROMPT.format(
                    user_goal=state['last_prompt'],
                    current_step_title=step['title'],
                    manifest=state['manifest'],
                    code=(state.get("pending_code") or "").strip(),
                    result=state['last_result'],
                    diagnostic_code=state.get("diagnostic_code").strip(),
                    diagnostic_output=state.get("diagnostic_observation").strip(),
                )
            else:
                payload = instructions.OBSERVER_CTX_PROMPT.format(
                    user_goal=state['last_prompt'],
                    current_step_title=step['title'],
                    manifest=state['manifest'],
                    code=(state.get("pending_code") or "").strip(),
                    result=state['last_result'],
                )
            msgs = [
                self.system_prompt,
                HumanMessage(content=instructions.OBSERVER_PROMPT),
                HumanMessage(content=payload),
            ]
            
            self._log("ENTER NODE", body=f"step_idx={state['current_idx']}\nstep_title={step['title']}", node=node)
            resp = self._llm_invoke(node, "observe_and_status", msgs)
                    
            status, summary = StateGraphHelper.parse_status(resp.content)
            next_step = "generator" if status == "blocked" else "orchestrator"
            next_idx = state["current_idx"] + (0 if status == "blocked" else 1)
            
            new_manifest = dict(state["manifest"])
            rc = dict(state.get("retry_counts") or {})
            diag_rounds = dict(state["manifest"].get("diagnostics_rounds") or {})
            if status == "blocked":
                rc[state["current_idx"]] = rc.get(state["current_idx"], 0) + 1
                new_manifest["retry_count"] = rc[state["current_idx"]]
                new_manifest["repair_feedback"] = summary
                new_manifest["repair_step_idx"] = state["current_idx"]
                
                # routing
                if rc[state["current_idx"]] > self.MAX_STEP_RETRIES:
                    next_step = "diagnostics"
                    next_idx = state["current_idx"]
                else:
                    next_step = "generator"
                    next_idx = state["current_idx"]
                    
                # logs
                self._log("STATUS", body=f"blocked=True\nnotes=\n{summary}", node=node)
                self._log("EXIT NODE", body="next_step=input_guard (retry same step)", node=node)
            else:
                new_manifest.pop("repair_feedback", None)
                new_manifest.pop("repair_step_idx", None)
                new_manifest.pop("retry_count", None)
                
                # routing
                next_step = "orchestrator"
                next_idx = state["current_idx"] + 1
                if state["current_idx"] in rc:
                    rc.pop(state["current_idx"], None)
                    
                # logs
                self._log("STATUS", body=f"done=True\nnotes=\n{summary}", node=node)
                self._log("EXIT NODE", body=f"advance_to_idx={state['current_idx']}\nnext_step=orchestrator", node=node)
                
                # storing succes state observation
                obs = {
                    "step_idx": state["current_idx"],
                    "title": step["title"],
                    "status": status,
                    "summary": summary,
                    "stdout": (state.get("last_result") or "")[:12000],
                    "files_snapshot": self._list_ctx_files(state.get("run_temp_dir","")),
                }
                new_manifest["observations"] = list(new_manifest.get("observations", [])) + [obs]


            plan = list(state["plan"])
            plan[state["current_idx"]] = {
                **plan[state["current_idx"]],
                "notes": summary,
                "status": "done" if status == "done" else "blocked",
            }
            updates = {
                "plan": plan,
                "current_idx": next_idx,
                "next_step": next_step,
                "messages": [AIMessage(content=resp.content)],
                "manifest": new_manifest,
                "retry_counts": rc,
                "diagnostic_mode": False,
                "diagnostic_code": False,
                "diagnostic_observation": False,
            }
            
            # ------ feedback replay mode check ------
            if status == "blocked":
                pause = self._maybe_pause(
                    state,
                    resume_to="generator",
                    pause_kind="after_observer",
                    prompt_text=f"""STEP RESULT\n\nStatus: {status.upper()}\n\nSummary:\n{summary}\n\n""",
                    # Reply **yes** to continue. I'll try to fix the issue by myself, or send changes to retry this step before moving on.""",
                )
                if pause:
                    return {**updates, **pause}
            # ----------------------------------------

            return updates
        
        def _diagnostics(self, state: AgentState) -> AgentState:
            node = "diagnostics"
            step = state["plan"][state["current_idx"]]
            manifest = state.get("manifest", {}) or {}
            retry_count = manifest.get("retry_count", 0)
            observer_summary = manifest.get("repair_feedback", "").strip()
            last_code = (state.get("pending_code") or "").strip()

            prompt = instructions.DIAGNOSTICS_PROMPT
            ctx = instructions.DIAGNOSTICS_CTX_PROMPT.format(
                user_goal=state.get("last_prompt",""),
                current_step_title=step["title"],
                retry_count=retry_count,
                observer_summary=observer_summary or "<none>",
                last_code=last_code or "<none>",
                run_temp_dir=state.get("run_temp_dir",""),
            )

            msgs = [
                self.system_prompt, 
                HumanMessage(content=prompt),
                HumanMessage(content=ctx) 
            ]
            self._log("ENTER NODE", body=f"retry_count={retry_count}\nstep={step['title']}", node=node)
            resp = self._llm_invoke(node, "diagnostics_plan", msgs)

            # Reuse GENERATOR to actually produce the probe code
            # We piggyback repair flow by stuffing the plan into 'repair_feedback'
            new_manifest = dict(manifest)
            new_manifest["repair_feedback"] = f"DIAGNOSTICS_REQUEST:\n{resp.content}"
            new_manifest["repair_step_idx"] = state["current_idx"]

            self._log("EXIT NODE", body="next_step=generator (probe code)", node=node)
            
            rc = dict(state.get("retry_counts") or {})
            if state["current_idx"] in rc:
                rc.pop(state["current_idx"], None)
            return {
                "retry_counts": rc,
                "manifest": new_manifest,
                "diagnostic_mode": True,
                "next_step": "generator",
                "messages": [AIMessage(content=resp.content)],
            }
            
        def _finalizer(self, state: AgentState) -> AgentState:
            node = "finalizer"
            self._log("ENTER NODE", body="publishing artifacts + generating report", node=node)

            manifest = dict(state.get("manifest") or {})
            temp_dir = state.get("run_temp_dir", "")
            run_id = state.get("run_id")
            pub = manifest.get("publisher") or {}
            base_url = (pub.get("base_url") or "").rstrip("/")

            files = self._list_ctx_files(temp_dir)
            def _want(relname: str) -> bool:
                name = relname.lower()
                SKIP = (".cache/", "__pycache__", ".ipynb_checkpoints", ".mamba", ".micromamba")
                return not any(x in name for x in SKIP)

            expose_paths = [f["name"] for f in files if _want(f["name"])]

            artifacts = {}
            observations = manifest.get("observations", [])
            try:
                # from genomeer.tools.function.artifacts import create_run, upload_files, publish_run
                from genomeer.agent.v2.utils.artifacts_service import create_run, upload_files, publish_run_http
                try:
                    create_run(run_id, base_url=self.artifacts_base_url)
                except Exception:
                    pass

                abs_paths = [str(Path(temp_dir) / p) for p in expose_paths]
                if abs_paths:
                    upload_files(run_id, abs_paths, subdir="outputs", base_url=self.artifacts_base_url)

                expose_rel = [f"outputs/{Path(p).name}" for p in expose_paths]
                art_manifest = publish_run_http(run_id, expose_rel, base_url=self.artifacts_base_url)
                artifacts = art_manifest or {}
            except Exception as e:
                self._log("PUBLISH ERROR", body=str(e), node=node)
                artifacts = {"artifacts": [], "error": str(e)}

            msgs = [
                SystemMessage(content=instructions.FINALIZER_PROMPT),
                HumanMessage(content=instructions.FINALIZER_CTX_PROMPT.format(
                    user_goal=state.get("last_prompt"),
                    plan=state.get("plan"),
                    observation=observations,
                    artifacts=artifacts
                ))
            ]
            resp = self._llm_invoke(node, "final_report", msgs)
            self._log("EXIT NODE", body="final report generated", node=node)
            return {
                "manifest": manifest,
                "next_step": "end",
                "messages": [AIMessage(content=resp.content.strip())],
            }

        
        # Bind as a bound method so it receives self automatically
        # -------------------------------------------------------------------------------
        self.planner = types.MethodType(_planner, self)
        self.qa = types.MethodType(_qa, self)
        self.orchestrator = types.MethodType(_orchestrator, self)
        self.input_guard = types.MethodType(_input_guard, self)
        self.generator = types.MethodType(_generator, self)
        self.ensure_env = types.MethodType(_ensure_env, self)
        self.executor = types.MethodType(_executor, self)
        self.observer = types.MethodType(_observer, self)
        self.diagnostics = types.MethodType(_diagnostics, self)
        self.finalizer = types.MethodType(_finalizer, self)
        
        # Create the workflow
        # --------------------------------------------------------------------------------
        workflow = StateGraph(AgentState)
        workflow.add_node("planner", self.planner)
        workflow.add_node("qa", self.qa)
        workflow.add_node("orchestrator", self.orchestrator)
        workflow.add_node("input_guard", self.input_guard)
        workflow.add_node("generator", self.generator)
        workflow.add_node("ensure_env", self.ensure_env)
        workflow.add_node("executor", self.executor)
        workflow.add_node("observer", self.observer)
        workflow.add_node("diagnostics", self.diagnostics)
        workflow.add_node("finalizer", self.finalizer)

        # defining workflow edges
        workflow.add_edge(START, "planner")
        workflow.add_conditional_edges(
            "planner",
            lambda s: s["next_step"],
            {
                "qa": "qa",
                "orchestrator": "orchestrator",
                # for jump after feedback
                "generator": "generator",
                "ensure_env": "ensure_env",
                "input_guard": "input_guard",
            },
        )
        workflow.add_conditional_edges(
            "orchestrator",
            lambda s: s["next_step"],
            {
                "planner": "planner",
                "input_guard": "input_guard",
                "finalizer": "finalizer",
            },
        )
        workflow.add_conditional_edges(
            "input_guard",
            lambda s: s["next_step"],
            {
                "qa": "qa",
                "generator": "generator",
            },
        )
        workflow.add_conditional_edges(
            "generator",
            lambda s: s["next_step"],
            {
                "ensure_env": "ensure_env",
                "qa": "qa",
            },
        )
        workflow.add_conditional_edges(
            "ensure_env",
            lambda s: s["next_step"],
            {
                "ensure_env": "ensure_env",
                "executor": "executor",
                "end": END,
            },
        )
        workflow.add_conditional_edges(
            "executor",
            lambda s: s["next_step"],
            {
                "observer": "observer",
            },
        )
        workflow.add_conditional_edges(
            "observer",
            lambda s: s["next_step"],
            {
                "orchestrator": "orchestrator",
                "generator": "generator",
                "diagnostics": "diagnostics",
                "qa": "qa",
            },
        )
        workflow.add_conditional_edges(
            "diagnostics",
            lambda s: s["next_step"],
            {
                "generator": "generator",
                "end": "qa",
            },
        )
        workflow.add_edge("qa", END)
        workflow.add_edge("finalizer", END)
        
        # Compile the workflow
        # --------------------------------------------------------------------------------
        self.app = workflow.compile()
        self.checkpointer = MemorySaver()
        self.app.checkpointer = self.checkpointer


    # OTHER UTILS
    def _stage_attachments(self, tmp_dir: str, attachments: list[str]) -> list[str]:
        """
        Copy user-supplied file paths into the run's temp dir
        Returns the relative paths inside tmp_dir
        """
        staged_rel: list[str] = []
        up = os.path.join(tmp_dir, "uploads")
        os.makedirs(up, exist_ok=True)
        for src in attachments or []:
            try:
                bn = os.path.basename(src)
                dst = os.path.join(up, bn)
                # copy (safer across FS boundaries)
                shutil.copy2(src, dst)
                staged_rel.append(os.path.relpath(dst, tmp_dir))
            except Exception as e:
                self._log("ATTACH STAGE ERROR", body=f"{src}: {e}", node="driver")
        return staged_rel

    def _list_ctx_files(self, temp_dir: str):
        """
        - This function will return a list of all files available in the the current run temp folder
        - Indeed each request have a temp storage folder - ex: `/tmp/206005a0-c0a1-4114-907c-c3eda23d3f32`
        - All uploaded file will be inside automatically and all downloaded file by agent will be there.
        Return a list of all files inside temp_dir (including subfolders).
        Each item: {'name': 'relative/path/to/file', 'ext': '.fasta', 'size_bytes': 123}
        """
        files = []
        try:
            for root, _, entries in os.walk(temp_dir):
                for entry in sorted(entries):
                    p = os.path.join(root, entry)
                    if os.path.isfile(p):
                        rel_path = os.path.relpath(p, temp_dir)  # keep it relative
                        ext = os.path.splitext(entry)[1]
                        files.append({
                            "name": rel_path,
                            "ext": ext if ext else "",
                            "size_bytes": os.path.getsize(p),
                        })
        except Exception as e:
            self._log("TEMP LIST ERROR", body=str(e), node="input_guard")
        return files
    
    def _inject_custom_functions_to_repl(self):
        """Inject custom functions into the Python REPL execution environment.
        This makes custom tools available during code execution.
        """
        if hasattr(self, "_custom_functions") and self._custom_functions:
            # Access the persistent namespace used by run_python_repl
            from genomeer.utils.helper import _persistent_namespace

            # Inject all custom functions into the execution namespace
            for name, func in self._custom_functions.items():
                _persistent_namespace[name] = func

            # Also make them available in builtins for broader access
            import builtins

            if not hasattr(builtins, "_bioagent_custom_functions"):
                builtins._bioagent_custom_functions = {}
            builtins._bioagent_custom_functions.update(self._custom_functions)
            
    def _prepare_resources_for_retrieval(self, prompt):
        """Prepare resources for retrieval and return selected resource names.
        Args:
            prompt: The user's query
        Returns:
            dict: Dictionary containing selected resource names for tools, data_lake, and libraries
        """
        if not self.use_tool_retriever:
            return None

        # Gather all available resources
        # 1. Tools from the registry
        all_tools = self.tool_registry.tools if hasattr(self, "tool_registry") else []

        # 2. Data lake items with descriptions
        data_lake_path = self.path + "/data_lake"
        data_lake_content = glob.glob(data_lake_path + "/*")
        data_lake_items = [x.split("/")[-1] for x in data_lake_content]

        # Create data lake descriptions for retrieval
        data_lake_descriptions = []
        for item in data_lake_items:
            description = self.data_lake_dict.get(item, f"Data lake item: {item}")
            data_lake_descriptions.append({"name": item, "description": description})

        # Add custom data items to retrieval if they exist
        if hasattr(self, "_custom_data") and self._custom_data:
            for name, info in self._custom_data.items():
                data_lake_descriptions.append({"name": name, "description": info["description"]})

        # 3. Libraries with descriptions - use library_content_dict directly
        library_descriptions = []
        for lib_name, lib_desc in self.library_content_dict.items():
            library_descriptions.append({"name": lib_name, "description": lib_desc})

        # Add custom software items to retrieval if they exist
        if hasattr(self, "_custom_software") and self._custom_software:
            for name, info in self._custom_software.items():
                # Check if it's not already in the library descriptions to avoid duplicates
                if not any(lib["name"] == name for lib in library_descriptions):
                    library_descriptions.append({"name": name, "description": info["description"]})

        # Use retrieval to get relevant resources
        resources = {
            "tools": all_tools,
            "data_lake": data_lake_descriptions,
            "libraries": library_descriptions,
        }

        # Use prompt-based retrieval with the agent's LLM
        selected_resources = self.retriever.prompt_based_retrieval(prompt, resources, llm=self.llm)
        print("Using prompt-based retrieval with the agent's LLM")

        # Extract the names from the selected resources for the system prompt
        selected_resources_names = {
            "tools": selected_resources["tools"],
            "data_lake": [],
            "libraries": [lib["name"] if isinstance(lib, dict) else lib for lib in selected_resources["libraries"]],
        }

        # Process data lake items to extract just the names
        for item in selected_resources["data_lake"]:
            if isinstance(item, dict):
                selected_resources_names["data_lake"].append(item["name"])
            elif isinstance(item, str) and ": " in item:
                # If the item already has a description, extract just the name
                name = item.split(": ")[0]
                selected_resources_names["data_lake"].append(name)
            else:
                selected_resources_names["data_lake"].append(item)

        return selected_resources_names

    def _start_artifacts_server_in_bg(self, host: str, port: int, prefix: str):
        """
        Fire-and-forget tiny artifact server in a background thread.
        Intended for local/dev workflows. In production, prefer mounting the router in main API.
        """
        def _run():
            from genomeer.agent.v2.utils.artifacts_service import start_artifacts_server
            start_artifacts_server(host=host, port=port, prefix=prefix)
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        self._log("ARTIFACT SERVER", f"Started on http://{host}:{port}{prefix}", node="driver")
        
    def update_system_prompt_with_selected_resources(self, selected_resources):
        """Update the system prompt with the selected resources."""
        # Extract tool descriptions for the selected tools
        tool_desc = {}
        for tool in selected_resources["tools"]:
            # Get the module name from the tool
            if isinstance(tool, dict):
                module_name = tool.get("module", None)

                # If module is not specified, try to find it in the module2api
                if not module_name and hasattr(self, "module2api"):
                    for mod, apis in self.module2api.items():
                        for api in apis:
                            if api.get("name") == tool.get("name"):
                                module_name = mod
                                # Update the tool with the module information
                                tool["module"] = module_name
                                break
                        if module_name:
                            break

                # If still not found, use a default (fallback)
                if not module_name:
                    module_name = "genomeer.tool.scRNA_tools"
                    tool["module"] = module_name
            else:
                module_name = getattr(tool, "module_name", None)

                # If module is not specified, try to find it in the module2api
                if not module_name and hasattr(self, "module2api"):
                    tool_name = getattr(tool, "name", str(tool))
                    for mod, apis in self.module2api.items():
                        for api in apis:
                            if api.get("name") == tool_name:
                                module_name = mod
                                # Set the module_name attribute
                                tool.module_name = module_name
                                break
                        if module_name:
                            break

                # If still not found, use a default
                if not module_name:
                    module_name = "genomeer.tool.scRNA_tools"  # Default to scRNA_tools as a fallback
                    tool.module_name = module_name

            if module_name not in tool_desc:
                tool_desc[module_name] = []

            # Add the tool to the appropriate module
            if isinstance(tool, dict):
                # Ensure the module is included in the tool description
                if "module" not in tool:
                    tool["module"] = module_name
                tool_desc[module_name].append(tool)
            else:
                # Convert tool object to dictionary
                tool_dict = {
                    "name": getattr(tool, "name", str(tool)),
                    "description": getattr(tool, "description", ""),
                    "parameters": getattr(tool, "parameters", {}),
                    "module": module_name,  # Explicitly include the module
                }
                tool_desc[module_name].append(tool_dict)

        # Prepare data lake items with descriptions
        data_lake_with_desc = []
        for item in selected_resources["data_lake"]:
            description = self.data_lake_dict.get(item, f"Data lake item: {item}")
            data_lake_with_desc.append({"name": item, "description": description})

        # Prepare custom resources for highlighting
        custom_tools = []
        if hasattr(self, "_custom_tools") and self._custom_tools:
            for name, info in self._custom_tools.items():
                custom_tools.append(
                    {
                        "name": name,
                        "description": info["description"],
                        "module": info["module"],
                    }
                )

        custom_data = []
        if hasattr(self, "_custom_data") and self._custom_data:
            for name, info in self._custom_data.items():
                custom_data.append({"name": name, "description": info["description"]})

        custom_software = []
        if hasattr(self, "_custom_software") and self._custom_software:
            for name, info in self._custom_software.items():
                custom_software.append({"name": name, "description": info["description"]})

        self.system_prompt = self._generate_system_prompt(
            tool_desc=tool_desc,
            data_lake_content=data_lake_with_desc,
            library_content_list=selected_resources["libraries"],
            self_critic=getattr(self, "self_critic", False),
            is_retrieval=True,
            custom_tools=custom_tools if custom_tools else None,
            custom_data=custom_data if custom_data else None,
            custom_software=custom_software if custom_software else None,
        )

        # Print the raw system prompt for debugging
        # print("\n" + "="*20 + " RAW SYSTEM PROMPT FROM AGENT " + "="*20)
        # print(self.system_prompt)
        # print("="*70 + "\n")
    
    def visualize_graph(self, mode="manual", file: str | None = None, show: bool = True):
        """
        Visualize the compiled agent graph using mermaid.ink API.
        Returns PNG bytes.
        """
        if not hasattr(self, "app"):
            raise RuntimeError("Graph is not compiled yet. Call configure() first.")

        import base64, requests
        from IPython.display import Image, display

        if mode  == "manual":
            mmd = self.app.get_graph().draw_mermaid()
            print(mmd)
            print("Open : https://mermaid.live/edit and pass this code to render the graph.")
            return mmd
        
        encoded = base64.urlsafe_b64encode(mmd.encode("utf-8")).decode("ascii")
        url = f"https://mermaid.ink/svg/{encoded}"

        resp = requests.get(url)
        resp.raise_for_status()
        img_bytes = resp.content

        if file:
            with open(file, "wb") as f:
                f.write(img_bytes)
        if show:
            display(Image(img_bytes))
        return img_bytes
     
    def extract_tagged_blocks(self, text: str):
        """
        Split text into an ordered list of segments:
        - {"kind": "text", "text": "..."}
        - {"kind": "block", "tag": "EXECUTE"|"OBSERVE"|... , "text": "<...>...</...>"}
        Preserves exact order of appearance. Handles:
        - Paired tags: <execute|observe|observation|solution|think|subscribe|logs>...</...>
        - Standalone tags: <STATUS:...>, <OK/>, <NEXT:...>
        """
        if not text:
            return []

        RX = re.compile(
            r"""
            (?P<block>                                   # Paired block
            <
                (?P<name>[a-z]+)                        # tag name (letters)
                (?:\s+[^>]*)?                           # optional attrs
            >
            (?P<body>.*?)
            </(?P=name)>
            )
            |
            (?P<standalone>                              # Standalone tags
            <
                (?P<solo>STATUS:[^>]+|OK\s*/\s*|NEXT:[^>]+)
            \s*>
            )
            """,
            re.IGNORECASE | re.DOTALL | re.VERBOSE,
        )

        segments = []
        pos = 0
        for m in RX.finditer(text):
            start, end = m.start(), m.end()
            # Emit any preceding plain text
            if start > pos:
                before = text[pos:start]
                if before:  # keep empty filtering to caller if you want
                    segments.append({"kind": "text", "text": before})

            if m.group("block"):
                raw = m.group(0)
                name = (m.group("name") or "").upper()
                # normalize OBSERVATION -> OBSERVE (optional)
                if name == "OBSERVATION":
                    name = "OBSERVE"
                segments.append({"kind": "block", "tag": name, "text": raw})
            else:
                raw = m.group(0)
                solo = (m.group("solo") or "").upper()
                # Tag is the leading token before ':' or whitespace
                base = solo.split(":", 1)[0].split()[0]  # STATUS / OK / NEXT
                segments.append({"kind": "block", "tag": base, "text": raw})

            pos = end

        # Emit trailing text
        if pos < len(text):
            tail = text[pos:]
            if tail:
                segments.append({"kind": "text", "text": tail})

        return segments
    
    def _history_snippet(self, messages, max_chars=3000):
        parts = []
        for m in messages[-10:]:  # last 10 turns
            role = getattr(m, "type", "").upper() or m.__class__.__name__.upper()
            parts.append(f"{role}: {getattr(m, 'content', str(m))}")
        txt = "\n".join(parts)
        return txt[-max_chars:]
    
    def _has_session_state(self, thread_id: str) -> bool:
        try:
            state = self.app.get_state({"configurable": {"thread_id": thread_id}})
            # state.values holds your saved AgentState; state.next is the saved next node
            return bool(state and (state.values or state.next))
        except Exception:
            return False

    def _maybe_pause(self, state: AgentState, *, resume_to: str, prompt_text: str, pause_kind: str) -> Dict[str, Any] | None:
        mode = (state.get("manifest") or {}).get("interaction_mode", "auto")
        if mode != "feedback":
            return None
        
        if not (prompt_text and prompt_text.strip()):
            self._log("HITL: skip pause (empty prompt_text)", node="driver")
            return None

        new_manifest = dict(state.get("manifest") or {})
        new_manifest["route_hint"] = "await_user"
        new_manifest["qa_payload"] = prompt_text
        new_manifest["resume_to"] = resume_to
        new_manifest["resume_step_idx"] = state.get("current_idx", 0)
        new_manifest["pause_kind"] = pause_kind

        return {
            "manifest": new_manifest,
            "next_step": "qa",
            "messages": [AIMessage(content=f"<REVIEW>\n{prompt_text}\n</REVIEW>")],
        }


    # AGENT RUNNER
    def go(self, prompt, mode: str = "dev", attachments: list[str] | None = None, session_id: str | None = None, cancel_event: Any = None):
        """Execute the agent with the given prompt.
        Args:
            prompt: The user's query
            mode: 'dev' (default) shows everything; 'prod' hides HumanMessage outputs.
        """
        self.critic_count = 0
        self.user_task = prompt
        thread_id = session_id or str(uuid4())
        
        assert mode in ("dev", "prod"), "mode must be 'dev' or 'prod'"
        def _is_human(msg) -> bool:
            return getattr(msg, "type", "").lower() == "human"

        if self.use_tool_retriever:
            selected_resources_names = self._prepare_resources_for_retrieval(prompt)
            self.update_system_prompt_with_selected_resources(selected_resources_names)

        with run_workdir("run", session_id) as tmp:
            staged = self._stage_attachments(tmp, attachments or [])
            
            if not self._has_session_state(thread_id):
                # FIRST TURN OF THIS SESSION -> full bootstrap state
                inputs = {
                    "messages": [HumanMessage(content=prompt)], 
                    "next_step": None, 
                    "env_name": "bio-agent-env1", 
                    "env_ready": False, 
                    "pending_code": None,
                    "manifest": {
                        "timeout_seconds": self.timeout_seconds, 
                        "observations": [], 
                        "attachments": staged,
                        "interaction_mode": getattr(self, "interaction_mode", "auto"),
                    },
                    "plan": [],
                    "current_idx": 0,
                    "last_prompt": None,
                    "last_result": None,
                    "missing": [],
                    "run_temp_dir": tmp,
                    "retry_counts": {},
                    "diagnostic_mode": False,
                    "diagnostic_code": None,
                    "diagnostic_observation": None,
                    "run_id": tmp.split('run')[1][1:],
                }
            else:
                # FOLLOW-UP TURN -> only append the new message and record any new attachments
                msg_block = [HumanMessage(content=prompt)]
                if staged:
                    msg_block.append(HumanMessage(content=f"[upload notice] New files: {staged}"))
                inputs = {
                    "messages": msg_block
                }
                
            config = {"recursion_limit": 500, "configurable": {"thread_id": thread_id}}
            self.log = []
            last_msg_text = None

            for s in self.app.stream(inputs, stream_mode="values", config=config):
                if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                    # graceful exit
                    last_msg_text = "<observe>Request canceled by client.</observe>"
                    break
                
                message = s["messages"][-1]
                if mode == "prod" and _is_human(message):
                    continue
            
                text = str(message.content)
                if text != last_msg_text:
                    out = pretty_print(message)
                    self.log.append(out)
                    last_msg_text = text
                
                # ************* logs [dev-only] *************
                curr_idx = s.get("current_idx", None)
                next_step = s.get("next_step", None)
                self._log("STEP SNAPSHOT", body=f"current_idx={curr_idx}\nnext_step={next_step}", node="driver")
                # *******************************************

            return self.log, last_msg_text #str(message.content)
    
    def go_stream(self, prompt, mode: str = "dev", attachments: list[str] | None = None, session_id: str | None = None, cancel_event: Any = None) -> Generator[dict, None, None]:
        """Execute the agent with the given prompt and return a generator that yields each step.
        This function returns a generator that yields each step of the agent's execution,
        allowing for real-time monitoring of the agent's progress.
        Args:
            prompt: The user's query
            mode: 'dev' (default) shows everything; 'prod' hides HumanMessage outputs.

        Yields:
            {"type": "message", "text": "..."}                 # de-duped raw assistant text
            {"type": "block", "tag": "EXECUTE", "text": "..."} # extracted tagged blocks
            {"type": "think", "tag": "THINK", "text": "..."}   # if THINK blocks appear
        """
        self.critic_count = 0
        self.user_task = prompt
        thread_id = session_id or str(uuid4())
        
        assert mode in ("dev", "prod"), "mode must be 'dev' or 'prod'"
        def _is_human(msg) -> bool:
            return getattr(msg, "type", "").lower() == "human"

        if self.use_tool_retriever:
            selected_resources_names = self._prepare_resources_for_retrieval(prompt)
            self.update_system_prompt_with_selected_resources(selected_resources_names)

        with run_workdir("run", session_id) as tmp:
            staged = self._stage_attachments(tmp, attachments or [])
            
            if not self._has_session_state(thread_id):
                # FIRST TURN OF THIS SESSION -> full bootstrap state
                inputs = {
                    "messages": [HumanMessage(content=prompt)], 
                    "next_step": None, 
                    "env_name": "bio-agent-env1", 
                    "env_ready": False, 
                    "pending_code": None,
                    "manifest": {
                        "timeout_seconds": self.timeout_seconds, 
                        "observations": [], 
                        "attachments": staged,
                        "interaction_mode": getattr(self, "interaction_mode", "auto"),
                    },
                    "plan": [],
                    "current_idx": 0,
                    "last_prompt": None,
                    "last_result": None,
                    "missing": [],
                    "run_temp_dir": tmp,
                    "retry_counts": {},
                    "diagnostic_mode": False,
                    "diagnostic_code": None,
                    "diagnostic_observation": None,
                    "run_id": tmp.split('run')[1][1:],
                }
            else:
                # FOLLOW-UP TURN -> only append the new message and record any new attachments
                msg_block = [HumanMessage(content=prompt)]
                if staged:
                    msg_block.append(HumanMessage(content=f"[upload notice] New files: {staged}"))
                inputs = {
                    "messages": msg_block
                }

            config = {"recursion_limit": 500, "configurable": {"thread_id": thread_id}}
            last_msg_text = None
            self.log = []

            for s in self.app.stream(inputs, stream_mode="values", config=config):
                # bail if canceled
                if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                    yield {"type": "message", "text": "<observe>Request canceled by client.</observe>"}
                    return
                
                message = s["messages"][-1]
                text = str(message.content)

                if mode == "prod" and _is_human(message):
                    continue
                if text == last_msg_text:
                    continue
                
                last_msg_text = text
                out = pretty_print(message)
                self.log.append(out)

                for seg in self.extract_tagged_blocks(text):
                    if seg["kind"] == "text":
                        if seg["text"].strip():
                            yield {"type": "message", "text": seg["text"]}
                    else:
                        tag = seg.get("tag", "BLOCK").upper()
                        if tag == "THINK":
                            yield {"type": "think", "tag": tag, "text": seg["text"]}
                        else:
                            yield {"type": "block", "tag": tag, "text": seg["text"]}
    
