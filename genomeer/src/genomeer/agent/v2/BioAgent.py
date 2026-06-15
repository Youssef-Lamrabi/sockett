# -----------------------------------------------
# LIBRARY
# -----------------------------------------------
from pathlib import Path
import copy, glob, inspect, os, re, threading, time, types, traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from genomeer.agent.v2.utils.validator import ToolValidator
from genomeer.agent.v2.utils.quality_gate import check_quality, BIOLOGICAL_GATES
from genomeer.utils.version_tracker import VersionTracker
from genomeer.model.feedback import FeedbackParser
from genomeer.utils.security import check_bash_script, check_python_code
from genomeer.model.bio_rag import BioRAGStore, BioRAGRetriever, build_finalizer_rag_context

# Minimum validator score required to accept a step as genuinely done.
# The AssemblyContract false-positive (staged FASTA with score=0.02) is now prevented
# by word-boundary matching in _match_contract. This threshold is kept very low (0.005)
# to allow valid low-protein-count runs (e.g. Prodigal on 15kb FASTA → 13 proteins
# → score=0.013) without false-blocking them.
_VALIDATOR_MIN_SCORE: float = 0.005

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
        "batch_orchestrator",
        "input_guard",
        "generator",
        "ensure_env",
        "executor",
        "validator",
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
    batch_mode: bool
    batch_strategy: str | None
    sample_manifest: List[Dict[str, Any]] | None
    current_sample_idx: int
    current_sample_id: str | None
    per_sample_results: Dict[str, Any]
    # ── bio_hint optional fields (set to None / -1 when not used) ───────────
    bio_hint: str | None          # raw validated text from the 8B domain model
    bio_hint_step_idx: int        # current_idx at last bio_hint call (dedup guard)
    bio_hint_mode: str | None     # "pre_gen" | "debug"
    bio_hint_skipped: bool        # True when triage decided to skip the 8B call
    # ── Multi-turn isolation (turn_id starts at 1 on first user prompt of a
    #    session, increments on every follow-up call). Used by _planner to
    #    detect a turn boundary and reset transient quality data without
    #    touching env state / attachments / file_registry / etc. ────────────
    turn_id: int


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
        interaction_mode: str = "auto",
        bio_hint_llm: Any = None,
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
        print("BioAgent_v3 CONFIGURATION")
        print("=" * 50)

        # show the effective (resolved) config — constructor args take priority over settings defaults
        effective = {
            "path":               path,
            "run_dir":            settings.run_dir,
            "timeout_seconds":    timeout_seconds,
            "llm":                llm,
            "temperature":        settings.temperature,
            "use_tool_retriever": use_tool_retriever,
            "source":             source,
            "base_url":           base_url,
        }
        print("EFFECTIVE CONFIG :")
        for key, value in effective.items():
            if value is not None:
                print(f"  {key.replace('_', ' ').title()}: {value}")
        if api_key is not None and api_key != "EMPTY":
            print(f"  Api Key: {'*' * 8 + api_key[-4:] if len(api_key) > 8 else '***'}")
        print("=" * 50 + "\n")

        # ── LangSmith tracing (optional, silent if no API key) ────────────────
        _ls_key = os.environ.get("LANGCHAIN_API_KEY", "").strip()
        if _ls_key:
            os.environ["LANGCHAIN_TRACING_V2"] = os.environ.get("LANGCHAIN_TRACING_V2", "true")
            os.environ["LANGCHAIN_API_KEY"] = _ls_key
            os.environ["LANGCHAIN_PROJECT"] = os.environ.get("LANGCHAIN_PROJECT", "genomeer")
            os.environ.setdefault("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com")
            print(f"LangSmith tracing: ON (project={os.environ['LANGCHAIN_PROJECT']})")
        # If LANGCHAIN_API_KEY is unset/empty → do nothing, langgraph defaults apply.

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

        # Optional secondary LLM for biological domain hints (bio_hint node)
        self.bio_hint_llm = bio_hint_llm

        # Add timeout parameter
        self.timeout_seconds = timeout_seconds
        self.configure()
        
        # [DEV-ONLY] logs
        # self._set_debug_log("/home/biolab-office-1/DATALAB/2025/Genomeer/genomeer/src/genomeer/agent/v2/agent_debug.log")
        self._set_debug_log("./agent_debug.log")
        
        self._version_tracker = VersionTracker()

        # CONSTANTS
        self.MAX_STEP_RETRIES = 3          # retries before diagnostics
        self.MAX_DIAG_ROUNDS_PER_STEP = 2  # how many times we allow re-entering diagnostics for the same step
        
        # Artifact server — set PUBLIC_ARTIFACTS_URL env var from the configured port
        # so artifacts_service.py (which reads it at call time) uses the correct URL.
        _artifacts_url = f"http://{artifacts_host}:{artifacts_port}{artifacts_prefix}"
        if not os.environ.get("PUBLIC_ARTIFACTS_URL"):
            os.environ["PUBLIC_ARTIFACTS_URL"] = _artifacts_url
        self.artifacts_base_url = os.environ["PUBLIC_ARTIFACTS_URL"]
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
        Central LLM call with exponential backoff retry.
        Retries on transient errors (429, 503, connection issues) up to 4 attempts.
        """
        import time
        if verbose:
            prompt_txt = self._fmt_msgs(msgs)
            self._log(f"LLM REQUEST ({purpose})", prompt_txt, node=node)

        max_attempts = 4
        last_exc = None
        for attempt in range(max_attempts):
            try:
                resp = self.llm.invoke(msgs)
                if verbose:
                    self._log(f"LLM RESPONSE ({purpose})", getattr(resp, "content", str(resp)), node=node)
                return resp
            except Exception as exc:
                last_exc = exc
                err_str = str(exc).lower()
                # Retry on rate-limit, server errors, and transient connection issues
                retryable = any(k in err_str for k in (
                    "429", "503", "502", "rate limit", "too many requests",
                    "connection", "timeout", "temporarily unavailable",
                ))
                if not retryable or attempt == max_attempts - 1:
                    self._log(f"LLM ERROR ({purpose})", f"attempt={attempt+1} non-retryable: {exc}", node=node)
                    raise
                delay = (2 ** attempt) + (0.1 * attempt)
                self._log(f"LLM RETRY ({purpose})", f"attempt={attempt+1} retrying in {delay:.1f}s: {exc}", node=node)
                time.sleep(delay)
        raise last_exc
    
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
        import shutil as _shutil
        from genomeer.model.retriever import _CLI_TOOL_BINARIES
        _missing = [exe for exe in _CLI_TOOL_BINARIES.values() if not _shutil.which(exe)]
        _present = [exe for exe in _CLI_TOOL_BINARIES.values() if _shutil.which(exe)]
        self._log("ENV SCAN", body=f"CLI tools available: {_present}\nCLI tools ABSENT (filtered from registry): {_missing}", node="configure")

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
                # User likely provided the missing info in the latest message.
                # Clean route_hint NOW so it cannot propagate to downstream nodes
                # even if input_guard still finds something missing (it will re-set it).
                clean_manifest = dict(manifest)
                clean_manifest.pop("route_hint", None)
                clean_manifest.pop("qa_payload", None)
                self._log("RESUME", body="Pending missing inputs -> jump to orchestrator (manifest cleaned)", node=node)
                return {
                    "next_step": "orchestrator",
                    "manifest": clean_manifest,
                    "messages": [AIMessage(content="<observe>Resuming with your new inputs…</observe>")],
                }
            # -----------------------------
                
            user_prompt = state["messages"][-1].content
            _past_templates = self._load_past_templates(user_prompt)
            msgs = [
                self.system_prompt,
                HumanMessage(content=instructions.PLANNER_PROMPT.format(
                    temp_run_dir=state.get("run_temp_dir") or "",
                ) + (_past_templates or "")),
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

            # Multi-turn context fix (Bug 1): give the planner prior conversation
            # turns so step titles inherit context. Slicing off the last message
            # avoids duplicating the current user_prompt (appended just below).
            # First turn -> empty -> no-op (behavior identical to before).
            history = self._history_snippet(state["messages"][:-1]) if len(state["messages"]) > 1 else ""
            if history:
                msgs.append(HumanMessage(content=(
                    "CONVERSATION_HISTORY (previous turns for context):\n" + history
                )))

            msgs.append(HumanMessage(content=user_prompt))
            resp = self._llm_invoke(node, "plan_route", msgs)
            steps, route = StateGraphHelper.parse_checklist_and_route(resp.content)

            # Strip inline code from step titles.
            # Weak models embed code both with backticks (`code`) and without (after ': ').
            # NOTE: do NOT include bare () or [] in _CODE_SIGNALS — they appear in natural
            # English like "(number of contigs, total length)" and would wrongly strip
            # the entire step description down to just "Step N".
            _CODE_SIGNALS = re.compile(
                r'[;=]|'                               # assignment or semicolon
                r'\bimport\s|\bfrom\s+\w+\s+import\b|'  # import statements
                r'\bdef\s+\w+\s*\(|'                   # function definitions
                r'\w+\.\w+\s*\('                       # method calls like SeqIO.parse(
            )
            # Connectors that become dangling after backtick-block removal
            _DANGLING_END = re.compile(
                r"\s+\b(with|using|via|by|from|and|or|et|avec|en|pour|par|de|du|des"
                r"|the|a|an)\s*[.]?\s*$",
                re.I,
            )
            _DANGLING_START = re.compile(
                r"^\s*\b(and|or|with|et|ou|via|using|the)\b\s*",
                re.I,
            )
            # Mid-sentence orphan sequences left after backtick removal.
            # e.g. "using `Bio.SeqIO` and Python" → "using  and Python" → "and Python"
            # e.g. "using `ncbi-genome-download` with accession" → "using  with accession" → "with accession"
            # e.g. "save report to `ecoli.txt`." → "save report to ." → "save report"
            _MID_ORPHAN_PREP = re.compile(
                r"\b(using|via)\s+(with|by|and|or|,)\s*"
                r"|\b(with|by)\s+(and|or|,)\s*",
                re.I,
            )
            _TRAILING_PREP = re.compile(
                r"\b(to|in|at|from|de|du|en)\s*[.!?,]?\s*$",
                re.I,
            )

            def _clean_title(t: str) -> str:
                # 1. Replace backtick-quoted code blocks with just the first token
                # (the CLI command / tool name) so the Generator knows which tool to use.
                # e.g. `seqkit stats -a --tabular genome.fna` → seqkit
                #      `quast.py genome.fna -o quast_output` → quast.py
                #      `prodigal -i genome -p meta` → prodigal
                # Previously this removed the block entirely, losing the tool name.
                # Accession / ID patterns that must survive title cleaning.
                # These are critical for the Generator to use the right input.
                _ACCESSION_RX = re.compile(
                    r'\b(?:GCF|GCA|SRR|ERR|DRR|PRJ|SAM|SRS|SRX)'
                    r'[_\d]{5,20}(?:\.\d+)?\b'   # include version suffix e.g. GCF_000027325.1
                    r'|\b[A-Z]{2,3}\d{5,9}(?:\.\d+)?\b',  # e.g. NC_000913.3
                    re.IGNORECASE,
                )
                # Output file patterns critical for downstream steps (e.g. seqkit_stats.tsv)
                _OUTPUT_FILE_RX = re.compile(
                    r'\b[\w\-]+\.(?:tsv|txt|faa|fna|gff|gff3|fasta|bed|json|csv|png|html)\b',
                    re.IGNORECASE,
                )
                def _keep_tool(m: re.Match) -> str:
                    inner = m.group(0)[1:-1].strip()   # content without backticks
                    tokens = inner.split()
                    if not tokens:
                        return ""
                    tool = tokens[0]
                    extras = []
                    # Keep accession IDs (critical for downloads)
                    accessions = _ACCESSION_RX.findall(inner)
                    if accessions:
                        extras.append(f"accession: {', '.join(accessions)}")
                    # Keep output filenames (critical for inter-step contracts)
                    out_files = _OUTPUT_FILE_RX.findall(inner)
                    if out_files:
                        extras.append(f"output: {', '.join(dict.fromkeys(out_files))}")
                    if extras:
                        return f"{tool} ({'; '.join(extras)})"
                    return tool
                t = re.sub(r"`[^`]+`", _keep_tool, t)
                # 2. Collapse multiple spaces left by the removal
                t = re.sub(r" {2,}", " ", t)
                # 3. Clean mid-sentence orphans: "using and"→"and", "using with"→"with", "save to ."→"save"
                t = _MID_ORPHAN_PREP.sub(lambda m: (m.group(2) or m.group(4)) + " ", t)
                t = _TRAILING_PREP.sub("", t)
                # 4. Strip dangling connectors at end ("Load FASTA with ")
                t = _DANGLING_END.sub("", t)
                # 5. Strip dangling connectors at start ("and compute N50")
                t = _DANGLING_START.sub("", t)
                # 6. If ': ' separator exists and suffix looks like code, drop suffix
                if ': ' in t:
                    label, _, rest = t.partition(': ')
                    if _CODE_SIGNALS.search(rest):
                        t = label
                # 7. Drop trailing colon and tidy whitespace
                t = re.sub(r":\s*$", "", t.strip())
                return t.strip() or "Step"
            # Keep raw_title (pre-cleaning) so the Generator receives the full instruction.
            # _clean_title is used everywhere else (routing, validator, display).
            steps = [{**s, "title": _clean_title(s["title"]), "raw_title": s["title"]} for s in steps]

            # When routing to QA, suppress the planner's LLM draft from state messages.
            # Two reasons:
            # 1. The draft would appear as a spurious first response in the user stream.
            # 2. QA's _history_snippet would pick it up and generate "Based on recent history…"
            #    instead of a clean direct answer — causing the double-response bug.
            if route == "qa" or not steps:
                planner_msg = AIMessage(content=f"<log><route>{route}</route></log>")
            else:
                planner_msg = AIMessage(content=resp.content)

            updates = {
                "plan": steps,
                "current_idx": 0,
                "next_step": route,
                "messages": [planner_msg],
                "last_prompt": user_prompt,
            }
            if manifest.get("route_hint") == "await_user":
                updates["manifest"] = new_manifest

            # ── Multi-turn isolation (turn boundary reset) ─────────────────
            # When the planner re-runs for a NEW user turn, transient quality
            # data from the previous turn (observations, quality_signals) must
            # be cleared so the finalizer/observer don't mix turn-1 + turn-2
            # signals. Env-vars-preserved fields (env_name, attachments,
            # file_registry, tool_versions, etc.) are left untouched because
            # we mutate only the two known transient keys.
            # Also accumulate step_offset so the orchestrator's "<running
            # step=N/>" UI label keeps chronological numbering across turns
            # (turn-1 ended at step 2 -> turn-2 starts at step 3, not 1).
            # Kill-switch: GENOMEER_TURN_SCOPING=0 disables this fix.
            if os.environ.get("GENOMEER_TURN_SCOPING", "1") != "0":
                _current_turn = int(state.get("turn_id", 1) or 1)
                _base_manifest = updates.get("manifest") or dict(state.get("manifest") or {})
                if _current_turn != _base_manifest.get("last_planned_turn"):
                    _prev_plan_len = len(state.get("plan") or [])
                    _prev_offset = int(_base_manifest.get("step_offset", 0) or 0)
                    _new_offset = _prev_offset + _prev_plan_len
                    _base_manifest["observations"] = []
                    _base_manifest["quality_signals"] = {}
                    _base_manifest["last_planned_turn"] = _current_turn
                    _base_manifest["step_offset"] = _new_offset
                    updates["manifest"] = _base_manifest
                    self._log(
                        "TURN BOUNDARY",
                        body=(f"turn_id={_current_turn} -> reset observations + "
                              f"quality_signals; step_offset {_prev_offset}->{_new_offset} "
                              f"(prev_plan_len={_prev_plan_len})"),
                        node=node,
                    )

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

            # Clean routing keys from manifest before exiting QA — but ONLY for
            # terminal routes ("ask_for_missing", "finalize"). For "await_user",
            # route_hint MUST persist into the checkpoint so the NEXT user reply
            # (typically a button-click "Approved — please continue.") is detected
            # by the planner's await_user fast-path (L ~653). Without this guard,
            # human-in-the-loop silently breaks: user clicks "I agree" but the
            # planner sees no route_hint, falls through to normal LLM planning,
            # and the pipeline never resumes.
            clean_manifest = dict(state.get("manifest") or {})
            if route_hint != "await_user":
                clean_manifest.pop("route_hint", None)
                clean_manifest.pop("qa_payload", None)

            updates = {
                "next_step": next_step,
                "manifest": clean_manifest,
                "messages": [AIMessage(content=resp.content)],
            }
            self._log("EXIT NODE", body=f"next_step={next_step} route_hint_preserved={route_hint=='await_user'}", node=node)
            return updates

        def _orchestrator(self, state: AgentState) -> AgentState:
            node = "orchestrator"
            self._log("ENTER NODE", body=f"current_idx={state.get('current_idx')}\nplan_len={len(state.get('plan', []))}", node=node)

            # batch_mode: delegate to batch_orchestrator when strategy requires it
            _batch_mode     = state.get("batch_mode", False)
            _batch_strategy = state.get("batch_strategy") or ""
            _sample_manifest = state.get("sample_manifest") or []
            if _batch_mode and _sample_manifest and _batch_strategy in ("parallel", "sequential", "batch"):
                self._log("EXIT NODE", body=f"batch_mode=True strategy={_batch_strategy} samples={len(_sample_manifest)} → batch_orchestrator", node=node)
                return {
                    "next_step": "batch_orchestrator",
                    "messages": [AIMessage(content=f"<log>Batch mode activated ({_batch_strategy}, {len(_sample_manifest)} samples).</log>")],
                }

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
                    "messages": [AIMessage(content="<log>All steps complete. Finalizing…</log>")],
                }

            # otherwise go check inputs
            # Display step number = current_idx + manifest.step_offset + 1
            # so chronological order is preserved across multi-turn sessions.
            _disp_offset = int((state.get("manifest") or {}).get("step_offset", 0) or 0)
            _disp_step = idx + _disp_offset + 1
            self._log("EXIT NODE", body=f"all_done=False\ncurrent_idx={idx}\ndisp_step={_disp_step}\nnext_step=input_guard", node=node)
            return {
                "current_idx": idx,
                "next_step": "input_guard",
                "messages": [AIMessage(content=f"<running step={_disp_step}/>\n<description>\n{plan[idx]['title']}\n</description>\n")],
            }

        def _batch_orchestrator(self, state: AgentState) -> AgentState:
            """
            Multi-sample batch orchestrator.

            Reads sample_manifest from state, processes each sample in a
            separate thread (bounded by GENOMEER_BATCH_CONCURRENCY), collects
            per_sample_results, then routes to finalizer.
            """
            node = "batch_orchestrator"
            self._log("ENTER NODE", body="starting batch processing", node=node)

            samples: list = list(state.get("sample_manifest") or [])
            if not samples:
                self._log("EXIT NODE", body="sample_manifest empty → finalizer", node=node)
                return {
                    "next_step": "finalizer",
                    "messages": [AIMessage(content="<log>No samples in manifest — skipping batch.</log>")],
                }

            # --- concurrency / RAM config ---
            _concurrency = max(1, int(os.environ.get("GENOMEER_BATCH_CONCURRENCY", "2")))
            _total_ram   = float(os.environ.get("GENOMEER_MAX_RAM_GB", "0") or "0")
            _per_worker_ram = round(_total_ram / _concurrency, 2) if _total_ram > 0 else None

            semaphore     = threading.Semaphore(_concurrency)
            results_lock  = threading.Lock()
            progress_lock = threading.Lock()

            per_sample: Dict[str, Any] = dict(state.get("per_sample_results") or {})
            completed_count = [0]

            def process_sample(sample: Dict[str, Any]) -> Dict[str, Any]:
                """Run one sample through the inner pipeline (thread-safe clone)."""
                sample_id  = str(sample.get("sample_id") or sample.get("id") or "unknown")
                cancel_evt = state.get("_cancel_event")

                with semaphore:
                    if cancel_evt is not None and getattr(cancel_evt, "is_set", lambda: False)():
                        return {"sample_id": sample_id, "status": "cancelled", "error": "cancelled before start"}

                    # --- deep-clone state for isolation ---
                    local_state = copy.deepcopy(dict(state))
                    local_state["current_sample_id"]  = sample_id
                    local_state["current_sample_idx"] = samples.index(sample)
                    local_state["manifest"]           = copy.deepcopy(dict(state.get("manifest") or {}))
                    local_state["manifest"]["quality_signals"]  = {}
                    local_state["manifest"]["observations"]     = []
                    local_state["run_started_at"]     = time.time()
                    local_state["retry_counts"]       = {}
                    local_state["diagnostic_mode"]    = False
                    local_state["diagnostic_code"]    = None
                    local_state["diagnostic_observation"] = None
                    if _per_worker_ram is not None:
                        local_state["_per_worker_ram_gb"] = _per_worker_ram

                    # propagate sample-specific fields into manifest
                    local_state["manifest"]["sample_id"] = sample_id
                    for k, v in sample.items():
                        if k not in ("sample_id", "id"):
                            local_state["manifest"][k] = v

                    self._log("BATCH SAMPLE START", body=f"sample_id={sample_id}", node=node)

                    # --- inner sequential pipeline ---
                    _PIPELINE = ["input_guard", "generator", "ensure_env", "executor", "observer"]
                    _step_idx = 0

                    while _step_idx < len(_PIPELINE):
                        if cancel_evt is not None and getattr(cancel_evt, "is_set", lambda: False)():
                            return {"sample_id": sample_id, "status": "cancelled", "error": "cancelled mid-run"}

                        step_name = _PIPELINE[_step_idx]
                        try:
                            step_fn = getattr(self, step_name, None)
                            if step_fn is None:
                                _step_idx += 1
                                continue
                            result = step_fn(local_state)
                            local_state.update(result)
                        except Exception as _exc:
                            self._log(
                                "BATCH SAMPLE STEP ERROR",
                                body=f"sample_id={sample_id} step={step_name} error={_exc}",
                                node=node,
                            )
                            return {
                                "sample_id": sample_id,
                                "status": "error",
                                "error": f"{step_name}: {_exc}",
                            }

                        # routing within inner pipeline
                        _next = local_state.get("next_step", "")
                        if _next == "diagnostics":
                            # run diagnostics inline then re-enter generator
                            try:
                                diag_result = self.diagnostics(local_state)
                                local_state.update(diag_result)
                            except Exception as _de:
                                self._log("BATCH DIAG ERROR", body=str(_de), node=node)
                            _step_idx = _PIPELINE.index("generator")
                            continue
                        if _next in ("orchestrator", "finalizer", "qa"):
                            break
                        _step_idx += 1

                    # --- collect results ---
                    _manifest_out = local_state.get("manifest") or {}
                    return {
                        "sample_id":        sample_id,
                        "status":           "done",
                        "quality_signals":  _manifest_out.get("quality_signals", {}),
                        "amr_genes_detected": _manifest_out.get("amr_genes_detected", []),
                        "observations":     _manifest_out.get("observations", []),
                        "retry_counts":     local_state.get("retry_counts", {}),
                        "last_result":      (local_state.get("last_result") or "")[:2000],
                    }

            # --- launch all samples with ThreadPoolExecutor ---
            futures_map: Dict = {}
            with ThreadPoolExecutor(max_workers=_concurrency) as executor_pool:
                for sample in samples:
                    fut = executor_pool.submit(process_sample, sample)
                    futures_map[fut] = str(sample.get("sample_id") or sample.get("id") or "unknown")

                for fut in as_completed(futures_map):
                    sid = futures_map[fut]
                    try:
                        result = fut.result()
                    except Exception as exc:
                        self._log("BATCH FUTURE ERROR", body=f"sample_id={sid} exc={exc}", node=node)
                        result = {"sample_id": sid, "status": "error", "error": str(exc)}

                    with results_lock:
                        per_sample[sid] = result

                    with progress_lock:
                        completed_count[0] += 1
                        self._log(
                            "BATCH PROGRESS",
                            body=f"completed={completed_count[0]}/{len(samples)} sample_id={sid} status={result.get('status')}",
                            node=node,
                        )

            self._log(
                "EXIT NODE",
                body=f"all_samples_done={len(per_sample)}/{len(samples)} → finalizer",
                node=node,
            )
            return {
                "per_sample_results": per_sample,
                "next_step": "finalizer",
                "messages": [AIMessage(content=f"<log>Batch complete: {len(per_sample)}/{len(samples)} samples processed.</log>")],
            }

        def _input_guard(self, state: AgentState) -> AgentState:
            node = "input_guard"
            step = state["plan"][state["current_idx"]]
            current_step_title = step["title"].strip()
            user_goal = state.get("last_prompt") or (state["messages"][0].content if state.get("messages") else "")
            manifest = dict(state.get("manifest") or {})

            # current run storage home lsdir
            temp_dir = state.get("run_temp_dir") or ""

            # Collect text to scan for absolute paths:
            # - original task prompt (last_prompt)
            # - EVERY subsequent human message (user may provide file paths in follow-up answers)
            import re as _re
            _texts_to_scan = [user_goal]
            _last_prompt_content = state.get("last_prompt") or ""
            for _m in (state.get("messages") or []):
                if getattr(_m, "type", "") == "human":
                    _mc = getattr(_m, "content", "") or ""
                    if _mc != _last_prompt_content and _mc.strip():
                        _texts_to_scan.append(_mc)
            _all_text = "\n".join(_texts_to_scan)

            # Extract absolute paths (Windows and Unix, quoted or bare, spaces allowed).
            _quoted_win  = _re.findall(r'["\']([A-Za-z]:[/\\][^"\']+)["\']', _all_text)
            _bare_win    = _re.findall(r'(?<!["\'/\\])([A-Za-z]:[/\\]\S+)', _all_text)
            _quoted_unix = _re.findall(r'["\'](/(?:[^"\']+))["\']', _all_text)
            # Bare Unix paths with bio extensions (allow spaces — common on /mnt/c/ WSL mounts).
            _bio_ext = r'(?:fasta|fna|fastq|fa|fq|tsv|gff|gff3|faa|bam|vcf|bed|txt|csv|json|gz|png|pdf)'
            _bare_unix_bio = [
                m.group(1).strip()
                for m in _re.finditer(
                    r'(/[^\n"\']+\.' + _bio_ext + r')\b',
                    _all_text, _re.IGNORECASE
                )
            ]
            _abs_paths = list(dict.fromkeys(
                p.rstrip('.,;:)>]}') for p in (
                    _quoted_win + _bare_win + _quoted_unix + _bare_unix_bio
                )
                if p.rstrip('.,;:)>]}')
            ))
            files = self._list_ctx_files(temp_dir, extra_paths=_abs_paths)
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

            # Fix 9 — append filesystem helper reference to INPUT_GUARD context
            try:
                from genomeer.utils.filesystem import FILESYSTEM_PROMPT_SNIPPET as _FS_SNIPPET_IG
                context_block += "\n\n" + _FS_SNIPPET_IG
            except ImportError:
                pass

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
                # Start from a copy, then explicitly remove stale routing keys.
                # If route_hint="ask_for_missing" from a previous iteration survives here,
                # the planner will intercept it and send the pipeline to QA→END even though
                # all inputs are present. Popping them is the only safe fix.
                new_manifest = dict(state.get("manifest") or {})
                new_manifest.pop("route_hint", None)
                new_manifest.pop("qa_payload", None)
                new_manifest["input_state"] = {
                    "summary": [m for m in items],
                    "root_dir": temp_dir,
                    "files": files,
                    "guidance": (
                        "Use either the initial user prompt or the 'files' list "
                        "to decide what inputs to use for code generation in this step."
                    ),
                }
                self._log("INPUTS OK", body="No missing items", node=node)
                self._log("EXIT NODE", body="next_step=generator", node=node)
                return {
                    "manifest": new_manifest,
                    "next_step": "generator",
                    "messages": [AIMessage(content=f"<log>{resp.content}</log>")],
                }
        
        def _generator(self, state: AgentState) -> AgentState:
            node = "generator"
            step = state["plan"][state["current_idx"]]
            env_name = state["env_name"]
            
            # detect repair mode
            manifest = state.get("manifest", {}) or {}
            repair_feedback = manifest.get("repair_feedback")
            is_diagnostic = isinstance(repair_feedback, str) and repair_feedback.strip().upper().startswith("DIAGNOSTICS_REQUEST:")
            temp_dir = state.get("run_temp_dir") or ""
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
                    # Add line numbers to PREVIOUS_CODE so the LLM can pinpoint
                    # the exact faulty line in repair context instead of regenerating
                    # from memory (which causes identical broken code on every retry).
                    _raw_code = (state.get("pending_code") or "").strip()
                    _numbered = "\n".join(
                        f"{i+1:4d}: {line}"
                        for i, line in enumerate(_raw_code.splitlines())
                    )
                    content = instructions.GENERATOR_REPAIR_CTX_PROMPT.format(
                        user_goal=state['last_prompt'],
                        current_step_title=step.get('raw_title') or step['title'],
                        manifest=manifest.get("input_state"),
                        run_temp_dir=temp_dir,
                        repair_feedback=repair_feedback,
                        previous_code=_numbered,
                        last_result=(state.get("last_result") or "").strip(),
                        files_str=files_str,
                    )
            else:
                prompt = instructions.GENERATOR_PROMPT
                content = instructions.GENERATOR_CTX_PROMPT.format(
                    user_goal=state['last_prompt'],
                    current_step_title=step.get('raw_title') or step['title'],
                    manifest=state['manifest'].get("input_state"),
                    run_temp_dir=state.get('run_temp_dir') or "",
                )

            # Inject bio_hint context when available (bio_hint node ran before this call)
            if not is_diagnostic:
                _bio_hint = (manifest.get("bio_hint")) or state.get("bio_hint")
                if _bio_hint:
                    content += instructions.BIO_HINT_CONTEXT_BLOCK.format(bio_hint=_bio_hint)

            # Dynamically inject code pattern snippets when the step involves known-hard patterns.
            # Small models (llama3:8b) reliably ignore rules in the general prompt but DO follow
            # examples placed immediately before the task. This is the reliable fix for N50 / SeqIO.
            _step_ctx = f"{step['title']} {state.get('last_prompt', '')}".lower()
            # Use step title ONLY for tool-specific injections (prodigal, quast) so that
            # mentioning those tools in OTHER steps of the user prompt doesn't spuriously
            # inject the wrong code pattern into the wrong step.
            _step_title_ctx = step['title'].lower()
            _injections = []

            if any(k in _step_ctx for k in ("n50", "assembly stat", "contig stat", "scaffold stat",
                                             "sequence stat", "assembly metric", "stats")):
                _injections.append(
                    'REQUIRED — copy this N50 pattern exactly (no walrus :=, no None placeholder):\n'
                    '    lengths = sorted([len(r.seq) for r in contigs], reverse=True)\n'
                    '    total = sum(lengths)\n'
                    '    cumsum, n50 = 0, 0\n'
                    '    for l in lengths:\n'
                    '        cumsum += l\n'
                    '        if cumsum >= total / 2:\n'
                    '            n50 = l\n'
                    '            break\n'
                    '    print(f"N50: {n50}")'
                )

            if any(k in _step_ctx for k in ("seqio", "fasta", "fastq", "parse", "sequence", "contig", "read")):
                _injections.append(
                    'REQUIRED — always materialise SeqIO.parse into a list before any use:\n'
                    '    contigs = list(SeqIO.parse(fasta_path, "fasta"))  # ONE call, then reuse the list'
                )

            if any(k in _step_ctx for k in ("gc", "gc content", "gc%", "gc percent", "base composition")):
                _injections.append(
                    # Use single quotes for 'G'/'C' so the snippet is safe inside any f-string delimiter
                    "REQUIRED — GC content: count actual G and C bases, NEVER divide by 4:\n"
                    "    gc_count = sum(s.seq.count('G') + s.seq.count('C') for s in contigs)\n"
                    "    total_bases = sum(len(s.seq) for s in contigs)\n"
                    "    gc_pct = gc_count / total_bases * 100 if total_bases else 0.0\n"
                    "    print(f'GC content: {gc_pct:.2f}%')\n"
                    "WRONG (never do this): gc = total_length / (4 * num_contigs)"
                )

            if any(k in _step_ctx for k in ("ncbi", "ncbi-genome-download", "genome-download",
                                             "download genome", "download assembly",
                                             "download bacteria", "download organism",
                                             "entrez", "taxid", "taxon")):
                # Detect whether an assembly accession (GCF_ / GCA_) is already known.
                # If yes: inject the accession-based snippet as executable code.
                # If no:  inject the --genera snippet (with --assembly-levels complete).
                # Small models copy the FIRST runnable code block they see — so only
                # the correct path must appear as real code; the other is omitted.
                _has_accession = bool(re.search(r"\bGC[FA]_\d+", _step_ctx, re.I))

                if _has_accession:
                    # Extract the accession string for the snippet
                    _acc_match = re.search(r"\bGC[FA]_\d+(?:\.\d+)?", _step_ctx, re.I)
                    _acc = _acc_match.group(0).upper() if _acc_match else "GCF_XXXXXXXXX.X"
                    _injections.append(
                        "REQUIRED — use --assembly-accessions (accession is known — DO NOT use --genera):\n"
                        "\n"
                        "  import subprocess, glob, os, gzip, shutil, sys\n"
                        "\n"
                        f'  run_dir = r"{temp_dir}"  # output folder — do not change\n'
                        f'  accession = "{_acc}"  # use this exact accession\n'
                        '  cmd = ["ncbi-genome-download",\n'
                        '         "--assembly-accessions", accession,\n'
                        '         "--formats", "fasta",\n'
                        '         "--flat-output",\n'
                        '         "--output-folder", run_dir,\n'
                        '         "bacteria"]\n'
                        "\n"
                        "  res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)\n"
                        "  print(res.stdout or res.stderr)\n"
                        "  if res.returncode != 0:\n"
                        '      print(f"Download failed (exit {res.returncode}): {res.stderr}")\n'
                        "      sys.exit(1)\n"
                        "\n"
                        '  # Prefer uncompressed .fna; fall back to .fna.gz if needed\n'
                        '  fasta_files = (glob.glob(os.path.join(run_dir, "*.fna")) +\n'
                        '                 glob.glob(os.path.join(run_dir, "*.fna.gz")) +\n'
                        '                 glob.glob(os.path.join(run_dir, "**", "*.fna"), recursive=True))\n'
                        "  if not fasta_files:\n"
                        '      print("No FASTA file found after download")\n'
                        "      sys.exit(1)\n"
                        "\n"
                        "  fasta_path = fasta_files[0]\n"
                        '  if fasta_path.endswith(".gz"):\n'
                        "      unzipped = fasta_path[:-3]\n"
                        "      if not os.path.exists(unzipped):\n"
                        "          with gzip.open(fasta_path, 'rb') as fi, open(unzipped, 'wb') as fo:\n"
                        "              shutil.copyfileobj(fi, fo)\n"
                        "      fasta_path = unzipped\n"
                        '  print(f"FASTA ready: {fasta_path}")\n'
                        "\n"
                        "  WRONG: --genus  --species  --organism  --name  (do not exist)"
                    )
                else:
                    _injections.append(
                        "REQUIRED — complete ncbi-genome-download pattern (copy and adapt):\n"
                        "\n"
                        "  import subprocess, glob, os, gzip, shutil, sys\n"
                        "\n"
                        f'  run_dir = r"{temp_dir}"  # output folder — do not change\n'
                        "  # WARNING: ALWAYS include --assembly-levels complete.\n"
                        "  # Without it, ncbi-genome-download lists ALL assemblies for the kingdom\n"
                        "  # (thousands of files) and hangs for hours.\n"
                        "  organism = \"Escherichia coli\"   # adapt to user request\n"
                        '  cmd = ["ncbi-genome-download",\n'
                        '         "--genera", organism,\n'
                        '         "--assembly-levels", "complete",\n'
                        '         "--section", "refseq",\n'
                        '         "--formats", "fasta",\n'
                        '         "--output-folder", run_dir,\n'
                        '         "--flat-output",\n'
                        '         "bacteria"]\n'
                        "\n"
                        "  dry = subprocess.run(cmd + [\"--dry-run\"], capture_output=True, text=True, timeout=60)\n"
                        "  print(\"Dry-run:\", dry.stdout or dry.stderr)\n"
                        "  if dry.returncode != 0:\n"
                        '      print(f"Dry-run failed: {dry.stderr}")\n'
                        "      sys.exit(1)\n"
                        "\n"
                        "  res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)\n"
                        "  print(res.stdout or res.stderr)\n"
                        "  if res.returncode != 0:\n"
                        '      print(f"Download failed (exit {res.returncode}): {res.stderr}")\n'
                        "      sys.exit(1)\n"
                        "\n"
                        '  # Prefer uncompressed .fna; fall back to .fna.gz if needed\n'
                        '  fasta_files = (glob.glob(os.path.join(run_dir, "*.fna")) +\n'
                        '                 glob.glob(os.path.join(run_dir, "*.fna.gz")) +\n'
                        '                 glob.glob(os.path.join(run_dir, "**", "*.fna"), recursive=True))\n'
                        "  if not fasta_files:\n"
                        '      print("No FASTA files found after download")\n'
                        "      sys.exit(1)\n"
                        "\n"
                        "  fasta_path = fasta_files[0]\n"
                        '  if fasta_path.endswith(".gz"):\n'
                        "      unzipped = fasta_path[:-3]\n"
                        "      if not os.path.exists(unzipped):\n"
                        "          with gzip.open(fasta_path, 'rb') as fi, open(unzipped, 'wb') as fo:\n"
                        "              shutil.copyfileobj(fi, fo)\n"
                        "      fasta_path = unzipped\n"
                        '  print(f"FASTA ready: {fasta_path}")\n'
                        "\n"
                        "  WRONG flags that DO NOT EXIST: --genus  --species  --organism  --name"
                    )

            # Prodigal injection — triggered on step title only (not full user prompt)
            # so that mentioning prodigal in step 4 doesn't inject into steps 1-3.
            if any(k in _step_title_ctx for k in ("prodigal", "orf pred", "gene pred", "gene call", "orf call")):
                _injections.append(
                    "THIS STEP RUNS PRODIGAL. The -f gff flag is MANDATORY — without it, Prodigal\n"
                    "writes its native Genbank-like format and GFF parsers will count 0 CDS.\n"
                    "\n"
                    "  import subprocess, os\n"
                    "  gff_path     = os.path.join(run_dir, 'genes.gff')\n"
                    "  protein_path = os.path.join(run_dir, 'predicted_proteins.faa')\n"
                    "  # For an isolate genome:\n"
                    "  cmd = ['prodigal', '-i', fasta_path, '-a', protein_path,\n"
                    "         '-o', gff_path, '-f', 'gff', '-p', 'single']\n"
                    "  # For a metagenome: replace '-p', 'single' with '-p', 'meta'\n"
                    "  result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)\n"
                    "  print(result.stdout[-2000:] or result.stderr[-1000:])\n"
                    "  if result.returncode != 0:\n"
                    "      import sys; sys.exit(f'Prodigal failed: {result.stderr}')\n"
                    "\n"
                    "  # Count CDS features — ONLY from GFF lines (not # comment lines):\n"
                    "  orf_count = sum(1 for l in open(gff_path)\n"
                    "                  if not l.startswith('#') and '\\t' in l\n"
                    "                  and l.split('\\t')[2] == 'CDS')\n"
                    "  protein_count = sum(1 for l in open(protein_path) if l.startswith('>'))\n"
                    "  print(f'Predicted proteins: {protein_count}')\n"
                    "  print(f'CDS features in GFF: {orf_count}')\n"
                    "\n"
                    "  WRONG: omitting -f gff  → Genbank format → 0 CDS parsed\n"
                    "  WRONG: ['prodigal', '-i', fa, '-a', prot, '-o', gff, '-p', 'single']  ← no -f gff!"
                )

            # QUAST injection — triggered on step title only
            if any(k in _step_title_ctx for k in ("quast", "assembly qc", "assembly quality")):
                _injections.append(
                    "THIS STEP RUNS QUAST. Binary is quast.py — NOT quast, NOT seqkit.\n"
                    "Do NOT run seqkit in this step — seqkit was already run in a previous step.\n"
                    "\n"
                    "  import subprocess, os\n"
                    "  quast_dir = os.path.join(run_dir, 'quast_output')\n"
                    "  result = subprocess.run(\n"
                    "      ['quast.py', '-o', quast_dir, fasta_path],\n"
                    "      capture_output=True, text=True, timeout=300)\n"
                    "  print(result.stdout[-2000:] or result.stderr[-500:])\n"
                    "  if result.returncode != 0:\n"
                    "      import sys; sys.exit(f'quast.py failed: {result.stderr}')\n"
                    "\n"
                    "  # Parse quast_output/report.tsv — KEY-VALUE file (NOT a header-row CSV):\n"
                    "  report_path = os.path.join(quast_dir, 'report.tsv')\n"
                    "  stats = {}\n"
                    "  with open(report_path) as _f:\n"
                    "      for _line in _f:\n"
                    "          if not _line.strip(): continue\n"
                    "          _parts = _line.rstrip().split('\\t')\n"
                    "          if len(_parts) >= 2:\n"
                    "              stats[_parts[0].strip()] = _parts[1].strip()\n"
                    "  n50      = stats.get('N50', 'NA')\n"
                    "  contigs  = next((v for k, v in stats.items() if k.startswith('# contigs')), 'NA')\n"
                    "  print(f'QUAST N50: {n50}')\n"
                    "  print(f'QUAST contigs: {contigs}')\n"
                    "\n"
                    "  WRONG: ['quast', ...]  ← FileNotFoundError\n"
                    "  WRONG: running seqkit in this step — it was already done"
                )

            # FastQC injection — triggered on step title only
            if any(k in _step_title_ctx for k in ("fastqc", "fast qc", "quality control report", "qc report")):
                _injections.append(
                    "THIS STEP RUNS FASTQC. FastQC requires the output directory to already exist.\n"
                    "ALWAYS call os.makedirs(out_dir, exist_ok=True) BEFORE calling fastqc.\n"
                    "\n"
                    "  import os, subprocess, sys\n"
                    "  out_dir = os.path.join(run_dir, 'fastqc_raw')\n"
                    "  os.makedirs(out_dir, exist_ok=True)  # REQUIRED — fastqc errors if dir missing\n"
                    "  result = subprocess.run(\n"
                    "      ['fastqc', '-o', out_dir, r1_path, r2_path],\n"
                    "      capture_output=True, text=True, timeout=300)\n"
                    "  if result.returncode != 0:\n"
                    "      sys.exit(f'FastQC failed: {result.stderr}')\n"
                    "  print(f'FastQC done. Reports in: {out_dir}')\n"
                    "\n"
                    "  WRONG: forgetting os.makedirs() → 'Specified output directory does not exist'"
                )

            # bbduk.sh injection — server has BBMap 39.01 in meta-env1; the standard
            # Illumina adapters reference path is fixed. Without this hint the LLM
            # hesitates and emits meta-format text instead of code.
            if any(k in _step_title_ctx for k in ("bbduk", "bb duk", "adapter detect",
                                                   "adapter screen", "adapter check",
                                                   "bbtools adapter")):
                _injections.append(
                    "THIS STEP USES bbduk.sh (BBMap 39.01) FOR ADAPTER DETECTION.\n"
                    "The Illumina adapter reference is pre-installed at a FIXED path on this server:\n"
                    "  /home/workshop/.bioagentpkg/runtime/pkgs/envs/meta-env1/opt/bbmap-39.01-1/resources/adapters.fa\n"
                    "DO NOT search /usr/share/bbmap, /opt/bbmap, or other locations — they do not exist.\n"
                    "DO NOT pass ref='adapters' as a keyword — the absolute path is required here.\n"
                    "\n"
                    "  import subprocess, os, sys\n"
                    "  ADAPTERS_FA = '/home/workshop/.bioagentpkg/runtime/pkgs/envs/meta-env1/opt/bbmap-39.01-1/resources/adapters.fa'\n"
                    "  stats_out = os.path.join(run_dir, 'adapter_stats.tsv')\n"
                    "  cmd = ['bbduk.sh',\n"
                    "         f'in={os.path.join(run_dir, \"raw_R1.fastq\")}',\n"
                    "         f'ref={ADAPTERS_FA}',\n"
                    "         f'stats={stats_out}',\n"
                    "         'k=23', 'mink=11', 'hdist=1',\n"
                    "         f'out={os.path.join(run_dir, \"bbduk_clean_R1.fastq\")}']\n"
                    "  res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)\n"
                    "  if res.returncode != 0:\n"
                    "      sys.exit(f'bbduk.sh failed: {res.stderr}')\n"
                    "  if not os.path.exists(stats_out) or os.path.getsize(stats_out) == 0:\n"
                    "      sys.exit(f'bbduk stats file missing: {stats_out}')\n"
                    "  print(f'Adapter detection done: {stats_out}')\n"
                    "  # adapter_stats.tsv format: comment lines start with '#'; data lines are\n"
                    "  # tab-separated: name<TAB>reads<TAB>reads_pct<TAB>bases<TAB>bases_pct\n"
                    "\n"
                    "  WRONG: ref='adapters' (keyword) — works on some BBTools installs, not here\n"
                    "  WRONG: omitting ref= entirely — bbduk requires it for adapter detection\n"
                    "  WRONG: using ref=/usr/share/bbmap/resources/adapters.fa — wrong path\n"
                    "  NOTE: bbduk also writes a 'cleaned' FASTQ (out=) but for stats-only mode,\n"
                    "        you can keep or delete it after; the key output is stats=<file>.tsv."
                )

            # fastp + wgsim injection — triggered when fastp step follows a wgsim simulation.
            # wgsim assigns quality scores of ~10-15; --qualified_quality_phred 20 filters ALL reads.
            if (any(k in _step_title_ctx for k in ("fastp", "trim", "adapter trim", "quality trim"))
                    and any(k in _step_ctx for k in ("wgsim", "simulated read", "simulate read",
                                                     "simul", "synthetic read", "artificial read"))):
                _injections.append(
                    "IMPORTANT — WGSIM READS + FASTP: wgsim assigns quality scores of ~10-15.\n"
                    "Using --qualified_quality_phred 20 (fastp default) will discard ALL reads.\n"
                    "ALWAYS add --disable_quality_filtering when running fastp on wgsim-simulated reads.\n"
                    "\n"
                    "  cmd = ['fastp',\n"
                    "         '-i', r1_path, '-I', r2_path,\n"
                    "         '-o', trim_r1, '-O', trim_r2,\n"
                    "         '--json', json_report, '--html', html_report,\n"
                    "         '--disable_quality_filtering',  # wgsim reads have no real quality scores\n"
                    "         '--length_required', '50']\n"
                    "\n"
                    "  WRONG: '--qualified_quality_phred', '20' without --disable_quality_filtering\n"
                    "        → reads passed filter: 0 — entire output is empty"
                )

            # minimap2 → samtools mapping injection — triggered on step title only.
            # The pipe pattern (minimap2 | samtools sort) FAILS in the installed samtools:
            #   samtools view -b - → "[main_samview] fail to read the header from '-'"
            #   Popen pipe → samtools sort exits early → minimap2 gets SIGPIPE → "minimap2 failed"
            # The only reliable approach is: minimap2 → SAM file → samtools view -bS → BAM → sort → index
            if any(k in _step_title_ctx for k in ("minimap2", "map reads", "read mapping",
                                                   "read alignment", "coverage depth",
                                                   "bam", "samtools", "jgi_summarize")):
                _injections.append(
                    "THIS STEP MAPS READS WITH MINIMAP2 + SAMTOOLS.\n"
                    "CRITICAL — use the SAM-file approach. The pipe pattern FAILS in this environment:\n"
                    "  samtools view -b - fails with 'fail to read the header from -'\n"
                    "  Popen pipe: samtools sort exits early → minimap2 SIGPIPE → 'minimap2 failed'\n"
                    "ALWAYS write minimap2 output to a SAM file, then convert with -bS, then sort.\n"
                    "\n"
                    "  import subprocess, os, sys\n"
                    "  sam_path    = os.path.join(run_dir, 'reads_aligned.sam')\n"
                    "  bam_path    = os.path.join(run_dir, 'reads_aligned.bam')\n"
                    "  sorted_bam  = os.path.join(run_dir, 'reads_aligned.sorted.bam')\n"
                    "\n"
                    "  # Step 1: minimap2 → SAM file\n"
                    "  with open(sam_path, 'w') as _sam_f:\n"
                    "      res = subprocess.run(\n"
                    "          ['minimap2', '-ax', 'sr', contig_fa, trim_r1, trim_r2],\n"
                    "          stdout=_sam_f, stderr=subprocess.PIPE, timeout=600)\n"
                    "  if res.returncode != 0:\n"
                    "      sys.exit(f'minimap2 failed: {res.stderr.decode()}')\n"
                    "\n"
                    "  # Step 2: SAM → BAM  (-bS: -b=output BAM, -S=input is SAM — required here)\n"
                    "  # CRITICAL: -o flag may not work in this samtools version — it outputs BAM to stdout.\n"
                    "  # BAM is binary (gzip-compressed); NEVER use text=True or capture_output=True here.\n"
                    "  # Redirect stdout to the bam file using stdout=open(bam_path, 'wb').\n"
                    "  with open(bam_path, 'wb') as _bam_f:\n"
                    "      res = subprocess.run(\n"
                    "          ['samtools', 'view', '-bS', sam_path],\n"
                    "          stdout=_bam_f, stderr=subprocess.PIPE, timeout=300)\n"
                    "  if res.returncode != 0:\n"
                    "      sys.exit(f'samtools view failed: {res.stderr.decode(errors=\"replace\")}')\n"
                    "  if not os.path.exists(bam_path) or os.path.getsize(bam_path) == 0:\n"
                    "      sys.exit('samtools view produced empty BAM')\n"
                    "\n"
                    "  # Step 3: sort BAM — this is the OLD samtools (v0.x) syntax:\n"
                    "  #   samtools sort <in.bam> <out.prefix>  →  creates <out.prefix>.bam\n"
                    "  # In this old version, -o is a FLAG with NO argument (means 'output to stdout'),\n"
                    "  # NOT '-o output.bam' like modern samtools. NEVER use -o here.\n"
                    "  # Pass the prefix WITHOUT the .bam extension; samtools appends .bam automatically.\n"
                    "  sorted_prefix = sorted_bam[:-4] if sorted_bam.endswith('.bam') else sorted_bam\n"
                    "  res = subprocess.run(\n"
                    "      ['samtools', 'sort', bam_path, sorted_prefix],\n"
                    "      stderr=subprocess.PIPE, timeout=300)\n"
                    "  if res.returncode != 0:\n"
                    "      sys.exit(f'samtools sort failed: {res.stderr.decode(errors=\"replace\")}')\n"
                    "\n"
                    "  if not os.path.exists(sorted_bam) or os.path.getsize(sorted_bam) == 0:\n"
                    "      sys.exit('Sorted BAM not created or empty')\n"
                    "\n"
                    "  # Step 4: index (creates sorted_bam + '.bai', no binary stdout)\n"
                    "  subprocess.run(['samtools', 'index', sorted_bam],\n"
                    "      stderr=subprocess.PIPE, check=True, timeout=120)\n"
                    "  print(f'Sorted and indexed BAM: {sorted_bam}')\n"
                    "\n"
                    "  # Step 5: jgi_summarize_bam_contig_depths\n"
                    "  # DO NOT call jgi_summarize_bam_contig_depths --version — it segfaults.\n"
                    "  # stdout is text; redirect to PIPE or DEVNULL to keep it out of Python stdout.\n"
                    "  depth_path = os.path.join(run_dir, 'depth.txt')\n"
                    "  res = subprocess.run(\n"
                    "      ['jgi_summarize_bam_contig_depths', '--outputDepth', depth_path, sorted_bam],\n"
                    "      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300)\n"
                    "  if res.returncode != 0:\n"
                    "      sys.exit(f'jgi_summarize_bam_contig_depths failed: {res.stderr.decode(errors=\"replace\")}')\n"
                    "  print(f'Depth file: {depth_path}')\n"
                    "\n"
                    "  WRONG: samtools sort bam -o sorted.bam → in old samtools, -o = stdout flag (no arg) → fails\n"
                    "  WRONG: samtools sort bam (no output) → 'Usage: samtools sort <in.bam> <out.prefix>'\n"
                    "  WRONG: samtools view -bS sam -o bam + capture_output=True/text=True\n"
                    "         → -o flag ignored, BAM written to stdout, UnicodeDecodeError (0x8b = gzip)\n"
                    "  WRONG: Popen pipe minimap2 | samtools sort → SIGPIPE, minimap2 fails\n"
                    "  WRONG: omitting -S in samtools view -bS → samtools may reject SAM input\n"
                    "  NOTE: samtools --version exits with code 1 on some versions — NORMAL, not missing.\n"
                    "  NOTE: jgi_summarize_bam_contig_depths --version segfaults — never call it."
                )

            # MetaBAT2 binning injection — triggered on step title only.
            # MetaBAT2 has a HARD minimum of 1500 bp for -m/--minContig regardless of what
            # the user requests. Setting -m below 1500 → "Contig length < 1500 is not allowed".
            # Also: --minContig cannot be passed twice (boost::program_options crashes).
            if any(k in _step_title_ctx for k in ("metabat", "binning", "contig binning", " bin ", "bin contigs")):
                _injections.append(
                    "THIS STEP RUNS METABAT2 FOR CONTIG BINNING.\n"
                    "CRITICAL CONSTRAINTS for this MetaBAT2 version (2.12.1):\n"
                    "  1. -m / --minContig MUST be >= 1500. The tool hard-rejects anything lower with:\n"
                    "       '[Error!] Contig length < 1500 is not allowed to be used for binning.'\n"
                    "     If the user/plan requests a value below 1500 (e.g. 200), OVERRIDE it to 1500.\n"
                    "  2. NEVER pass --minContig AND -m together — boost throws 'multiple_occurrences'.\n"
                    "     Use ONE of them only (prefer -m for short flag).\n"
                    "  3. --minContigLen does NOT exist — only -m / --minContig.\n"
                    "  4. depth.txt must be the output of jgi_summarize_bam_contig_depths (NOT cvExt format).\n"
                    "\n"
                    "  import subprocess, os, sys, glob\n"
                    "  contig_fa  = os.path.join(run_dir, 'megahit_output', 'final.contigs.fa')\n"
                    "  depth_path = os.path.join(run_dir, 'depth.txt')\n"
                    "  bins_dir   = os.path.join(run_dir, 'bins')\n"
                    "  os.makedirs(bins_dir, exist_ok=True)\n"
                    "\n"
                    "  # Force min contig length to MetaBAT2's hard minimum of 1500.\n"
                    "  # User may request lower; this is non-negotiable for the tool to run.\n"
                    "  cmd = ['metabat2',\n"
                    "         '-i', contig_fa,\n"
                    "         '-a', depth_path,\n"
                    "         '-m', '1500',  # MetaBAT2 hard minimum; user-requested lower values are forbidden\n"
                    "         '-o', os.path.join(bins_dir, 'bin')]\n"
                    "  res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)\n"
                    "  print(res.stdout or res.stderr)\n"
                    "  if res.returncode != 0:\n"
                    "      sys.exit(f'MetaBAT2 failed (exit {res.returncode}): {res.stderr}')\n"
                    "\n"
                    "  bin_files = sorted(glob.glob(os.path.join(bins_dir, 'bin.*.fa')))\n"
                    "  print(f'Number of bins: {len(bin_files)}')\n"
                    "\n"
                    "  WRONG: -m 200 (or any value < 1500) → 'Contig length < 1500 is not allowed'\n"
                    "  WRONG: --minContig 200 --minContigLen 200 → --minContigLen does not exist\n"
                    "  WRONG: --minContig 200 -m 200 → boost throws 'multiple_occurrences'\n"
                    "  NOTE: If no bins are produced (very fragmented assembly), report bin_count=0\n"
                    "        and continue — this is a valid result, not a script failure."
                )

            # Kraken2 taxonomic classification injection
            # The Kraken2 DB on this server is pre-installed at /mnt/nfs/llmhub/kraken2_db.
            # DO NOT scan random filesystem paths for it — the path is fixed.
            if any(k in _step_title_ctx for k in ("kraken2", "kraken ", "taxonomic class", "taxonom",
                                                   "classify reads", "read classif")):
                _injections.append(
                    "THIS STEP RUNS KRAKEN2.\n"
                    "CRITICAL: the Kraken2 database is PRE-INSTALLED at a fixed path on this server.\n"
                    "DO NOT search /usr/local, /opt, /usr/share, conda envs, or any other location.\n"
                    "DO NOT print 'database not installed' — it IS installed at the path below.\n"
                    "\n"
                    "  import os, subprocess, sys\n"
                    "  # Resolve DB path: env var first, then the known server location.\n"
                    "  KRAKEN2_DB = os.environ.get('KRAKEN2_DEFAULT_DB') or '/mnt/nfs/llmhub/kraken2_db'\n"
                    "  if not (os.path.exists(os.path.join(KRAKEN2_DB, 'hash.k2d'))\n"
                    "          and os.path.exists(os.path.join(KRAKEN2_DB, 'opts.k2d'))\n"
                    "          and os.path.exists(os.path.join(KRAKEN2_DB, 'taxo.k2d'))):\n"
                    "      sys.exit(f'Kraken2 DB files missing in {KRAKEN2_DB} — contact admin.')\n"
                    "\n"
                    "  trimmed_r1 = os.path.join(run_dir, 'trimmed_R1.fastq')\n"
                    "  trimmed_r2 = os.path.join(run_dir, 'trimmed_R2.fastq')\n"
                    "  report_out = os.path.join(run_dir, 'kraken2.report')\n"
                    "  output_out = os.path.join(run_dir, 'kraken2.out')\n"
                    "\n"
                    "  cmd = ['kraken2', '--db', KRAKEN2_DB,\n"
                    "         '--paired', trimmed_r1, trimmed_r2,\n"
                    "         '--report', report_out,\n"
                    "         '--output', output_out,\n"
                    "         '--threads', '4']\n"
                    "  res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)\n"
                    "  print(res.stdout)\n"
                    "  print(res.stderr)\n"
                    "  if res.returncode != 0:\n"
                    "      sys.exit(f'Kraken2 failed: {res.stderr}')\n"
                    "\n"
                    "  # report_out and output_out are valid even if 0 reads are classified —\n"
                    "  # the viral DB on this server will leave bacterial reads unclassified, that is OK.\n"
                    "  print(f'Kraken2 OK: report={report_out} ({os.path.getsize(report_out)} bytes), '\n"
                    "        f'output={output_out} ({os.path.getsize(output_out)} bytes)')\n"
                    "\n"
                    "  # CORRECT Kraken2 report parsing — read the format carefully:\n"
                    "  # col 1: percent | col 2: reads_in_clade (CUMULATIVE/hierarchical, do NOT sum across rows!)\n"
                    "  # col 3: reads_at_taxon (direct) | col 4: rank code | col 5: taxid | col 6: name\n"
                    "  # Rank codes: U=unclassified, R=root, D=domain, K=kingdom, P=phylum, C=class,\n"
                    "  #             O=order, F=family, G=genus, S=species (S1/S2 = subspecies/strain)\n"
                    "  total_reads, unclassified_reads, species_rows = 0, 0, []\n"
                    "  with open(report_out) as _rf:\n"
                    "      for _line in _rf:\n"
                    "          _p = _line.rstrip('\\n').split('\\t')\n"
                    "          if len(_p) < 6: continue\n"
                    "          _direct = int(_p[2].strip())   # reads_at_taxon (column 3)\n"
                    "          _rank   = _p[3].strip()\n"
                    "          _taxid  = _p[4].strip()\n"
                    "          _name   = _p[5].strip()\n"
                    "          total_reads += _direct          # sum of direct reads = total\n"
                    "          if _taxid == '0' or _rank == 'U':\n"
                    "              unclassified_reads = _direct\n"
                    "          if _rank == 'S':                # species ONLY — never root/Viruses/Riboviria\n"
                    "              species_rows.append((_name, int(_p[1].strip())))  # use clade count for species\n"
                    "  classified_reads = total_reads - unclassified_reads\n"
                    "  unclassified_pct = (100.0 * unclassified_reads / total_reads) if total_reads else 0.0\n"
                    "  top3 = sorted(species_rows, key=lambda x: -x[1])[:3]\n"
                    "  print(f'classified={classified_reads}, unclassified={unclassified_reads} ({unclassified_pct:.2f}%)')\n"
                    "  print(f'top3 species: {top3}')\n"
                    "\n"
                    "  WRONG: searching /usr/local/share/kraken2/database — that path does NOT exist here\n"
                    "  WRONG: looping over a candidate list and exiting if none match — DB IS at /mnt/nfs/llmhub/kraken2_db\n"
                    "  WRONG: calling 'kraken2-build --download-library' — DB is pre-installed, do not build\n"
                    "  WRONG: classified_reads += int(parts[1])  → sums reads_in_clade across rows → 5-10x over-count\n"
                    "         (because clade counts are HIERARCHICAL: root, Viruses, Riboviria all carry same reads)\n"
                    "  WRONG: treating 'root', 'Viruses', 'Riboviria' as species — they are not. Filter rank=='S'.\n"
                    "  NOTE: This DB is viral-only — bacterial reads (E. coli, Salmonella, etc.) will mostly\n"
                    "        be 'unclassified'. That is expected. The pipeline must continue."
                )

            # Bracken (downstream of Kraken2) injection
            if any(k in _step_title_ctx for k in ("bracken", "abundance estim", "species abundance")):
                _injections.append(
                    "THIS STEP RUNS BRACKEN on the kraken2.report from the previous step.\n"
                    "Use the SAME Kraken2 DB path as the Kraken2 step (it's required to be the same DB used to classify).\n"
                    "\n"
                    "  import os, subprocess, sys\n"
                    "  KRAKEN2_DB = os.environ.get('KRAKEN2_DEFAULT_DB') or '/mnt/nfs/llmhub/kraken2_db'\n"
                    "  kraken_report = os.path.join(run_dir, 'kraken2.report')\n"
                    "  bracken_out   = os.path.join(run_dir, 'bracken_species.tsv')\n"
                    "\n"
                    "  cmd = ['bracken', '-d', KRAKEN2_DB,\n"
                    "         '-i', kraken_report,\n"
                    "         '-o', bracken_out,\n"
                    "         '-l', 'S', '-r', '150']\n"
                    "  res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)\n"
                    "  print(res.stdout)\n"
                    "  print(res.stderr)\n"
                    "  # Bracken can legitimately exit non-zero when the DB has no Bracken-compatible kmer\n"
                    "  # distribution at the requested level OR when no reads were classified.\n"
                    "  # In that case write an empty header-only TSV and continue.\n"
                    "  if res.returncode != 0 or not os.path.exists(bracken_out):\n"
                    "      with open(bracken_out, 'w') as f:\n"
                    "          f.write('name\\ttaxonomy_id\\ttaxonomy_lvl\\tkraken_assigned_reads\\t'\n"
                    "                  'added_reads\\tnew_est_reads\\tfraction_total_reads\\n')\n"
                    "      print(f'Bracken produced no estimates (likely no classified reads at species level). '\n"
                    "            f'Wrote empty header-only file: {bracken_out}')\n"
                    "  else:\n"
                    "      print(f'Bracken OK: {bracken_out}')\n"
                    "\n"
                    "  WRONG: aborting on non-zero exit — empty result is acceptable for viral DB + bacterial reads"
                )

            # BLAST bin identification injection — fixes the cascading bug seen in
            # Pipeline 2 where bin_assignments.tsv had assigned_species correct but
            # per-reference coverage_pct columns all 0.00, which made the summary step
            # falsely report "references NOT recovered". Forces correct per-ref coverage.
            if any(k in _step_title_ctx for k in (
                    "blast bin", "blastn bin",
                    "bin identification", "bin assignment",
                    "blast.*reference", "identify each bin",
                    "assign each bin", "assign bins")):
                _injections.append(
                    "THIS STEP IDENTIFIES BINS BY BLAST AGAINST REFERENCE GENOMES.\n"
                    "CRITICAL — per-reference coverage_pct MUST be computed correctly,\n"
                    "not left at 0.00 placeholder. The summary step depends on this column\n"
                    "to decide which references are recovered.\n"
                    "\n"
                    "CORRECT pattern:\n"
                    "  import subprocess, os, sys, glob, csv\n"
                    "  # 1. Read each reference FASTA, store its total length (for coverage %)\n"
                    "  ref_dir = os.path.join(run_dir, 'refs')\n"
                    "  ref_files = sorted(glob.glob(os.path.join(ref_dir, '*.fna')))\n"
                    "  ref_sizes = {}\n"
                    "  ref_short = {}  # short name like 'Bacillus_subtilis'\n"
                    "  for rp in ref_files:\n"
                    "      total = 0\n"
                    "      with open(rp) as f:\n"
                    "          for L in f:\n"
                    "              if not L.startswith('>'): total += len(L.strip())\n"
                    "      acc = os.path.basename(rp).split('_genomic')[0]\n"
                    "      ref_sizes[rp] = total\n"
                    "      ref_short[rp] = acc\n"
                    "\n"
                    "  # 2. For each bin × each ref, run blastn and aggregate aligned_bp + pident\n"
                    "  bin_files = sorted(glob.glob(os.path.join(run_dir, 'bins', 'bin.*.fa')))\n"
                    "  rows = []\n"
                    "  for bf in bin_files:\n"
                    "      bin_name = os.path.basename(bf)\n"
                    "      per_ref = {}\n"
                    "      for rp in ref_files:\n"
                    "          res = subprocess.run(\n"
                    "              ['blastn', '-query', bf, '-subject', rp,\n"
                    "               '-outfmt', '6 pident length',\n"
                    "               '-evalue', '1e-10'],\n"
                    "              capture_output=True, text=True, timeout=600)\n"
                    "          aligned = 0; weighted = 0.0\n"
                    "          for line in res.stdout.strip().splitlines():\n"
                    "              parts = line.split('\\t')\n"
                    "              if len(parts) < 2: continue\n"
                    "              try:\n"
                    "                  pid = float(parts[0]); al = int(parts[1])\n"
                    "              except ValueError:\n"
                    "                  continue\n"
                    "              aligned += al\n"
                    "              weighted += pid * al\n"
                    "          per_ref[rp] = (aligned, (weighted/aligned if aligned else 0.0))\n"
                    "      # 3. Pick best reference per bin (max aligned_bp)\n"
                    "      best_rp = max(per_ref, key=lambda r: per_ref[r][0])\n"
                    "      best_aln, best_pid = per_ref[best_rp]\n"
                    "      best_cov = (100.0 * best_aln / ref_sizes[best_rp]) if ref_sizes[best_rp] else 0.0\n"
                    "      row = {\n"
                    "          'bin': bin_name,\n"
                    "          'assigned_species': ref_short[best_rp],\n"
                    "          'total_aligned_bp': best_aln,\n"
                    "          'weighted_pident': f'{best_pid:.2f}',\n"
                    "          'coverage_pct': f'{best_cov:.2f}',\n"
                    "      }\n"
                    "      # Per-reference coverage columns — REAL values, not 0.00 placeholder\n"
                    "      for rp in ref_files:\n"
                    "          col = ref_short[rp].replace('.', '_') + '_cov_pct'\n"
                    "          aln, _ = per_ref[rp]\n"
                    "          pct = (100.0 * aln / ref_sizes[rp]) if ref_sizes[rp] else 0.0\n"
                    "          row[col] = f'{pct:.2f}'\n"
                    "      rows.append(row)\n"
                    "\n"
                    "  # 4. Write bin_assignments.tsv with REAL per-ref coverage values\n"
                    "  out_tsv = os.path.join(run_dir, 'bin_assignments.tsv')\n"
                    "  if rows:\n"
                    "      fieldnames = list(rows[0].keys())\n"
                    "      with open(out_tsv, 'w', newline='') as f:\n"
                    "          w = csv.DictWriter(f, fieldnames=fieldnames, delimiter='\\t')\n"
                    "          w.writeheader()\n"
                    "          for r in rows: w.writerow(r)\n"
                    "  print(f'bin_assignments written: {out_tsv}')\n"
                    "  for r in rows:\n"
                    "      print(r['bin'], '->', r['assigned_species'],\n"
                    "            'cov_pct=', r['coverage_pct'], 'pident=', r['weighted_pident'])\n"
                    "\n"
                    "  WRONG: leaving per-reference cov_pct columns at 0.00 placeholder\n"
                    "  WRONG: computing coverage as (aligned_bp / bin_size) — must use ref_size\n"
                    "  WRONG: forgetting to read ref FASTA to get its true total length\n"
                    "  WRONG: only running blastn once for all bins concatenated — must be per-bin"
                )

            # Final summary/report injection — prevents LLM hallucination of numeric values
            # when synthesizing summary.txt from multiple sources. The LLM was inventing
            # values (e.g. duplication 0.74% instead of real 51.41%) because two tools
            # measure the same metric differently. Forces a single source of truth.
            if any(k in _step_title_ctx for k in (
                    "summary.txt",          # very specific — matches "Write summary.txt..."
                    "final summary",
                    "final report",
                    "compile summary",
                    "compile report",
                    "produce summary",
                    "produce final",
                    "write the summary",
                    "write the final report",
                    "write a final")):
                _injections.append(
                    "THIS STEP WRITES THE FINAL SUMMARY/REPORT FILE.\n"
                    "CRITICAL — SOURCE OF TRUTH RULE (mandatory):\n"
                    "BEFORE writing anything, scan run_dir for ALL data files and bind each metric\n"
                    "to its actual source file. Multi-source pipelines (AMR, MAG, QC) have NO\n"
                    "single metrics.json — you must check each candidate file individually.\n"
                    "\n"
                    "ALGORITHM (follow exactly):\n"
                    "  import os, csv, json, glob\n"
                    "  # Step 1: list ALL data files in run_dir\n"
                    "  available = {os.path.basename(p): p for p in glob.glob(os.path.join(run_dir, '*'))}\n"
                    "  available.update({os.path.basename(p): p\n"
                    "                    for p in glob.glob(os.path.join(run_dir, '*', '*'))})\n"
                    "\n"
                    "  # Step 2: look up each metric in the file most likely to hold it.\n"
                    "  # Example bindings (adapt to the pipeline's actual files):\n"
                    "  #   AMR genes count        → abricate_card.tsv      (count non-header lines)\n"
                    "  #   AMR gene names         → abricate_card.tsv col 6 (GENE column)\n"
                    "  #   AMR truth per-ref      → abricate_ref_*.tsv\n"
                    "  #   CDS count (Prokka)     → prokka_summary.tsv     OR parse combined.gff\n"
                    "  #   CDS count (Prodigal)   → orf_metrics.tsv\n"
                    "  #   Concordance %          → concordance_metrics.tsv\n"
                    "  #   Bin completeness/cont. → checkm2_out/quality_report.tsv (CheckM2)\n"
                    "  #   Bin → species mapping  → bin_assignments.tsv (col assigned_species)\n"
                    "  #   Q20/Q30/GC raw         → raw_stats.tsv (seqkit)\n"
                    "  #   Q20/Q30/GC trimmed     → trimmed_stats.tsv (seqkit)\n"
                    "  #   fastp filtering stats  → fastp.json (summary.before/after_filtering)\n"
                    "  #   Insert size peak       → fastp.json (insert_size.peak)\n"
                    "  #   Viral contamination    → kraken2.report (% classified line)\n"
                    "  #   Duplication exact-seq  → duplicate_count.txt (seqkit rmdup)\n"
                    "\n"
                    "CRITICAL RULES — VIOLATIONS GIVE WRONG SUMMARIES:\n"
                    "  R1. NEVER write 0 or N/A for a metric if its source file EXISTS and is non-empty.\n"
                    "      → ALWAYS open and parse the file before falling back to 0/N/A.\n"
                    "      → Examples seen in production:\n"
                    "        - abricate_card.tsv had 58 rows → wrote 'AMR genes: 0' (wrong)\n"
                    "        - concordance_metrics.tsv had 95.55 → wrote 'concordance: N/A' (wrong)\n"
                    "        - duplicate_count.txt had 51.41 → wrote 'duplication: 0.74' (wrong)\n"
                    "\n"
                    "  R2. NEVER invert recovered/not-recovered logic on bin assignments.\n"
                    "      → If bin_assignments.tsv row has a non-empty 'assigned_species' field,\n"
                    "        the reference IS recovered — add it to recovered list, NOT to\n"
                    "        the 'NOT recovered' list, regardless of coverage_pct numerical value.\n"
                    "      → Example seen in production: 4 bins assigned to 4 species, summary said\n"
                    "        'all 4 references NOT recovered' (logical inversion).\n"
                    "\n"
                    "  R3. NEVER average values from different methodologies measuring the same thing.\n"
                    "      → seqkit rmdup (exact-sequence dup, 51%) vs fastp (k-mer dup, 0.01%):\n"
                    "        these are DIFFERENT metrics — report both separately, never average.\n"
                    "      → Pick ONE method per metric and cite it.\n"
                    "\n"
                    "  R4. Pass/fail thresholds use the SOURCE values verbatim:\n"
                    "      → duplication >= 30% from seqkit rmdup → FAIL\n"
                    "      → AMR recall = |detected ∩ truth| / |truth| computed by reading\n"
                    "        abricate_card.tsv (detected) and abricate_ref_*.tsv (truth).\n"
                    "      → Verdict must be derivable from values cited in the summary.\n"
                    "\n"
                    "  R5. The summary's overall PASS/FAIL must match the BINARY of individual\n"
                    "      pass/fail checks (e.g. PASS only if every required gate passes).\n"
                    "      Do NOT conclude FAIL while all listed checks are PASS or vice versa.\n"
                    "\n"
                    "CORRECT example pattern:\n"
                    "  # AMR — count from abricate_card.tsv, never assume 0\n"
                    "  amr_path = available.get('abricate_card.tsv')\n"
                    "  amr_count = 0; amr_names = set()\n"
                    "  if amr_path and os.path.getsize(amr_path) > 0:\n"
                    "      with open(amr_path) as f:\n"
                    "          rdr = csv.reader(f, delimiter='\\t')\n"
                    "          hdr = next(rdr, None)\n"
                    "          if hdr:\n"
                    "              gene_idx = hdr.index('GENE') if 'GENE' in hdr else 5\n"
                    "              for row in rdr:\n"
                    "                  if not row or row[0].startswith('#'): continue\n"
                    "                  amr_count += 1\n"
                    "                  if len(row) > gene_idx: amr_names.add(row[gene_idx])\n"
                    "  # Now amr_count and amr_names are non-zero if abricate found anything.\n"
                    "\n"
                    "  WRONG: writing 0 AMR without opening abricate_card.tsv first\n"
                    "  WRONG: writing 'all refs NOT recovered' when bin_assignments.tsv assigns species\n"
                    "  WRONG: rounding 51.41 → 0.74 (factor-70 error)\n"
                    "  RULE  : every number in summary.txt must be byte-for-byte traceable to ONE\n"
                    "          source file. If the source HAS it, use the source value. If no source\n"
                    "          file exists, only THEN write N/A."
                )

            # SRA download injection (fixes recurring fastq-dump failures)
            # Bare `fastq-dump SRR...` without prefetch first → empty files
            # Forces ENA direct (wget) as primary path, prefetch+fasterq-dump as fallback
            if any(k in _step_title_ctx for k in ("sra ", "srr", "fastq-dump", "fasterq-dump",
                                                   "ena ", "download.*reads", "download reads",
                                                   "download paired", "illumina reads",
                                                   "metagenomic reads", "miseq reads")):
                _injections.append(
                    "THIS STEP DOWNLOADS SRA READS.\n"
                    "DO NOT call `fastq-dump SRR...` directly — it produces empty files when run\n"
                    "without prefetch first, and the LLM cannot reliably wait for it.\n"
                    "\n"
                    "PREFERRED — query the ENA API to get the EXACT URLs. NEVER compute the\n"
                    "subdir manually (rules differ for 7/8/9/10-digit SRR IDs and getting it\n"
                    "wrong gives 404 — e.g. SRR8359173 → subdir 003, NOT 073).\n"
                    "  import subprocess, os, sys, gzip, shutil, urllib.request\n"
                    "  acc = 'SRR8359173'           # adapt to the user-requested accession\n"
                    "  api = (f'https://www.ebi.ac.uk/ena/portal/api/filereport'\n"
                    "         f'?accession={acc}&result=read_run&fields=fastq_ftp&format=tsv')\n"
                    "  with urllib.request.urlopen(api, timeout=30) as _r:\n"
                    "      _txt = _r.read().decode().strip().splitlines()\n"
                    "  # Expected: header line then tab-separated values; fastq_ftp is ';'-separated\n"
                    "  if len(_txt) < 2:\n"
                    "      sys.exit(f'ENA API returned no data for {acc}: {_txt!r}')\n"
                    "  _cols = _txt[1].split('\\t')\n"
                    "  _ftp_csv = _cols[1] if len(_cols) >= 2 else ''\n"
                    "  _urls = [('https://' + u) if not u.startswith('http') else u\n"
                    "           for u in _ftp_csv.split(';') if u.strip()]\n"
                    "  if not _urls:\n"
                    "      sys.exit(f'No FASTQ URLs in ENA response for {acc}: {_txt!r}')\n"
                    "  out_paths = []\n"
                    "  for url in _urls:\n"
                    "      dest = os.path.join(run_dir, os.path.basename(url))\n"
                    "      res = subprocess.run(['wget', '-q', '-O', dest, url],\n"
                    "                           capture_output=True, text=True, timeout=1800)\n"
                    "      if res.returncode != 0 or os.path.getsize(dest) < 1024:\n"
                    "          sys.exit(f'ENA download failed for {url}: {res.stderr}')\n"
                    "      out = dest[:-3] if dest.endswith('.gz') else dest\n"
                    "      if dest.endswith('.gz'):\n"
                    "          with gzip.open(dest, 'rb') as fi, open(out, 'wb') as fo:\n"
                    "              shutil.copyfileobj(fi, fo)\n"
                    "          os.remove(dest)\n"
                    "      out_paths.append(out)\n"
                    "  # For paired-end: out_paths[0] = R1, out_paths[1] = R2\n"
                    "  raw_r1, raw_r2 = (out_paths + [None, None])[:2]\n"
                    "\n"
                    "FALLBACK — only if ENA URL above 404s (rare for valid SRR IDs):\n"
                    "  subprocess.run(['prefetch', acc, '-O', run_dir, '--max-size', '100g'],\n"
                    "                 check=True, timeout=1800)\n"
                    "  subprocess.run(['fasterq-dump', '--split-files', '-O', run_dir,\n"
                    "                  os.path.join(run_dir, acc, acc + '.sra')],\n"
                    "                 check=True, timeout=3600)\n"
                    "\n"
                    "  WRONG: subprocess.run(['fastq-dump', 'SRR...']) — produces empty files,\n"
                    "         no --split-files, and the LLM can't reliably detect completion.\n"
                    "  WRONG: relying on .sra file existing without prefetch step.\n"
                    "  RULE  : ALWAYS prefer wget on ftp.sra.ebi.ac.uk/vol1/fastq/{prefix}/{subdir}/{acc}/\n"
                    "          — single command, single file pair, fails fast if URL is wrong."
                )

            # CheckM2 bin quality injection
            # DB is pre-registered globally via `checkm2 database --setdblocation`.
            # Path: /home/workshop/checkm2_db/CheckM2_database/uniref100.KO.1.dmnd
            # NO need to set CHECKM2DB env var — checkm2 reads its config automatically.
            if any(k in _step_title_ctx for k in ("checkm2", "checkm 2", "bin quality",
                                                   "completeness", "contamination",
                                                   "mag quality", "bin qc")):
                _injections.append(
                    "THIS STEP RUNS CheckM2 for bin quality assessment.\n"
                    "CheckM2 DB is PRE-INSTALLED and pre-registered. DO NOT search filesystem,\n"
                    "DO NOT export CHECKM2DB, DO NOT call `checkm2 database --download`.\n"
                    "Just call `checkm2 predict` directly — it finds its DB via internal config.\n"
                    "\n"
                    "  import os, subprocess, sys, csv, glob\n"
                    "  bins_dir = os.path.join(run_dir, 'bins')\n"
                    "  out_dir  = os.path.join(run_dir, 'checkm2_out')\n"
                    "  os.makedirs(out_dir, exist_ok=True)\n"
                    "\n"
                    "  # bins must exist beforehand (e.g. from MetaBAT2 step)\n"
                    "  bin_files = sorted(glob.glob(os.path.join(bins_dir, 'bin.*.fa')))\n"
                    "  if not bin_files:\n"
                    "      sys.exit(f'No bin .fa files in {bins_dir} — run MetaBAT2 first')\n"
                    "\n"
                    "  cmd = ['checkm2', 'predict',\n"
                    "         '--input', bins_dir,\n"
                    "         '--output-directory', out_dir,\n"
                    "         '-x', 'fa',\n"
                    "         '--threads', '4',\n"
                    "         '--force']\n"
                    "  res = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)\n"
                    "  print(res.stdout)\n"
                    "  print(res.stderr)\n"
                    "  if res.returncode != 0:\n"
                    "      sys.exit(f'CheckM2 failed: {res.stderr}')\n"
                    "\n"
                    "  # Parse quality_report.tsv\n"
                    "  report = os.path.join(out_dir, 'quality_report.tsv')\n"
                    "  rows = list(csv.DictReader(open(report), delimiter='\\t'))\n"
                    "  for r in rows:\n"
                    "      print(r['Name'], 'completeness=', r['Completeness'],\n"
                    "            'contamination=', r['Contamination'])\n"
                    "  completeness_mean = sum(float(r['Completeness']) for r in rows) / len(rows) if rows else 0\n"
                    "  contamination_mean = sum(float(r['Contamination']) for r in rows) / len(rows) if rows else 0\n"
                    "  print(f'Mean completeness: {completeness_mean:.2f}%')\n"
                    "  print(f'Mean contamination: {contamination_mean:.2f}%')\n"
                    "\n"
                    "  WRONG: setting CHECKM2DB env var manually — already registered\n"
                    "  WRONG: calling `checkm2 database --download` — DB already installed\n"
                    "  WRONG: passing bin extension as '.fa' (with dot) → use 'fa' (no dot)\n"
                    "  NOTE: CheckM2 needs CPU+RAM. For 1-5 bins on E.coli scale, ~1-3 min on this server."
                )

            # bcftools mpileup + call + filter injection (variant calling pipelines)
            # The server has bcftools 1.21 (modern). The OLD bcftools 0.1.19 was replaced.
            # Common LLM mistakes this fixes:
            #   1. mapping rate computed as mapped/R1_only_count → gives 200%
            #   2. bcftools filter uses bare 'DP' which is ambiguous → 0 variants kept
            if any(k in _step_title_ctx for k in ("variant call", "bcftools", "mpileup",
                                                   "vcf filter", "snp call", "call variant",
                                                   "variant filter")):
                _injections.append(
                    "THIS STEP RUNS bcftools (variant calling or filtering).\n"
                    "Server has bcftools 1.21 (modern syntax) — mpileup and call subcommands exist.\n"
                    "\n"
                    "CORRECT mpileup + call pipeline:\n"
                    "  import subprocess, os, sys\n"
                    "  mpileup = subprocess.Popen(\n"
                    "      ['bcftools','mpileup','-f',ref_fasta,'-Ou',sorted_bam],\n"
                    "      stdout=subprocess.PIPE, stderr=subprocess.PIPE)\n"
                    "  call = subprocess.run(\n"
                    "      ['bcftools','call','-mv','-Ov','-o',vcf_path],\n"
                    "      stdin=mpileup.stdout, capture_output=True, text=True, timeout=600)\n"
                    "  mpileup.stdout.close()\n"
                    "  if call.returncode != 0: sys.exit(f'bcftools call failed: {call.stderr}')\n"
                    "\n"
                    "CORRECT bcftools filter — use -e (EXCLUDE) and INFO/DP (NOT bare DP):\n"
                    "  res = subprocess.run(\n"
                    "      ['bcftools','filter','-e','QUAL<20 || INFO/DP<10',\n"
                    "       '-Ov','-o',filtered_vcf, vcf_path],\n"
                    "      capture_output=True, text=True)\n"
                    "  WRONG: -i 'QUAL>20 && DP>10'  →  bare 'DP' is ambiguous in bcftools 1.21\n"
                    "         (interpreted as FORMAT/DP, often missing on biallelic sites) → 0 hits\n"
                    "  WRONG: -i 'QUAL>20 & DP>10'   → same DP ambiguity issue\n"
                    "  USE   : -e 'QUAL<20 || INFO/DP<10'  (exclude negative, explicit INFO/DP)\n"
                    "\n"
                    "CORRECT mapping rate computation — use samtools flagstat as SINGLE source:\n"
                    "  res = subprocess.run(['samtools','flagstat',sorted_bam],\n"
                    "                       capture_output=True, text=True)\n"
                    "  total_reads = 0\n"
                    "  mapped_reads = 0\n"
                    "  for line in res.stdout.splitlines():\n"
                    "      parts = line.split()\n"
                    "      if not parts: continue\n"
                    "      n = int(parts[0])\n"
                    "      if 'in total' in line and total_reads == 0: total_reads = n\n"
                    "      elif 'mapped (' in line and 'primary' not in line and mapped_reads == 0:\n"
                    "          mapped_reads = n\n"
                    "  mapping_rate = (100.0 * mapped_reads / total_reads) if total_reads else 0\n"
                    "  WRONG: total_reads = sum(1 for L in open('trimmed_R1.fastq') if L.startswith('@'))\n"
                    "         then dividing mapped (which counts R1+R2) by R1-only → gives 200%\n"
                    "  WRONG: dividing mapped by 4*total_lines/4 of one FASTQ — same off-by-2 bug\n"
                    "  RULE  : ALWAYS use flagstat 'in total' as the denominator. It already counts\n"
                    "          both mates, so the rate is mapped/total — guaranteed correct.\n"
                    "\n"
                    "CORRECT SNP vs indel counting on VCF — use ALL ALT alleles and length compare:\n"
                    "  snps, indels = 0, 0\n"
                    "  for L in open(vcf_path):\n"
                    "      if L.startswith('#'): continue\n"
                    "      cols = L.rstrip().split('\\t')\n"
                    "      if len(cols) < 5: continue\n"
                    "      ref = cols[3]\n"
                    "      for alt in cols[4].split(','):\n"
                    "          if len(ref) == 1 and len(alt) == 1: snps += 1\n"
                    "          else: indels += 1"
                )

            # ExPASy / SwissProt / UniProt injection
            if any(k in _step_ctx for k in ("expasy", "swissprot", "swiss-prot", "uniprot", "sprot", "p0a7g6", "protein entry", "protein record")):
                _injections.append(
                    "REQUIRED — correct Biopython API for ExPASy/SwissProt (Bio.ExPASy.ProteinDB does NOT exist):\n"
                    "\n"
                    "  from Bio import ExPASy, SwissProt\n"
                    "\n"
                    "  # Fetch a Swiss-Prot entry by UniProt accession\n"
                    "  accession = 'P0A7G6'  # adapt to request\n"
                    "  handle = ExPASy.get_sprot_raw(accession)\n"
                    "  record = SwissProt.read(handle)\n"
                    "\n"
                    "  # EXACT fields on Bio.SwissProt.Record — verified, use only these:\n"
                    "  seq            = record.sequence          # protein sequence string\n"
                    "  seq_len        = record.sequence_length   # int — number of amino acids\n"
                    "  mol_weight     = record.seqinfo[1]        # int, Daltons — seqinfo=(len, mw, crc64)\n"
                    "  gene_name      = record.gene_name         # list of dicts\n"
                    "  organism       = record.organism          # str e.g. 'Escherichia coli (strain K12).'\n"
                    "  description    = record.description       # str e.g. 'RecName: Full=Protein RecA'\n"
                    "  features       = record.features          # list of SeqFeature objects\n"
                    "  n_features     = len(features)\n"
                    "  keywords       = record.keywords          # list of str\n"
                    "  accessions     = record.accessions        # list of accession IDs\n"
                    "  # WRONG (do not exist): record.annotations  record.molecular_weight\n"
                    "\n"
                    "  WRONG (does not exist): from Bio.ExPASy import ProteinDB\n"
                    "  WRONG (does not exist): Bio.ExPASy.ProteinDB.get_protein_by_accession()"
                )

            # Bio.Entrez injection
            if any(k in _step_ctx for k in ("entrez", "efetch", "esearch", "nuccore", "pubmed", "gene db", "entrez fetch")):
                _injections.append(
                    "REQUIRED — correct Biopython Entrez API (copy exactly):\n"
                    "\n"
                    "  import time\n"
                    "  from Bio import Entrez, SeqIO\n"
                    "  from io import StringIO\n"
                    "  Entrez.email = 'genomeer@example.com'  # required by NCBI\n"
                    "\n"
                    "  # Search for IDs\n"
                    "  handle = Entrez.esearch(db='protein', term='RecA[gene] AND bacteria[organism]', retmax=5)\n"
                    "  search_record = Entrez.read(handle); handle.close()\n"
                    "  ids = search_record['IdList']\n"
                    "  print(f'Found {len(ids)} IDs: {ids}')\n"
                    "  if not ids: sys.exit(1)\n"
                    "\n"
                    "  time.sleep(1)  # NCBI rate limit\n"
                    "\n"
                    "  # Fetch sequences — read ALL content first, THEN parse (avoids network handle issues)\n"
                    "  handle = Entrez.efetch(db='protein', id=','.join(ids), rettype='fasta', retmode='text')\n"
                    "  fasta_text = handle.read(); handle.close()\n"
                    "  print(f'Fetched {len(fasta_text)} bytes')\n"
                    "  if not fasta_text.startswith('>'):\n"
                    "      print('ERROR: response is not FASTA:', fasta_text[:200]); sys.exit(1)\n"
                    "  sequences = list(SeqIO.parse(StringIO(fasta_text), 'fasta'))\n"
                    "  print(f'Parsed {len(sequences)} sequences')\n"
                )

            if _injections:
                content += "\n\nCODE PATTERN REMINDER (apply these in your code):\n" + "\n\n".join(_injections)

            # Fix 2 — Inject file_registry from manifest so model uses exact filenames
            # from previous steps instead of inventing them.
            _file_registry = manifest.get("file_registry", {})
            if _file_registry:
                _reg_lines = []
                for _ext, _names in sorted(_file_registry.items()):
                    for _nm in _names:
                        _reg_lines.append(f"  {_ext:<8} -> {_nm}")
                content += (
                    "\n\nFILE_REGISTRY (exact filenames produced by previous steps — "
                    "use these, never invent paths):\n"
                    + "\n".join(_reg_lines)
                )

            # Fix 9 — Always inject the filesystem helper reference so the model
            # uses list_files()/get_file() instead of inventing hardcoded paths.
            try:
                from genomeer.utils.filesystem import FILESYSTEM_PROMPT_SNIPPET as _FS_SNIPPET
                content += "\n\n" + _FS_SNIPPET
            except ImportError:
                pass

            # Fix 5 — Inject exact run_dir file listing into generator prompt.
            # The model invents filenames like "GCF_000009045.1.fna" instead of
            # "GCF_000009045.1_ASM904v1_genomic.fna". Showing the real filenames
            # eliminates all FileNotFoundError caused by invented paths.
            if temp_dir and os.path.isdir(temp_dir):
                import glob as _gl
                _dir_files = sorted(_gl.glob(os.path.join(temp_dir, "*")))
                if _dir_files:
                    _file_lines = []
                    for _fp in _dir_files:
                        _sz = os.path.getsize(_fp) if os.path.isfile(_fp) else 0
                        _file_lines.append(f"  {os.path.basename(_fp)}  ({_sz:,} bytes)")
                    _rundir_section = (
                        f"\nRUN_DIR = r\"{temp_dir}\"\n"
                        f"FILES_CURRENTLY_IN_RUN_DIR (use these EXACT names — do not invent paths):\n"
                        + "\n".join(_file_lines)
                    )
                    content += _rundir_section

            msgs = [
                self.system_prompt,
                HumanMessage(content=prompt),
                HumanMessage(content=content)
            ]

            self._log("ENTER NODE", body=f"step_idx={state['current_idx']}\nrepair_mode={bool(repair_feedback)}", node=node)

            # Retry up to 2 extra times on: empty block OR Python SyntaxError.
            # SyntaxErrors are detected before execution so the LLM can fix them immediately.
            _MAX_FORMAT_RETRIES = 2
            code = None
            sanitized_block = ""
            for _fmt_try in range(_MAX_FORMAT_RETRIES + 1):
                resp = self._llm_invoke(node, f"code_gen (attempt {_fmt_try+1})", msgs)
                sanitized_block = StateGraphHelper.sanitize_execute_block(resp.content)
                code, lang = StateGraphHelper.parse_execute(sanitized_block)

                # Determine if we should retry and why
                _retry_reason: str | None = None
                if not (code and code.strip()):
                    _retry_reason = (
                        "Your previous response contained NO <EXECUTE>...</EXECUTE> block. "
                        "You MUST output exactly one <EXECUTE>#!PY\\n...code...\\n</EXECUTE> block "
                        "and NOTHING else."
                    )
                elif not lang or lang == "PY":
                    # Syntax-check Python before execution.
                    try:
                        compile(code, "<check>", "exec")
                    except SyntaxError as _syn:
                        # Try the deterministic fixer FIRST — avoid an LLM roundtrip when
                        # the error is a simple f-string quote conflict that we can fix locally.
                        _fixed = self._auto_fix_fstring_quotes(code)
                        if _fixed is not code:  # fixer changed something
                            try:
                                compile(_fixed, "<check>", "exec")
                                # Deterministic fix worked — accept it immediately.
                                code = _fixed
                                self._log("FSTRING AUTO-FIX (in-loop)", body=str(_syn), node=node)
                                _syn = None  # signal success
                            except SyntaxError:
                                pass  # fixer didn't help — fall through to LLM retry

                        if _syn is not None:
                            _retry_reason = (
                                f"Your generated Python code has a SyntaxError: {_syn}\n"
                                "Most common cause: f-string with conflicting quotes, "
                                "e.g. f'result: '{val}'' or f\"count: {seq.count(\"G\")}\".\n"
                                "Fix: use SINGLE quotes inside f-strings delimited by double quotes:\n"
                                "  print(f\"GC: {seq.count('G')}\")   # correct\n"
                                "  print(f'N50: {n50}')               # correct (no inner quotes)\n"
                                "Rewrite the ENTIRE code fixing ALL quote conflicts."
                            )

                if _retry_reason is None:
                    break  # code is valid — stop retrying

                if _fmt_try < _MAX_FORMAT_RETRIES:
                    self._log("FORMAT RETRY", body=f"attempt {_fmt_try+1}: {_retry_reason[:120]}", node=node)
                    msgs = msgs + [
                        AIMessage(content=resp.content),
                        HumanMessage(content=_retry_reason + "\n\nTry again now."),
                    ]
            
            # If all retries failed to produce any code, inject a synthetic failure script
            # so the executor+observer can handle it cleanly instead of silently passing None.
            if not code or not code.strip():
                code = (
                    "#!PY\nimport sys\n"
                    "print('GENERATOR_FAILURE: no valid code was produced after all retries.')\n"
                    "sys.exit(1)"
                )
                lang = "PY"
                self._log("SYNTHETIC FAILURE CODE", body="all retries exhausted", node=node)

            # Post-process Python code (deterministic fixes, LLM-independent).
            if code and (not lang or lang == "PY"):
                code = self._inject_missing_imports(code)
                code = self._sanitize_output_paths(code, temp_dir)
                code = self._fix_gc_formula(code)
                code = self._fix_cli_commands(code)
                code = self._fix_fasta_reading(code)
                code = self._fix_faa_line_counting(code)
                code = self._fix_quast_parsing(code)
                code = self._fix_subprocess_kwargs_in_str(code)
                code = self._inject_print_sentinel(code)
                # Final compile() after all post-processing.
                # If our own fixers (or residual LLM errors) produced a SyntaxError,
                # attempt the deterministic f-string quote normalizer before giving up.
                code = self._auto_fix_fstring_quotes(code)

            # Layer 2 — post-generation env correction.
            # _select_env decided env from the prompt (early, no code yet).
            # Now that code is generated, re-evaluate: if the generated code
            # contains a binary that belongs to a different env, correct it.
            if code and not is_diagnostic:
                from genomeer.agent.v2.utils.structured_output import _resolve_env_from_code
                resolved_env = _resolve_env_from_code(code)
                if resolved_env != env_name:
                    self._log(
                        "ENV CORRECTION",
                        body=f"{env_name} → {resolved_env} (based on generated code content)",
                        node=node,
                    )
                    env_name = resolved_env

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

            # Fix 8 — Per-step timeout: classify the step by keyword and set the
            # appropriate timeout_seconds in the manifest before the executor reads it.
            _TIMEOUT_7200_KW = ("humann", "humann3", "functional profiling")
            _TIMEOUT_3600_KW = ("assemble", "assembly", "spades", "megahit", "flye", "scaffold",
                                "de novo", "kraken2", "kraken", "semibin", "concoct", "maxbin",
                                "binning", "antismash", "bgc", "biosynthetic",
                                # annotation / classification / profiling are slow inference steps
                                "annotate", "annotation", "classify reads", "taxonomic classif",
                                "taxonomic profil", "reads profile", "metagenomic profil")
            _TIMEOUT_1800_KW = ("download", "ncbi", "ncbi-genome-download", "fetch genome", "ftp",
                                "genome download", "checkm2", "checkm", "bin quality",
                                "bin completeness", "bin contamination",
                                "eggnog", "diamond", "emapper",
                                "kaiju", "genomad", "pharokka")
            _TIMEOUT_600_KW  = ("hmmer", "hmmscan", "quast", "dbcan", "nonpareil", "sylph")
            if any(k in _step_ctx for k in _TIMEOUT_7200_KW):
                _step_timeout = 7200
            elif any(k in _step_ctx for k in _TIMEOUT_3600_KW):
                _step_timeout = 3600
            elif any(k in _step_ctx for k in _TIMEOUT_1800_KW):
                _step_timeout = 1800
            elif any(k in _step_ctx for k in _TIMEOUT_600_KW):
                _step_timeout = 600
            else:
                _step_timeout = 600
            _tmfest = dict(updates.get("manifest", manifest))
            _tmfest["timeout_seconds"] = _step_timeout
            updates["manifest"] = _tmfest
            self._log("FIX8 TIMEOUT", body=f"step_timeout={_step_timeout}s  step_ctx_sample={_step_ctx[:80]}", node=node)
            
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
            from genomeer.runtime.env_manager import (
                load_registry, spec_path, env_prefix,
                has_env, has_conda_installed, has_pip_installed,
                _write_conda_sentinel, create_or_update_env,
            )
            from genomeer.agent.v2.utils.structured_output import _resolve_env_from_code

            # No-downgrade rule: once meta-env1 is selected for this pipeline,
            # never switch back to bio-agent-env1 — even if a repair cycle
            # regenerates code that doesn't mention meta-env1 tools.
            current_env  = state.get("env_name", "bio-agent-env1")
            pending_code = state.get("pending_code") or state.get("diagnostic_code") or ""
            resolved_env = _resolve_env_from_code(pending_code)
            env_name = (
                "meta-env1"
                if resolved_env == "meta-env1" or current_env == "meta-env1"
                else "bio-agent-env1"
            )
            if env_name != current_env:
                self._log(
                    "ENV NO-DOWNGRADE",
                    body=f"keeping meta-env1 (resolved={resolved_env}, state={current_env})",
                    node="ensure_env",
                )

            if has_env(env_name):
                prefix = env_prefix(env_name)

                # Level 1 — conda sentinel: env is usable if conda packages are installed.
                # For legacy envs (installed before two-level sentinel system), write it now.
                if not has_conda_installed(env_name):
                    _write_conda_sentinel(env_name)

                # Level 2 — pip: attempt non-blocking repair if sentinel missing.
                if not has_pip_installed(env_name):
                    try:
                        from genomeer.runtime.env_manager import _pip_install_from_spec
                        reg = load_registry()
                        rec = next((e for e in reg.get("envs", []) if e.get("name") == env_name), None)
                        if rec:
                            _pip_install_from_spec(prefix, spec_path(rec["spec"]))
                            self._log("PIP REPAIR", body=f"pip packages installed into '{env_name}'", node="ensure_env")
                    except Exception as exc:
                        # Pip failure is non-fatal — conda packages are sufficient for code execution.
                        self._log("PIP REPAIR SKIPPED", body=f"non-fatal: {exc}", node="ensure_env")

                return {
                    "env_ready": True,
                    "next_step": "executor",
                    "messages": [AIMessage(content=f"<log>Environment '{env_name}' ready at {prefix}</log>")],
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
                        "messages": [AIMessage(content=f"<log>Error: Env '{env_name}' not found in registry.</log>")],
                    }
                
                spec = spec_path(rec["spec"])
                channels = rec.get("channels")
                stream = entry["stream"]
                
                # Block until micromamba finishes; logs go to stream.push(...)
                create_or_update_env(env_name, spec, channels, stream.push)
                
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
                    "messages": [AIMessage(content=f"<log>Environment '{env_name}' ready at {prefix}</log>")]
                }
            except Exception as e:
                try: entry["stream"].push(f"ERROR: {e}\n")
                except Exception: pass
                try: entry["stream"].close()
                except Exception: pass
                self._install_threads.pop(env_name, None)
                return {
                    "next_step": "end",
                    "messages": [AIMessage(content=f"<log>Env install failed: {e}</log>")]
                }
        
        def _get_current_step(self, state: AgentState):
            plan = state.get("plan") or []
            idx  = state.get("current_idx", 0)
            if not plan or idx >= len(plan):
                return {"title": "<unknown>", "status": "todo", "notes": ""}
            return plan[idx]

        def _executor(self, state: AgentState) -> AgentState:
            node = "executor"
            code = (state.get("pending_code") or "").strip()
            diagnostic_code = (state.get("diagnostic_code") or "").strip()
            diagnostic_mode = state.get("diagnostic_mode")
            env = state["env_name"]
            timeout = state["manifest"].get("timeout_seconds", 600)
            last_result = ""

            # Fix T2.1 / T2.3 — export RUN_TEMP_DIR so child processes inherit it
            _run_temp_dir = state.get("run_temp_dir") or ""
            if _run_temp_dir:
                os.environ["RUN_TEMP_DIR"] = _run_temp_dir
            _extra_env: dict = {"RUN_TEMP_DIR": _run_temp_dir, "MPLBACKEND": "Agg"} if _run_temp_dir else {"MPLBACKEND": "Agg"}

            # Fix T9 — export GENOMEER_MAX_RAM_GB if per-worker RAM limit is set
            _per_worker_ram = state.get("_per_worker_ram_gb")
            if _per_worker_ram is not None:
                _ram_str = str(_per_worker_ram)
                os.environ["GENOMEER_MAX_RAM_GB"] = _ram_str
                _extra_env["GENOMEER_MAX_RAM_GB"] = _ram_str

            # BUG-37 — include tool versions in manifest for cache invalidation
            _detected_tools = [t for t in BIOLOGICAL_GATES if re.search(r'\b' + re.escape(t) + r'\b', code)]
            for _tool in _detected_tools:
                self._version_tracker.record_tool(_tool, env)
            _tool_versions = self._version_tracker.as_dict()

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

            # ── SECURITY CHECK (pre-execution) ───────────────────────────────────
            _is_bash_code = (
                code.strip().startswith("#!R")
                or code.strip().startswith("# R code")
                or code.strip().startswith("# R script")
                or code.strip().startswith("#!BASH")
                or code.strip().startswith("# Bash script")
                or code.strip().startswith("#!CLI")
            )
            if _is_bash_code:
                _sec_ok, _sec_reason = check_bash_script(code, diagnostic_mode=bool(diagnostic_mode))
            else:
                _sec_ok, _sec_reason = check_python_code(code)

            if not _sec_ok:
                self._log(
                    "SECURITY BLOCK",
                    body=f"WARNING: {_sec_reason}\nCode preview (first 300 chars): {code[:300]}",
                    node=node,
                )
                _sec_manifest = self._clean_manifest(state.get("manifest") or {})
                _sec_manifest["repair_feedback"] = (
                    f"SECURITY_BLOCK: The generated code was rejected by the security checker.\n"
                    f"Reason: {_sec_reason}\n"
                    f"Rewrite the code avoiding the dangerous pattern entirely.\n"
                    f"MANDATORY FIXES:\n"
                    f"1. NEVER use subprocess.run(..., shell=True) — always pass a list of args:\n"
                    f"   WRONG : subprocess.run('seqkit stats -a file > out.tsv', shell=True)\n"
                    f"   RIGHT : res = subprocess.run(['seqkit', 'stats', '-a', fasta_path],\n"
                    f"               capture_output=True, text=True, check=True)\n"
                    f"           with open(output_tsv, 'w') as f: f.write(res.stdout)\n"
                    f"2. NEVER use os.system() — replace with subprocess.run(list_of_args).\n"
                    f"3. NEVER use eval() or exec().\n"
                    f"For shell redirection (>): capture stdout with capture_output=True and write to file manually."
                )
                _sec_manifest["repair_step_idx"] = state.get("current_idx", 0)
                return {
                    "next_step":   "generator",
                    "last_result": f"[SECURITY BLOCK] {_sec_reason}",
                    "manifest":    _sec_manifest,
                    "messages": [AIMessage(content=(
                        f"<STATUS:blocked>\n[SECURITY BLOCK] Code rejected before execution.\n"
                        f"Reason: {_sec_reason}"
                    ))],
                }
            # ── END SECURITY CHECK ────────────────────────────────────────────────

            try:
                if (code.strip().startswith("#!R") or code.strip().startswith("# R code") or code.strip().startswith("# R script")):
                    r_code = re.sub(r"^#!R|^# R code|^# R script", "", code, 1).strip()  # noqa: B034
                    out = run_with_timeout(
                        run_r_code,
                        args=[r_code],
                        kwargs={"env_name": env, "timeout": timeout, "extra_env": _extra_env},
                        timeout=timeout
                    )
                elif (code.strip().startswith("#!BASH") or code.strip().startswith("# Bash script") or code.strip().startswith("#!CLI")):
                    if code.strip().startswith("#!CLI"):
                        cli_command = re.sub(r"^#!CLI", "", code, 1).strip().replace("\n", " ")  # noqa: B034
                        out = run_with_timeout(
                            run_bash_script,
                            args=[cli_command],
                            kwargs={"env_name": env, "timeout": timeout, "extra_env": _extra_env},
                            timeout=timeout
                        )
                    else:
                        bash_script = re.sub(r"^#!BASH|^# Bash script", "", code, 1).strip()  # noqa: B034
                        out = run_with_timeout(
                            run_bash_script,
                            args=[bash_script],
                            kwargs={"env_name": env, "timeout": timeout, "extra_env": _extra_env},
                            timeout=timeout
                        )
                else:
                    # Inject custom functions into the Python execution environment
                    self._inject_custom_functions_to_repl()
                    code = re.sub(r"^\s*#!PY\s*\r?\n", "", code, count=1)
                    # Fix UUID hallucination: normalize every run_dir string assignment
                    # to the correct path so LLM typos in the UUID are silently corrected.
                    if _run_temp_dir:
                        code = re.sub(
                            r'run_dir\s*=\s*r?["\'][^"\']*["\']',
                            f'run_dir = r"{_run_temp_dir}"',
                            code,
                        )
                    out = run_with_timeout(
                        run_python_code,
                        args=[code],
                        kwargs={"env_name": env, "timeout": timeout, "extra_env": _extra_env},
                        timeout=timeout
                    )

                # bound size — keep TAIL so errors (which appear last) are never lost
                if out and len(out) > 12000:
                    out = "...<truncated head>\n" + out[-12000:]

                last_result = out or ""
                self._log("EXECUTION RESULT", body=last_result[:2000], node=node)

                # Fix SIGKILL — detect OOM / CPU-limit kill (exit code -9)
                _sigkill_m = re.search(r"Exit code[:\s]+-9\b", last_result, re.IGNORECASE)
                if _sigkill_m:
                    _ram_limit = os.environ.get("GENOMEER_MAX_RAM_GB", "unknown")
                    self._log(
                        "SIGKILL DETECTED",
                        body=f"Process killed with exit code -9 (OOM or CPU limit). RAM limit: {_ram_limit} GB.",
                        node=node,
                    )

            except Exception as e:
                tb = traceback.format_exc()
                last_result = f"[EXECUTION ERROR] {type(e).__name__}: {e}\n"
                last_result += f"traceback: {tb}"
                self._log("EXECUTION ERROR", body=last_result, node=node)

            self._log("EXIT NODE", body="next_step=observer", node=node)
            result_key = "diagnostic_observation" if diagnostic_mode else "last_result"
            new_manifest = dict(state.get("manifest") or {})
            if _tool_versions:
                new_manifest["tool_versions"] = _tool_versions
            updates = {
                "next_step": "validator",
                result_key: last_result,
                "manifest": new_manifest,
                "messages": [AIMessage(content=f"<observe>Code Execution output:  '{last_result}'</observe>")],
            }
            return updates

        # ── VALIDATOR ────────────────────────────────────────────────────────
        def _validator(self, state: AgentState) -> AgentState:
            """
            Deterministic post-executor gate (Phase 1 + Phase 2 + Phase 3).

            ok=True  + score≥0   → bookkeeping (file_registry + manifest) + orchestrator
            ok=True  + score=-1  → no contract for this step → observer (LLM)
            ok=False + RUNTIME=long   → 0 retries → observer immediately
            ok=False + RUNTIME=medium → 1 retry with best hint → observer if still failing
            ok=False + RUNTIME=fast   → up to 3 sequential variants → observer if exhausted
            """
            node = "validator"

            # diagnostic runs store their result in diagnostic_observation, not last_result.
            # The validator has no business checking file contracts on debug code output —
            # pass through immediately so the observer can read diagnostic_observation directly.
            if state.get("diagnostic_mode"):
                self._log("VALIDATOR BYPASS", body="diagnostic_mode=True → observer", node=node)
                return {"next_step": "observer"}

            step        = self._get_current_step(state)
            run_dir     = state.get("run_temp_dir", "")
            last_result = state.get("last_result") or ""

            # If execution failed (non-zero exit code), contracts cannot prove success.
            # Pass directly to observer so its HARD BLOCK catches the failure.
            _exit_m = re.search(r"Exit code[:\s]+(\d+)", last_result, re.IGNORECASE)
            if _exit_m and _exit_m.group(1) != "0":
                self._log(
                    "VALIDATOR BYPASS (exit!=0)",
                    body=f"exit_code={_exit_m.group(1)} — skipping contracts → observer",
                    node=node,
                )
                return {"next_step": "observer"}

            result = ToolValidator.validate(step["title"], run_dir, last_result)
            max_r  = ToolValidator.max_retries(step["title"])
            self._log(
                "VALIDATOR",
                body=(f"step='{step['title']}' ok={result.ok} "
                      f"score={result.score:.2f} runtime_max_retries={max_r} "
                      f"reason={result.reason}"),
                node=node,
            )

            # ── no contract → observer handles it ────────────────────────────
            if result.score == -1.0:
                self._log("VALIDATOR PASS-THROUGH", body="no contract → observer", node=node)
                return {"next_step": "observer"}

            # ── CONTRACT OK → done bookkeeping ────────────────────────────────
            # Require both ok=True AND score >= _VALIDATOR_MIN_SCORE.
            # A low score (e.g. 0.02) means the contract matched a wrong file
            # (e.g. staged input FASTA found by AssemblyContract) — treat as no-contract.
            if result.ok and result.score >= _VALIDATOR_MIN_SCORE:
                summary = f"[validator] {result.reason} (score={result.score:.2f})"
                _dir_files = self._list_ctx_files(run_dir)
                plan = list(state["plan"])
                plan[state["current_idx"]] = {
                    **plan[state["current_idx"]],
                    "status": "done",
                    "notes": summary,
                }
                observations = list(state.get("manifest", {}).get("observations", []))
                observations.append({
                    "step_idx":       state["current_idx"],
                    "title":          step["title"],
                    "status":         "done",
                    "summary":        summary,
                    "score":          result.score,
                    "stdout":         last_result[:2000],
                    "files_snapshot": _dir_files,
                })
                new_manifest = self._clean_manifest(state["manifest"])
                new_manifest["observations"]  = observations
                new_manifest["file_registry"] = self._build_file_registry(run_dir)
                new_manifest["files"]         = [f["name"] for f in _dir_files]
                self._log("VALIDATOR DONE", body=summary, node=node)
                return {
                    "plan":                   plan,
                    "current_idx":            state["current_idx"] + 1,
                    "next_step":              "orchestrator",
                    "last_result":            last_result,
                    "manifest":               new_manifest,
                    "diagnostic_mode":        False,
                    "diagnostic_code":        None,
                    "diagnostic_observation": None,
                    "messages": [AIMessage(content=f"<STATUS:done>\n{summary}")],
                }

            # ── CONTRACT FAILED ────────────────────────────────────────────────
            rc = {int(k): int(v) for k, v in (state.get("retry_counts") or {}).items() if str(k).isdigit()}
            rc[state["current_idx"]] = rc.get(state["current_idx"], 0) + 1
            attempt = rc[state["current_idx"]]  # 1-based

            # long tools → 0 retries allowed → inject context then observer
            if max_r == 0:
                fallback = (result.retry_params or {}).get("hint", "check command arguments")
                hint = ToolValidator.get_variant_hint(step["title"], 0, fallback)
                new_manifest = self._clean_manifest(state["manifest"])
                new_manifest["repair_feedback"] = (
                    f"[VALIDATOR] '{step['title']}' failed.\n"
                    f"score={result.score:.2f}, reason={result.reason}\n"
                    f"Long-running tool — no auto-retry. Suggested fix: {hint}\n"
                    f"Observer must diagnose from stdout below."
                )
                new_manifest["repair_step_idx"] = state["current_idx"]
                self._log(
                    "VALIDATOR → OBSERVER (long tool)",
                    body=f"{result.reason} — long runtime, no auto-retry",
                    node=node,
                )
                return {
                    "next_step":    "observer",
                    "retry_counts": rc,
                    "manifest":     new_manifest,
                }

            # within retry budget → pick variant hint for this attempt
            if attempt <= max_r:
                fallback = (result.retry_params or {}).get("hint", "fix the command and retry")
                # retry_idx is 0-based: attempt 1 → variant[0], attempt 2 → variant[1], …
                hint = ToolValidator.get_variant_hint(step["title"], attempt - 1, fallback)
                new_manifest = self._clean_manifest(state["manifest"])
                new_manifest["repair_feedback"] = (
                    f"VALIDATOR_FAIL (attempt {attempt}/{max_r}): {result.reason}.\n"
                    f"Parameter fix to apply: {hint}"
                )
                new_manifest["repair_step_idx"] = state["current_idx"]
                self._log(
                    "VALIDATOR RETRY",
                    body=f"attempt {attempt}/{max_r} — hint: {hint[:120]}",
                    node=node,
                )
                return {
                    "plan": [
                        {**p, "status": "blocked"} if i == state["current_idx"] else p
                        for i, p in enumerate(state["plan"])
                    ],
                    "current_idx":            state["current_idx"],
                    "next_step":              "generator",
                    "manifest":               new_manifest,
                    "retry_counts":           rc,
                    "diagnostic_mode":        False,
                    "diagnostic_code":        None,
                    "diagnostic_observation": None,
                    "messages": [AIMessage(content=(
                        f"<STATUS:blocked>\n[validator] {result.reason} "
                        f"— attempt {attempt}/{max_r}, applying: {hint}"
                    ))],
                }

            # retries exhausted → observer for LLM judgment
            self._log(
                "VALIDATOR EXHAUSTED",
                body=f"{max_r} retries used, passing to observer",
                node=node,
            )
            return {"next_step": "observer", "retry_counts": rc}

        def _observer(self, state: AgentState) -> AgentState:
            node = "observer"
            step = self._get_current_step(state)
            diagnostic_mode = state.get("diagnostic_mode")
            last_result = state.get("last_result") or ""

            # ── EXIT CODE EXTRACTION (Fix 4) ────────────────────────────────────────
            # helper.py formats failures as "Exit code: N" (with colon).
            _exit_code_m = re.search(r"Exit code[:\s]+(\d+)", last_result, re.IGNORECASE)
            _exit_code_nonzero = bool(_exit_code_m and _exit_code_m.group(1) != "0")
            _exit_code_zero    = bool(_exit_code_m and _exit_code_m.group(1) == "0")

            # Fix 4 — additional deterministic signals beyond exit_code
            _has_traceback = bool(re.search(r"Traceback \(most recent call last\)", last_result))
            _any_pyerr     = bool(re.search(
                r"\b(?:ValueError|TypeError|RuntimeError|UnicodeDecodeError|"
                r"OSError|IOError|ZeroDivisionError|IndexError|StopIteration|"
                r"AssertionError|RecursionError|OverflowError):",
                last_result,
            ))

            _ERROR_SIGNALS = re.compile(
                r"Traceback|Error:|Exception:|GENERATOR_FAILURE|"
                r"FileNotFoundError|NameError|KeyError|AttributeError|SyntaxError|"
                r"ImportError|ModuleNotFoundError|TimeoutError|PermissionError",
                re.IGNORECASE,
            )
            _MEANINGFUL_OUTPUT = re.compile(
                # \d{3,} requires ≥3-digit numbers (biological magnitudes like N50, genome length).
                # Plain \d+ would falsely match "0" in "Exit code: 0" and trigger FAST-DONE
                # on runs that produced zero stdout.
                r"\d{3,}|percent|%|N50|GC|contig|length|sequence|scaffold|"
                r"found|done|success|saved|written|downloaded|parsed|FASTA ready",
                re.IGNORECASE,
            )

            # Fix 4 — file-existence check: if run_dir has files, that's a done signal
            _temp_dir_obs = state.get("run_temp_dir", "")
            _dir_files_obs = self._list_ctx_files(_temp_dir_obs) if _temp_dir_obs else []
            _output_files_exist = len(_dir_files_obs) > 0

            # FAST-DONE: exit_code=0 (or no exit code) AND no errors AND
            # (meaningful stdout OR files exist in run_dir)
            _is_exit_ok = (
                not _exit_code_nonzero
                and not _has_traceback
                and not _any_pyerr
                and last_result
                and not _ERROR_SIGNALS.search(last_result)
                and (_MEANINGFUL_OUTPUT.search(last_result) or _output_files_exist)
                and len(last_result.strip()) > 5
            )

            # ── HARD BLOCK 0: no output at all ──────────────────────────────────────
            # An empty last_result means the executor produced nothing — no stdout,
            # no stderr, no exit code.  Sending this to the LLM observer causes small
            # models to hallucinate STATUS:done on a step that never ran or crashed
            # silently.  Force a blocked return so the generator can retry.
            if not diagnostic_mode and not last_result.strip():
                rc = {int(k): int(v) for k, v in (state.get("retry_counts") or {}).items() if str(k).isdigit()}
                rc[state["current_idx"]] = rc.get(state["current_idx"], 0) + 1
                new_manifest = self._clean_manifest(state["manifest"])
                new_manifest["repair_feedback"] = (
                    "Execution produced no output (empty stdout and stderr). "
                    "The script likely crashed before printing anything or was never executed. "
                    "Fix: add a print() at the top of the script to confirm it starts, "
                    "and wrap the main logic in try/except to surface any hidden error."
                )
                new_manifest["repair_step_idx"] = state["current_idx"]
                self._log("HARD BLOCK empty output", body="last_result is empty", node=node)
                return {
                    "plan": [{**p, "status": "blocked"} if i == state["current_idx"] else p
                             for i, p in enumerate(state["plan"])],
                    "current_idx": state["current_idx"],
                    "next_step": "diagnostics" if rc[state["current_idx"]] > self.MAX_STEP_RETRIES else "generator",
                    "messages": [AIMessage(content="<STATUS:blocked>\nExecution produced no output — treated as failure.")],
                    "manifest": new_manifest,
                    "retry_counts": rc,
                    "diagnostic_mode": False,
                    "diagnostic_code": None,
                    "diagnostic_observation": None,
                }

            # ── HARD BLOCK 1: non-zero exit code ────────────────────────────────────
            if not diagnostic_mode and _exit_code_nonzero:
                rc = {int(k): int(v) for k, v in (state.get("retry_counts") or {}).items() if str(k).isdigit()}
                rc[state["current_idx"]] = rc.get(state["current_idx"], 0) + 1
                new_manifest = self._clean_manifest(state["manifest"])
                new_manifest["repair_feedback"] = (
                    f"EXIT_CODE={_exit_code_m.group(1)}: execution failed. "
                    f"Read the STDERR carefully and fix the root cause.\n"
                    f"Last output:\n{last_result[-800:]}"
                )
                new_manifest["repair_step_idx"] = state["current_idx"]
                self._log("HARD BLOCK exit_code!=0", body=f"exit_code={_exit_code_m.group(1)}", node=node)
                return {
                    "plan": [{**p, "status": "blocked"} if i == state["current_idx"] else p
                             for i, p in enumerate(state["plan"])],
                    "current_idx": state["current_idx"],
                    "next_step": "diagnostics" if rc[state["current_idx"]] > self.MAX_STEP_RETRIES else "generator",
                    "messages": [AIMessage(content=f"<STATUS:blocked>\nExit code {_exit_code_m.group(1)} — execution failed.")],
                    "manifest": new_manifest,
                    "retry_counts": rc,
                    "diagnostic_mode": False,
                    "diagnostic_code": None,
                    "diagnostic_observation": None,
                }

            # ── HARD BLOCK 2: Traceback/Python error without captured exit_code ─────
            # Catches failures where the execution wrapper swallowed the exit code
            # but the Python traceback is still visible in the output.
            if not diagnostic_mode and not _exit_code_nonzero and (_has_traceback or _any_pyerr):
                rc = {int(k): int(v) for k, v in (state.get("retry_counts") or {}).items() if str(k).isdigit()}
                rc[state["current_idx"]] = rc.get(state["current_idx"], 0) + 1
                new_manifest = self._clean_manifest(state["manifest"])
                _err_snippet = last_result[-600:]
                new_manifest["repair_feedback"] = (
                    f"Python exception detected (no exit code captured). "
                    f"Fix the error shown below:\n{_err_snippet}"
                )
                new_manifest["repair_step_idx"] = state["current_idx"]
                self._log("HARD BLOCK traceback", body="Traceback/PyError without exit_code", node=node)
                return {
                    "plan": [{**p, "status": "blocked"} if i == state["current_idx"] else p
                             for i, p in enumerate(state["plan"])],
                    "current_idx": state["current_idx"],
                    "next_step": "diagnostics" if rc[state["current_idx"]] > self.MAX_STEP_RETRIES else "generator",
                    "messages": [AIMessage(content=f"<STATUS:blocked>\nPython exception detected — execution failed.")],
                    "manifest": new_manifest,
                    "retry_counts": rc,
                    "diagnostic_mode": False,
                    "diagnostic_code": None,
                    "diagnostic_observation": None,
                }

            # ── FAST-DONE: deterministic success ────────────────────────────────────
            if not diagnostic_mode and _is_exit_ok:
                # Full summary kept in observations + plan.notes for the finalizer
                # context. The chat-facing message is intentionally brief: the raw
                # stdout is already streamed via the executor's <observe> block
                # (rendered in the left logs panel as a collapsible). Dumping it
                # again in the chat bubble was pure noise (UX feedback).
                summary = f"Execution succeeded.\n\nOutput:\n{last_result[:500]}"
                _files_note = f" ({len(_dir_files_obs)} file(s) in run dir)" if _output_files_exist else ""
                chat_msg = f"Step done.{_files_note}"
                _reason = "exit_code=0 + files_exist" if _output_files_exist else "exit_code=0 + meaningful output"
                self._log("OBSERVER FAST-DONE", body=f"{_reason} → done (no LLM)", node=node)
                plan = list(state["plan"])
                plan[state["current_idx"]] = {**plan[state["current_idx"]], "status": "done", "notes": summary}
                observations = list(state.get("manifest", {}).get("observations", []))
                observations.append({
                    "step_idx": state["current_idx"],
                    "title": step["title"],
                    "status": "done",
                    "summary": summary,
                    "stdout": last_result[:2000],
                    "files_snapshot": _dir_files_obs,
                })
                new_manifest = self._clean_manifest(state["manifest"])
                new_manifest["observations"] = observations
                # Fix 2 — build and store file_registry after every done step
                new_manifest["file_registry"] = self._build_file_registry(_temp_dir_obs)
                new_manifest["files"] = [f["name"] for f in _dir_files_obs]
                return {
                    "plan": plan,
                    "current_idx": state["current_idx"] + 1,
                    "next_step": "orchestrator",
                    "last_result": last_result,
                    "manifest": new_manifest,
                    "diagnostic_mode": False,
                    "diagnostic_code": None,
                    "messages": [AIMessage(content=f"<STATUS:done>\n{chat_msg}")],
                }

            if not diagnostic_mode and last_result and "GENERATOR_FAILURE:" in last_result:
                self._log("OBSERVER PRE-CHECK", body="generator failure fast-path", node=node)
            # ── END PRE-CHECK ────────────────────────────────────────────────────────

            # Fast-path: Python import errors are always execution failures, never missing inputs.
            # Bypass the LLM entirely to prevent small models from misrouting this to QA/user.
            # IMPORTANT: only fire for ModuleNotFoundError (missing package) OR ImportError with
            # "No module named" (also missing package). Do NOT fire for ImportError alone —
            # that usually means a hallucinated class/function inside an existing module,
            # which requires fixing the code, not pip-installing anything.
            _has_module_not_found = bool(re.search(r"ModuleNotFoundError:", last_result))
            _has_no_module = bool(re.search(r"No module named '", last_result))
            _has_import_error = bool(re.search(r"ImportError:", last_result))
            _is_missing_package = _has_module_not_found or (_has_import_error and _has_no_module)
            _is_bad_import = _has_import_error and not _has_no_module  # hallucinated name

            # Fix 7 — Deterministic error classifier: map error type → targeted repair.
            # Each branch builds a precise summary that guides the generator directly,
            # avoiding the LLM observer for unambiguous error patterns.
            _temp_dir_err = state.get("run_temp_dir", "")
            _manifest_files = state.get("manifest", {}).get("files", [])

            # NameError: variable not defined
            _nameerr_m = re.search(r"NameError: name '([^']+)' is not defined", last_result)
            if not diagnostic_mode and _nameerr_m and not _is_missing_package and not _is_bad_import:
                _undef_var = _nameerr_m.group(1)
                # Check if it's a known pattern we can fix
                if _undef_var in ("fasta_path", "run_dir", "accessions", "contigs"):
                    _files_hint = (
                        f"Files in run_dir: {_manifest_files}" if _manifest_files
                        else f"run_dir = r\"{_temp_dir_err}\""
                    )
                    summary = (
                        f"NameError: '{_undef_var}' is not defined. "
                        f"Fix: define it before use. {_files_hint}. "
                        f"For fasta_path: use glob.glob(os.path.join(run_dir, '*.fna'))[0]. "
                        f"For run_dir: use the value from the prompt context. "
                        f"For accessions: use [os.path.basename(f) for f in glob.glob(os.path.join(run_dir, '*.fna'))]. "
                        f"For contigs: use list(SeqIO.parse(fasta_path, 'fasta'))."
                    )
                    self._log("FAST-PATH NameError", body=summary, node=node)
                    new_manifest = self._clean_manifest(state["manifest"])
                    rc = {int(k): int(v) for k, v in (state.get("retry_counts") or {}).items() if str(k).isdigit()}
                    rc[state["current_idx"]] = rc.get(state["current_idx"], 0) + 1
                    new_manifest["repair_feedback"] = summary
                    new_manifest["repair_step_idx"] = state["current_idx"]
                    return {
                        "plan": [{**p, "status": "blocked"} if i == state["current_idx"] else p
                                 for i, p in enumerate(state["plan"])],
                        "current_idx": state["current_idx"],
                        "next_step": "diagnostics" if rc[state["current_idx"]] > self.MAX_STEP_RETRIES else "generator",
                        "messages": [AIMessage(content=f"<STATUS:blocked>\n{summary}")],
                        "manifest": new_manifest,
                        "retry_counts": rc,
                        "diagnostic_mode": False,
                        "diagnostic_code": None,
                        "diagnostic_observation": None,
                    }

            # KeyError: dictionary key missing — most commonly wrong column name in a TSV
            _key_err_m = re.search(r"KeyError:\s*'([^']+)'", last_result)
            if not diagnostic_mode and _key_err_m and not _is_missing_package:
                _bad_key = _key_err_m.group(1)
                # Find any TSV/TXT/CSV files in run_dir that may be the culprit
                _file_preview = ""
                if _temp_dir_err:
                    _candidate_files = []
                    for _ext in ("*.tsv", "*.txt", "*.csv"):
                        _candidate_files.extend(
                            sorted(__import__("glob").glob(os.path.join(_temp_dir_err, "**", _ext), recursive=True))
                        )
                    # Read the first 5 lines of the first 2 matching files
                    _previews = []
                    for _cf in _candidate_files[:2]:
                        try:
                            with open(_cf, encoding="utf-8", errors="replace") as _fh:
                                _lines = [_fh.readline().rstrip() for _ in range(5)]
                            _previews.append(
                                f"File: {os.path.relpath(_cf, _temp_dir_err)}\n"
                                + "\n".join(f"  {l}" for l in _lines if l)
                            )
                        except Exception:
                            pass
                    if _previews:
                        _file_preview = "\n\nActual file contents (first 5 lines each):\n" + "\n\n".join(_previews)

                summary = (
                    f"KeyError: '{_bad_key}' — the dictionary key does not exist in the file. "
                    f"The column name or key is wrong. Check the actual headers/keys in the file, "
                    f"then fix the code to use the exact key present."
                    f"{_file_preview}"
                )
                self._log("FAST-PATH KeyError", body=summary[:400], node=node)
                new_manifest = self._clean_manifest(state["manifest"])
                rc = {int(k): int(v) for k, v in (state.get("retry_counts") or {}).items() if str(k).isdigit()}
                rc[state["current_idx"]] = rc.get(state["current_idx"], 0) + 1
                new_manifest["repair_feedback"] = summary
                new_manifest["repair_step_idx"] = state["current_idx"]
                return {
                    "plan": [{**p, "status": "blocked"} if i == state["current_idx"] else p
                             for i, p in enumerate(state["plan"])],
                    "current_idx": state["current_idx"],
                    "next_step": "diagnostics" if rc[state["current_idx"]] > self.MAX_STEP_RETRIES else "generator",
                    "messages": [AIMessage(content=f"<STATUS:blocked>\n{summary[:600]}")],
                    "manifest": new_manifest,
                    "retry_counts": rc,
                    "diagnostic_mode": False,
                    "diagnostic_code": None,
                    "diagnostic_observation": None,
                }

            # FileNotFoundError: file path wrong
            _fnf_m = re.search(r"FileNotFoundError.*?'([^']+)'", last_result)
            if not diagnostic_mode and _fnf_m and not _is_missing_package:
                _bad_path = _fnf_m.group(1)
                _files_hint = f"Actual files in run_dir: {_manifest_files}" if _manifest_files else ""
                summary = (
                    f"FileNotFoundError: '{os.path.basename(_bad_path)}' not found. "
                    f"The filename was invented — use glob to find the real file. "
                    f"{_files_hint}. "
                    f"Fix: fasta_path = glob.glob(os.path.join(run_dir, '*.fna'))[0]  "
                    f"— never hardcode filenames."
                )
                self._log("FAST-PATH FileNotFoundError", body=summary, node=node)
                new_manifest = self._clean_manifest(state["manifest"])
                rc = {int(k): int(v) for k, v in (state.get("retry_counts") or {}).items() if str(k).isdigit()}
                rc[state["current_idx"]] = rc.get(state["current_idx"], 0) + 1
                new_manifest["repair_feedback"] = summary
                new_manifest["repair_step_idx"] = state["current_idx"]
                return {
                    "plan": [{**p, "status": "blocked"} if i == state["current_idx"] else p
                             for i, p in enumerate(state["plan"])],
                    "current_idx": state["current_idx"],
                    "next_step": "diagnostics" if rc[state["current_idx"]] > self.MAX_STEP_RETRIES else "generator",
                    "messages": [AIMessage(content=f"<STATUS:blocked>\n{summary}")],
                    "manifest": new_manifest,
                    "retry_counts": rc,
                    "diagnostic_mode": False,
                    "diagnostic_code": None,
                    "diagnostic_observation": None,
                }

            if not diagnostic_mode and _is_missing_package:
                pkg_m = re.search(r"No module named '([^']+)'", last_result)
                missing_pkg = pkg_m.group(1) if pkg_m else "unknown"
                # genomeer is the agent's internal package — it is never on PyPI and
                # must never be imported in generated code.  Give targeted guidance
                # instead of the generic "pip install" advice which the LLM can't follow.
                if missing_pkg.startswith("genomeer"):
                    summary = (
                        f"ModuleNotFoundError: '{missing_pkg}' is an internal agent module "
                        f"and does NOT exist in the execution environment (micromamba). "
                        f"NEVER import from genomeer.* in generated code. "
                        f"Remove the import entirely and use only standard libraries "
                        f"(os, glob, subprocess, sys, shutil, gzip, pathlib) and "
                        f"conda packages (biopython, ncbi-genome-download, etc.)."
                    )
                elif missing_pkg == "Bio" or missing_pkg.startswith("Bio."):
                    summary = (
                        f"ModuleNotFoundError: 'Bio' (biopython) is not installed in this environment. "
                        f"Missing module: '{missing_pkg}'. "
                        f"STRICT REPAIR RULE — do NOT retry with any Bio.* import. "
                        f"Rewrite using the standard library only:\n"
                        f"  1. Parse FASTA with a plain for-loop (no SeqIO, no SeqRecord, no Seq):\n"
                        f"       records = []\n"
                        f"       with open(fasta_path) as _f:\n"
                        f"           _sid, _seq = None, []\n"
                        f"           for _line in _f:\n"
                        f"               _line = _line.rstrip()\n"
                        f"               if _line.startswith('>'):\n"
                        f"                   if _sid: records.append((_sid, ''.join(_seq)))\n"
                        f"                   _sid, _seq = _line[1:].split()[0], []\n"
                        f"               else: _seq.append(_line)\n"
                        f"           if _sid: records.append((_sid, ''.join(_seq)))\n"
                        f"  2. Compute lengths as: lengths = [len(seq) for _, seq in records]\n"
                        f"  3. Never import from Bio.* — not even 'from Bio import SeqIO'."
                    )
                else:
                    summary = (
                        f"ModuleNotFoundError: package '{missing_pkg}' is not available in the environment. "
                        f"Fix: use subprocess.run([sys.executable, '-m', 'pip', 'install', '{missing_pkg}']) "
                        f"at the top of the script, then re-import. "
                        f"Do NOT ask the user to install anything."
                    )
                self._log("FAST-PATH ModuleNotFoundError", body=summary, node=node)
                new_manifest = self._clean_manifest(state["manifest"])
                rc = {int(k): int(v) for k, v in (state.get("retry_counts") or {}).items() if str(k).isdigit()}
                rc[state["current_idx"]] = rc.get(state["current_idx"], 0) + 1
                new_manifest["repair_feedback"] = summary
                new_manifest["repair_step_idx"] = state["current_idx"]
                next_step = "diagnostics" if rc[state["current_idx"]] > self.MAX_STEP_RETRIES else "generator"
                plan = list(state["plan"])
                plan[state["current_idx"]] = {**plan[state["current_idx"]], "notes": summary, "status": "blocked"}
                return {
                    "plan": plan,
                    "current_idx": state["current_idx"],
                    "next_step": next_step,
                    "messages": [AIMessage(content=f"<STATUS:blocked>\n{summary}")],
                    "manifest": new_manifest,
                    "retry_counts": rc,
                    "diagnostic_mode": False,
                    "diagnostic_code": None,
                    "diagnostic_observation": None,
                }
            elif not diagnostic_mode and _is_bad_import:
                # ImportError on a name that doesn't exist in an installed module.
                # Extract what was hallucinated and give precise repair guidance.
                bad_m = re.search(r"cannot import name '([^']+)' from '([^']+)'", last_result)
                bad_name = bad_m.group(1) if bad_m else "unknown"
                bad_module = bad_m.group(2) if bad_m else "unknown"
                summary = (
                    f"ImportError: '{bad_name}' does not exist in '{bad_module}'. "
                    f"The code used a hallucinated API. "
                    f"Fix: look up the correct class/function name in the module. "
                    f"For Bio.ExPASy use: handle = ExPASy.get_sprot_raw(accession); record = SwissProt.read(handle). "
                    f"For Bio.Entrez use: Entrez.email='x@y.com'; handle = Entrez.efetch(db=..., id=..., rettype=...). "
                    f"Do NOT invent new class names."
                )
                self._log("FAST-PATH ImportError (hallucinated name)", body=summary, node=node)
                new_manifest = self._clean_manifest(state["manifest"])
                rc = {int(k): int(v) for k, v in (state.get("retry_counts") or {}).items() if str(k).isdigit()}
                rc[state["current_idx"]] = rc.get(state["current_idx"], 0) + 1
                new_manifest["retry_count"] = rc[state["current_idx"]]
                new_manifest["repair_feedback"] = summary
                new_manifest["repair_step_idx"] = state["current_idx"]
                next_step = "diagnostics" if rc[state["current_idx"]] > self.MAX_STEP_RETRIES else "generator"
                plan = list(state["plan"])
                plan[state["current_idx"]] = {**plan[state["current_idx"]], "notes": summary, "status": "blocked"}
                return {
                    "plan": plan,
                    "current_idx": state["current_idx"],
                    "next_step": next_step,
                    "messages": [AIMessage(content=f"<STATUS:blocked>\n{summary}")],
                    "manifest": new_manifest,
                    "retry_counts": rc,
                    "diagnostic_mode": False,
                    "diagnostic_code": None,
                    "diagnostic_observation": None,
                }

            # Fix G4 / G10 — run biological quality gates before LLM
            _gate_fails = []
            _gate_warns = []
            if not diagnostic_mode:
                _code_for_gate = (state.get("pending_code") or "").strip()
                for _gt_name in BIOLOGICAL_GATES:
                    if re.search(r'\b' + re.escape(_gt_name) + r'\b', _code_for_gate):
                        _gate_level, _gate_msg = check_quality(_gt_name, None, last_result)
                        if _gate_level == "fail":
                            _gate_fails.append(_gate_msg)   # G10: collect ALL fails
                        elif _gate_level == "warn":
                            _gate_warns.append(_gate_msg)

            # BUG-36 — coerce any extracted quality_signals to float; drop unconvertible
            _quality_signals: dict = {}
            for _qmsg in _gate_fails + _gate_warns:
                _qm = re.search(r"([a-z][a-z0-9_]*)\s*[=:]\s*([\d.]+)", _qmsg, re.IGNORECASE)
                if _qm:
                    try:
                        _quality_signals[_qm.group(1)] = float(_qm.group(2))
                    except (ValueError, TypeError):
                        self._log("QA-WARN", body=f"quality_signal not convertible to float: {_qm.group(2)}", node=node)

            # G4: hard fail → force STATUS:blocked without calling LLM
            if _gate_fails:
                _gate_summary = "\n".join(_gate_fails)
                self._log("QUALITY GATE FAIL", body=_gate_summary, node=node)
                new_manifest = self._clean_manifest(state["manifest"])
                if _quality_signals:
                    new_manifest["quality_signals"] = _quality_signals
                rc = {int(k): int(v) for k, v in (state.get("retry_counts") or {}).items() if str(k).isdigit()}
                rc[state["current_idx"]] = rc.get(state["current_idx"], 0) + 1
                new_manifest["repair_feedback"] = f"QUALITY_GATE_FAIL:\n{_gate_summary}"
                new_manifest["repair_step_idx"] = state["current_idx"]
                _next = "diagnostics" if rc[state["current_idx"]] > self.MAX_STEP_RETRIES else "generator"
                return {
                    "plan": [{**p, "status": "blocked"} if i == state["current_idx"] else p
                             for i, p in enumerate(state["plan"])],
                    "current_idx": state["current_idx"],
                    "next_step": _next,
                    "manifest": new_manifest,
                    "retry_counts": rc,
                    "diagnostic_mode": False,
                    "diagnostic_code": None,
                    "diagnostic_observation": None,
                    "messages": [AIMessage(content=f"<STATUS:blocked>\n{_gate_summary}")],
                }

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
                _gate_warn_note = ("\n\nQUALITY GATE WARNINGS:\n" + "\n".join(_gate_warns)) if _gate_warns else ""
                payload = instructions.OBSERVER_CTX_PROMPT.format(
                    user_goal=state['last_prompt'],
                    current_step_title=step['title'],
                    manifest=state['manifest'],
                    code=(state.get("pending_code") or "").strip(),
                    result=state['last_result'],
                ) + _gate_warn_note
            msgs = [
                self.system_prompt,
                HumanMessage(content=instructions.OBSERVER_PROMPT),
                HumanMessage(content=payload),
            ]

            self._log("ENTER NODE", body=f"step_idx={state['current_idx']}\nstep_title={step['title']}", node=node)
            resp = self._llm_invoke(node, "observe_and_status", msgs)

            status, summary = StateGraphHelper.parse_status(resp.content)

            # Fix T11.2 — second LLM call + keyword inference if STATUS tag missing
            if summary.startswith("OBSERVER_FORMAT_ERROR"):
                _retry_msgs = [
                    self.system_prompt,
                    HumanMessage(content=(
                        "Respond with ONLY one of these two lines — nothing else:\n"
                        "  <STATUS:done>\n"
                        "  <STATUS:blocked> <one sentence: what failed and how to fix it>\n\n"
                        f"Execution output:\n{last_result[:800]}"
                    )),
                ]
                try:
                    _retry_resp = self._llm_invoke(node, "observe_status_retry", _retry_msgs)
                    status, summary = StateGraphHelper.parse_status(_retry_resp.content)
                except Exception:
                    pass
                if summary.startswith("OBSERVER_FORMAT_ERROR"):
                    _raw = resp.content.lower()
                    if any(w in _raw for w in ("success", "done", "complete", "finished", "saved", "written")):
                        status, summary = "done", resp.content[:300]
                    else:
                        status, summary = "blocked", resp.content[:300]
            next_step = "generator" if status == "blocked" else "orchestrator"
            next_idx = state["current_idx"] + (0 if status == "blocked" else 1)
            
            new_manifest = self._clean_manifest(state["manifest"])
            rc = {int(k): int(v) for k, v in (state.get("retry_counts") or {}).items() if str(k).isdigit()}
            diag_rounds = dict(state["manifest"].get("diagnostics_rounds") or {})
            if status == "blocked":
                rc[state["current_idx"]] = rc.get(state["current_idx"], 0) + 1
                new_manifest["retry_count"] = rc[state["current_idx"]]
                _raw_output = (last_result or "").strip()
                new_manifest["repair_feedback"] = (
                    f"{summary}\n\nEXECUTION OUTPUT:\n{_raw_output[:1000]}"
                    if _raw_output else summary
                )
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

                # BUG-36 — persist quality_signals (already coerced to float above)
                if _quality_signals:
                    new_manifest["quality_signals"] = _quality_signals

                # routing
                next_step = "orchestrator"
                next_idx = state["current_idx"] + 1
                if state["current_idx"] in rc:
                    rc.pop(state["current_idx"], None)

                # logs
                self._log("STATUS", body=f"done=True\nnotes=\n{summary}", node=node)
                self._log("EXIT NODE", body=f"advance_to_idx={state['current_idx']}\nnext_step=orchestrator", node=node)

                # storing success state observation
                obs = {
                    "step_idx": state["current_idx"],
                    "title": step["title"],
                    "status": status,
                    "summary": summary,
                    "stdout": (state.get("last_result") or "")[:12000],
                    "files_snapshot": self._list_ctx_files(state.get("run_temp_dir","")),
                }
                new_manifest["observations"] = list(new_manifest.get("observations", [])) + [obs]

                # Fix 2 + Fix 6 — auto-enrich manifest with exact file paths after each done step.
                _temp_dir = state.get("run_temp_dir", "")
                _file_registry = self._build_file_registry(_temp_dir)
                new_manifest["file_registry"] = _file_registry
                new_manifest["files_by_ext"] = _file_registry  # backward compat alias
                new_manifest["files"] = [
                    n for names in _file_registry.values() for n in names
                ]
                self._log("MANIFEST ENRICHED", body=f"file_registry: {_file_registry}", node=node)


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
                "diagnostic_code": None,
                "diagnostic_observation": None,
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
            import tempfile as _tf
            node = "diagnostics"
            step = self._get_current_step(state)
            manifest = state.get("manifest", {}) or {}
            retry_count = manifest.get("retry_count", 0)
            observer_summary = manifest.get("repair_feedback", "").strip()
            last_code = (state.get("pending_code") or "").strip()

            # BUG-49 — fallback to system tempdir if run_temp_dir is absent
            run_temp_dir = state.get("run_temp_dir") or _tf.gettempdir()

            # Fix diag_rounds — increment counter and persist in manifest
            diag_rounds = dict(manifest.get("diagnostics_rounds") or {})
            _idx = state.get("current_idx", 0)
            diag_rounds[_idx] = diag_rounds.get(_idx, 0) + 1

            # Fix MAX_DIAG cap — route to QA when rounds exceed the limit
            if diag_rounds[_idx] > self.MAX_DIAG_ROUNDS_PER_STEP:
                self._log(
                    "DIAGNOSTICS CAP REACHED",
                    body=f"step={step['title']} rounds={diag_rounds[_idx]} > {self.MAX_DIAG_ROUNDS_PER_STEP} → QA",
                    node=node,
                )
                new_manifest = dict(manifest)
                new_manifest["diagnostics_rounds"] = diag_rounds
                new_manifest["repair_feedback"] = (
                    f"DIAGNOSTICS_CAP: step '{step['title']}' failed {diag_rounds[_idx]} diagnostic rounds "
                    f"(limit={self.MAX_DIAG_ROUNDS_PER_STEP}). Escalating to QA for human review."
                )
                return {
                    "manifest": new_manifest,
                    "diagnostic_mode": False,
                    "next_step": "qa",
                    "messages": [AIMessage(content=new_manifest["repair_feedback"])],
                }

            prompt = instructions.DIAGNOSTICS_PROMPT
            ctx = instructions.DIAGNOSTICS_CTX_PROMPT.format(
                user_goal=state.get("last_prompt",""),
                current_step_title=step["title"],
                retry_count=retry_count,
                observer_summary=observer_summary or "<none>",
                last_code=last_code or "<none>",
                run_temp_dir=run_temp_dir,
            )

            msgs = [
                self.system_prompt,
                HumanMessage(content=prompt),
                HumanMessage(content=ctx)
            ]
            self._log("ENTER NODE", body=f"retry_count={retry_count}\ndiag_round={diag_rounds[_idx]}\nstep={step['title']}", node=node)
            resp = self._llm_invoke(node, "diagnostics_plan", msgs)

            # Reuse GENERATOR to actually produce the probe code
            # We piggyback repair flow by stuffing the plan into 'repair_feedback'
            new_manifest = dict(manifest)
            new_manifest["repair_feedback"] = f"DIAGNOSTICS_REQUEST:\n{resp.content}"
            new_manifest["repair_step_idx"] = state["current_idx"]
            new_manifest["diagnostics_rounds"] = diag_rounds

            self._log("EXIT NODE", body="next_step=generator (probe code)", node=node)

            rc = {int(k): int(v) for k, v in (state.get("retry_counts") or {}).items() if str(k).isdigit()}
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
            temp_dir = state.get("run_temp_dir") or ""
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

            # ── BioRAG context injection ─────────────────────────────────────────
            _rag_context = ""
            try:
                from pathlib import Path as _RAGPath
                _rag_store = BioRAGStore(
                    persist_dir=str(_RAGPath.home() / ".genomeer" / "rag_cache")
                )
                _rag_retriever = BioRAGRetriever(_rag_store)
                _quality_signals = manifest.get("quality_signals") or {}
                _pipeline_results = {
                    "amr_genes":         manifest.get("amr_genes_detected", []),
                    "pathways":          manifest.get("pathways", []),
                    "assembly_n50":      _quality_signals.get("n50_bp"),
                    "mean_completeness": _quality_signals.get("mean_completeness"),
                }
                _rag_context = build_finalizer_rag_context(_rag_retriever, _pipeline_results)
                if _rag_context:
                    self._log(
                        "RAG CONTEXT",
                        body=f"BioRAG active — {len(_rag_context)} chars injected",
                        node=node,
                    )
                else:
                    self._log("RAG CONTEXT", body="BioRAG returned empty context", node=node)
            except Exception as _rag_err:
                self._log(
                    "RAG DEGRADED",
                    body=f"BioRAG failed (non-fatal, continuing without RAG): {_rag_err}",
                    node=node,
                )
                _rag_context = ""
            # ── END BioRAG ───────────────────────────────────────────────────────

            _finalizer_system_prompt = instructions.FINALIZER_PROMPT
            if _rag_context:
                _finalizer_system_prompt = _finalizer_system_prompt + "\n\n" + _rag_context

            msgs = [
                SystemMessage(content=_finalizer_system_prompt),
                HumanMessage(content=instructions.FINALIZER_CTX_PROMPT.format(
                    user_goal=state.get("last_prompt"),
                    plan=state.get("plan"),
                    observation=observations,
                    artifacts=artifacts
                ))
            ]
            resp = self._llm_invoke(node, "final_report", msgs)
            self._log("EXIT NODE", body="final report generated", node=node)

            # ── Phase 4: persist run memory ──────────────────────────────────
            try:
                import json as _json
                from pathlib import Path as _Path
                from datetime import datetime as _dt

                _mem_dir = _Path.home() / ".genomeer"
                _mem_dir.mkdir(parents=True, exist_ok=True)
                _mem_file = _mem_dir / "runs_memory.jsonl"

                _plan = state.get("plan") or []
                _scores = {
                    obs["title"]: obs.get("score", -1.0)
                    for obs in observations
                    if "score" in obs
                }
                _winning_params = {
                    obs["title"]: obs.get("summary", "")
                    for obs in observations
                    if obs.get("status") == "done"
                }
                _task_type = self._infer_task_type(state.get("last_prompt") or "")

                _record = {
                    "timestamp":    _dt.utcnow().isoformat(),
                    "run_id":       run_id,
                    "task_type":    _task_type,
                    "user_goal":    (state.get("last_prompt") or "")[:300],
                    "plan":         [{"title": s["title"], "status": s.get("status")} for s in _plan],
                    "scores":       _scores,
                    "params":       _winning_params,
                    "done_count":   sum(1 for s in _plan if s.get("status") == "done"),
                    "total_steps":  len(_plan),
                }
                with open(_mem_file, "a", encoding="utf-8") as _fh:
                    _fh.write(_json.dumps(_record) + "\n")
                self._log("MEMORY WRITE", body=f"appended to {_mem_file}", node=node)
            except Exception as _me:
                self._log("MEMORY WRITE ERROR", body=str(_me), node=node)

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
        self.batch_orchestrator = types.MethodType(_batch_orchestrator, self)
        self.input_guard = types.MethodType(_input_guard, self)
        self.generator = types.MethodType(_generator, self)
        self.ensure_env = types.MethodType(_ensure_env, self)
        self.executor = types.MethodType(_executor, self)
        self.validator = types.MethodType(_validator, self)
        self.observer = types.MethodType(_observer, self)
        self.diagnostics = types.MethodType(_diagnostics, self)
        self.finalizer = types.MethodType(_finalizer, self)
        self._get_current_step = types.MethodType(_get_current_step, self)
        
        # Create the workflow
        # --------------------------------------------------------------------------------
        workflow = StateGraph(AgentState)
        workflow.add_node("planner", self.planner)
        workflow.add_node("qa", self.qa)
        workflow.add_node("orchestrator", self.orchestrator)
        workflow.add_node("batch_orchestrator", self.batch_orchestrator)
        workflow.add_node("input_guard", self.input_guard)
        workflow.add_node("generator", self.generator)
        workflow.add_node("ensure_env", self.ensure_env)
        workflow.add_node("executor", self.executor)
        workflow.add_node("validator", self.validator)
        workflow.add_node("observer", self.observer)
        workflow.add_node("diagnostics", self.diagnostics)
        workflow.add_node("finalizer", self.finalizer)

        # ── bio_hint optional node ────────────────────────────────────────────
        # Added only when a secondary domain LLM is configured.
        # When bio_hint_llm is None the graph is completely unchanged.
        _bio_hint_active = self.bio_hint_llm is not None
        if _bio_hint_active:
            from genomeer.agent.v2.utils.bio_hint import BioHintNode, make_bio_hint_router
            _bio_hint_node = BioHintNode(llm=self.bio_hint_llm, log_fn=self._log)
            workflow.add_node("bio_hint_node", _bio_hint_node)
            _route_via_bio_hint = make_bio_hint_router("bio_hint_node")

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
                "planner":            "planner",
                "input_guard":        "input_guard",
                "finalizer":          "finalizer",
                "batch_orchestrator": "batch_orchestrator",
            },
        )
        workflow.add_conditional_edges(
            "batch_orchestrator",
            lambda s: s["next_step"],
            {
                "finalizer": "finalizer",
            },
        )
        workflow.add_conditional_edges(
            "input_guard",
            _route_via_bio_hint if _bio_hint_active else (lambda s: s["next_step"]),
            {
                "qa": "qa",
                "generator": "generator",
                **( {"bio_hint_node": "bio_hint_node"} if _bio_hint_active else {} ),
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
                "validator": "validator",
                "generator": "generator",
            },
        )
        workflow.add_conditional_edges(
            "validator",
            lambda s: s["next_step"],
            {
                "observer":     "observer",
                "orchestrator": "orchestrator",
                "generator":    "generator",
            },
        )
        workflow.add_conditional_edges(
            "observer",
            _route_via_bio_hint if _bio_hint_active else (lambda s: s["next_step"]),
            {
                "orchestrator": "orchestrator",
                "generator": "generator",
                "diagnostics": "diagnostics",
                "qa": "qa",
                **( {"bio_hint_node": "bio_hint_node"} if _bio_hint_active else {} ),
            },
        )
        workflow.add_conditional_edges(
            "diagnostics",
            lambda s: s["next_step"],
            {
                "generator": "generator",
                "qa": "qa",
                "end": END,
            },
        )
        # bio_hint always routes to generator (no other target)
        if _bio_hint_active:
            workflow.add_conditional_edges(
                "bio_hint_node",
                lambda s: s["next_step"],
                {"generator": "generator"},
            )

        workflow.add_edge("qa", END)
        workflow.add_edge("finalizer", END)
        
        # Compile the workflow
        # --------------------------------------------------------------------------------
        self.app = workflow.compile()
        self.checkpointer = MemorySaver()
        self.app.checkpointer = self.checkpointer


    # OTHER UTILS
    def _enrich_prompt_with_uploads(self, prompt: str, staged: list[str]) -> str:
        """
        Surface the just-staged upload paths inside the user prompt with an
        explicit routing directive for the planner. Without this, terse user
        prompts ("??", "explain", "expplain") combined with an attached file
        cause the planner LLM to emit <next:QA> instead of <next:ORCHESTRATOR>,
        and the file is never opened.

        - When staged is empty, returns prompt unchanged (no-op).
        - When staged is non-empty, appends an "[ATTACHMENTS]" block at the END
          of the prompt so the planner extracts the original question + sees
          the directive.

        Kill-switch: GENOMEER_UPLOAD_ROUTING=0 disables the enrichment.
        """
        if not staged:
            return prompt
        if os.environ.get("GENOMEER_UPLOAD_ROUTING", "1") == "0":
            return prompt
        files_block = "\n".join(f"  - {p}" for p in staged)
        directive = (
            f"\n\n---\n"
            f"[ATTACHMENTS — the user uploaded the following file(s) this turn; "
            f"they are now in the run directory and MUST be opened/inspected:\n"
            f"{files_block}\n"
            f"ROUTING DIRECTIVE: emit <next:ORCHESTRATOR> and create at least "
            f"one step that reads each file with real code. Do NOT emit "
            f"<next:QA> when fresh uploads exist. Do NOT answer from the "
            f"filename or from memory — open the file.]"
        )
        return prompt + directive

    def _stage_attachments(self, tmp_dir: str, attachments: list) -> list[str]:
        """
        Copy user-supplied file paths into the run's temp dir.
        Returns the relative paths inside tmp_dir.

        Accepts heterogeneous item types so the UI layer can pass any of:
          - str                          (absolute file path)
          - dict  with key "path"        (legacy JSON shape)
          - Pydantic BaseModel / object  (has .path attribute, e.g. AttachmentIn)
        Items without a usable path are skipped with a log entry instead of
        being silently consumed by the broad except clause.
        """
        def _coerce(item) -> str | None:
            # Direct string
            if isinstance(item, str):
                return item
            # Pydantic model / arbitrary object exposing .path
            p = getattr(item, "path", None)
            if isinstance(p, str) and p:
                return p
            # Plain dict (from JSON)
            if isinstance(item, dict):
                cand = item.get("path") or item.get("name")
                if isinstance(cand, str) and cand:
                    return cand
            return None

        staged_rel: list[str] = []
        up = os.path.join(tmp_dir, "uploads")
        os.makedirs(up, exist_ok=True)
        for raw in attachments or []:
            src = _coerce(raw)
            if not src:
                self._log("ATTACH SKIP", body=f"unsupported attachment shape: {type(raw).__name__}", node="driver")
                continue
            try:
                if not os.path.isfile(src):
                    self._log("ATTACH SKIP", body=f"not a file: {src}", node="driver")
                    continue
                bn = os.path.basename(src)
                dst = os.path.join(up, bn)
                shutil.copy2(src, dst)
                staged_rel.append(os.path.relpath(dst, tmp_dir))
                self._log("ATTACH STAGED", body=f"{src} -> {dst}", node="driver")
            except Exception as e:
                self._log("ATTACH STAGE ERROR", body=f"{src}: {e}", node="driver")
        return staged_rel

    def _stage_prompt_files(self, prompt: str, tmp_dir: str) -> list[str]:
        """
        Scan the prompt for absolute file paths (Unix and Windows, quoted or unquoted)
        and copy them into tmp_dir so generated code can find them without path-with-spaces issues.
        Returns list of staged destination paths.
        """
        import re as _re
        staged: list[str] = []
        _bio_ext = r'(?:fasta|fna|fastq|fa|fq|tsv|gff|gff3|faa|bam|vcf|bed|txt|csv|json|gz|png|pdf)'
        # Unix paths — quoted or bare, spaces allowed (common on /mnt/c/ WSL mounts)
        unix_rx  = _re.compile(r'(/[^\n"\']+\.' + _bio_ext + r')\b', _re.IGNORECASE)
        # Windows paths: C:\path\to\file.ext (with or without surrounding quotes)
        win_rx   = _re.compile(r'["\']?([A-Za-z]:\\[^"\']+\.[a-zA-Z0-9]+)["\']?')
        candidates: list[str] = []
        for m in unix_rx.finditer(prompt):
            candidates.append(m.group(1).strip())
        for m in win_rx.finditer(prompt):
            candidates.append(m.group(1).strip())
        seen: set[str] = set()
        for src in candidates:
            if src in seen:
                continue
            seen.add(src)
            try:
                if not os.path.isfile(src):
                    continue
                bn = os.path.basename(src)
                dst = os.path.join(tmp_dir, bn)
                if not os.path.exists(dst):
                    shutil.copy2(src, dst)
                staged.append(dst)
                self._log("PROMPT FILE STAGED", body=f"{src} → {dst}", node="driver")
            except Exception as e:
                self._log("PROMPT STAGE SKIP", body=f"{src}: {e}", node="driver")
        return staged

    @staticmethod
    def _infer_task_type(prompt: str) -> str:
        """Map a user prompt to a coarse task category for runs_memory indexing."""
        p = prompt.lower()
        if any(k in p for k in ("metagenom", "binning", "bin", "kraken", "assembly", "assemble")):
            return "metagenomics"
        if any(k in p for k in ("rnaseq", "rna-seq", "deseq", "differential expression", "transcriptom")):
            return "rnaseq"
        if any(k in p for k in ("variant", "snp", "snv", "gatk", "vcf", "mutation")):
            return "variant_calling"
        if any(k in p for k in ("chip-seq", "chipseq", "atac", "peak")):
            return "epigenomics"
        if any(k in p for k in ("16s", "amplicon", "qiime", "otu", "asv")):
            return "amplicon"
        return "general"

    def _load_past_templates(self, prompt: str, max_records: int = 5) -> str:
        """
        Phase 4 — load the last N successful runs of the same task_type from
        ~/.genomeer/runs_memory.jsonl and return a formatted string to inject
        into the planner prompt.

        Returns an empty string if the file doesn't exist or has no matches.
        """
        import json as _json
        from pathlib import Path as _Path

        mem_file = _Path.home() / ".genomeer" / "runs_memory.jsonl"
        if not mem_file.exists():
            return ""

        task_type = self._infer_task_type(prompt)
        matching = []
        try:
            with open(mem_file, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = _json.loads(line)
                    except Exception:
                        continue
                    if rec.get("task_type") == task_type and rec.get("done_count", 0) > 0:
                        matching.append(rec)
        except Exception:
            return ""

        if not matching:
            return ""

        # Keep the most recent N records
        recent = matching[-max_records:]

        lines = [
            "\n\n---\nPAST SUCCESSFUL RUNS (use as reference templates, do not copy blindly):",
        ]
        for i, rec in enumerate(recent, 1):
            steps = " → ".join(s["title"] for s in rec.get("plan", []))
            scores_str = ", ".join(
                f"{k}: {v:.2f}" for k, v in rec.get("scores", {}).items() if v >= 0
            )
            lines.append(
                f"\n[Run {i}] goal: {rec.get('user_goal','')[:120]}\n"
                f"  steps: {steps}\n"
                f"  scores: {scores_str or 'n/a'}"
            )
        lines.append("---\n")
        return "\n".join(lines)

    @staticmethod
    def _clean_manifest(manifest: dict) -> dict:
        """Return a copy of manifest with all stale routing keys removed.
        Call this on every blocked-path manifest copy to prevent route_hint
        from a previous iteration hijacking the planner on the next cycle.
        """
        m = dict(manifest)
        for k in ("route_hint", "qa_payload", "resume_to", "pause_kind"):
            m.pop(k, None)
        return m

    def _build_file_registry(self, temp_dir: str) -> dict:
        """Fix 2 — Build {ext: [basename, ...]} from current run_dir contents.

        Returns e.g. {'.fna': ['GCF_000009045.1_ASM904v1_genomic.fna'],
                       '.png': ['genome_comparison.png'], ...}
        Sorted by extension so output is deterministic.
        """
        registry: dict = {}
        if not temp_dir or not os.path.isdir(temp_dir):
            return registry
        import glob as _gl
        for fp in sorted(_gl.glob(os.path.join(temp_dir, "*"))):
            if os.path.isfile(fp):
                bn  = os.path.basename(fp)
                ext = os.path.splitext(bn)[1].lower() or ".noext"
                registry.setdefault(ext, []).append(bn)
        return registry

    def _list_ctx_files(self, temp_dir: str, extra_paths: list = None):
        """
        - This function will return a list of all files available in the the current run temp folder
        - Indeed each request have a temp storage folder - ex: `/tmp/206005a0-c0a1-4114-907c-c3eda23d3f32`
        - All uploaded file will be inside automatically and all downloaded file by agent will be there.
        - FIX: also scans absolute paths mentioned in the prompt (extra_paths)
        Return a list of all files inside temp_dir (including subfolders).
        Each item: {'name': 'relative/path/to/file', 'ext': '.fasta', 'size_bytes': 123}
        """
        files = []
        try:
            for root, _, entries in os.walk(temp_dir):
                for entry in sorted(entries):
                    p = os.path.join(root, entry)
                    if os.path.isfile(p):
                        rel_path = os.path.relpath(p, temp_dir)
                        ext = os.path.splitext(entry)[1]
                        files.append({
                            "name": rel_path,
                            "ext": ext if ext else "",
                            "size_bytes": os.path.getsize(p),
                        })
        except Exception as e:
            self._log("TEMP LIST ERROR", body=str(e), node="input_guard")

        # FIX: aussi chercher les fichiers absolus mentionnés dans le prompt
        import shutil
        for abs_path in (extra_paths or []):
            abs_path = abs_path.strip()
            if os.path.isfile(abs_path):
                entry = os.path.basename(abs_path)
                dst = os.path.join(temp_dir, entry)
                if not os.path.exists(dst):
                    shutil.copy2(abs_path, dst)
                    self._log("AUTO STAGE", body=f"Copied {abs_path} → {dst}", node="input_guard")
                ext = os.path.splitext(entry)[1]
                files.append({
                    "name": entry,
                    "ext": ext if ext else "",
                    "size_bytes": os.path.getsize(dst),
                })
        return files
    
    def _inject_custom_functions_to_repl(self):
        """Inject custom functions into the Python REPL execution environment.
        This makes custom tools available during code execution.
        """
        from genomeer.utils.helper import _persistent_namespace

        # Fix 9 — always inject list_files / get_file into the REPL namespace so
        # generated code can call them without an explicit import.
        try:
            from genomeer.utils.filesystem import list_files as _lf, get_file as _gf
            _persistent_namespace["list_files"] = _lf
            _persistent_namespace["get_file"] = _gf
        except ImportError:
            pass

        if hasattr(self, "_custom_functions") and self._custom_functions:
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

    def _next_turn_id(self, thread_id: str) -> int:
        """Multi-turn isolation: compute the turn_id for the call about to start.
        Returns 1 for the first turn of a session, prev+1 for follow-ups.
        Robust to checkpointer/read errors -> always returns at least 1.
        """
        try:
            state = self.app.get_state({"configurable": {"thread_id": thread_id}})
            if state and state.values:
                return int(state.values.get("turn_id", 0)) + 1
        except Exception:
            pass
        return 1

    def _inject_missing_imports(self, code: str) -> str:
        """
        Each step runs in a fresh subprocess — imports from previous steps are gone.
        Detect common symbols used but not imported and prepend the missing import statements.
        Works in both normal and repair mode (with or without #!PY header).
        """
        if not code:
            return code
        has_py_header = "#!PY" in code[:15]

        # Map: symbol used in code → import statement to inject
        _KNOWN = [
            (r"\bos\b",          "import os"),
            (r"\bsys\b",         "import sys"),
            (r"\bre\b",          "import re"),
            (r"\bjson\b",        "import json"),
            (r"\bcsv\b",         "import csv"),
            (r"\bglob\b",        "import glob"),
            (r"\bgzip\b",        "import gzip"),
            (r"\bshutil\b",      "import shutil"),
            (r"\bPath\b",        "from pathlib import Path"),
            (r"\bSeqIO\b",       "from Bio import SeqIO"),
            (r"\bSeqRecord\b",   "from Bio.SeqRecord import SeqRecord"),
            (r"\bSeq\b",         "from Bio.Seq import Seq"),
            (r"\bpd\b",          "import pandas as pd"),
            (r"\bnp\b",          "import numpy as np"),
            (r"\bplt\b",         "import matplotlib.pyplot as plt"),
            (r"\bsubprocess\b",  "import subprocess"),
            (r"\btime\b",        "import time"),
            (r"\bStringIO\b",    "from io import StringIO"),
        ]

        to_add = []
        for symbol_rx, stmt in _KNOWN:
            if re.search(symbol_rx, code) and stmt not in code:
                to_add.append(stmt)

        if not to_add:
            return code

        header = "\n".join(to_add) + "\n\n"
        if has_py_header:
            # Insert right after the #!PY line
            return re.sub(r"(#!PY\s*\n)", r"\g<1>" + header, code, count=1)
        else:
            # No #!PY header (repair mode without lang marker) — prepend directly
            return header + code

    def _fix_cli_commands(self, code: str) -> str:
        """
        Deterministic corrections for hallucinated CLI flags — model-agnostic.
        Runs before execution so wrong commands never reach the shell.
        """
        if not code:
            return code

        # ── ncbi-genome-download ────────────────────────────────────────────
        if "ncbi-genome-download" in code:
            # --dry-run causes TimeoutExpired (slow network check) and is never needed.
            code = re.sub(r'[\s]*["\']--dry-run["\'][\s]*,?', '', code)
            code = re.sub(r'\s+--dry-run\b', '', code)
            # --genus → --genera  (--genus is a long-deprecated alias, use canonical)
            code = re.sub(r"--genus\b", "--genera", code)
            # --species <name>  does not exist → remove entirely
            code = re.sub(r"\s+--species\s+\S+", " ", code)
            # --organism <name>  does not exist → remove entirely
            code = re.sub(r"\s+--organism\s+\S+", " ", code)
            # --name <name>  does not exist → remove entirely
            code = re.sub(r"\s+--name\s+\S+", " ", code)

            # CRITICAL SAFETY: when --genera is used without --assembly-levels complete,
            # the tool lists ALL assemblies for the whole kingdom (thousands of entries)
            # which causes hours-long hangs and accidental mass downloads.
            # Deterministically inject --assembly-levels complete and --section refseq
            # whenever --genera is present but --assembly-levels is absent.
            # This guard does NOT apply when --assembly-accessions is used (no risk there).
            if re.search(r"--genera\b", code) and not re.search(r"--assembly-levels\b|--assembly_levels\b", code):
                # List-style command: inject after --genera value token
                # Pattern: "--genera", "<value>" → add flags after the value
                # Also handles: --genera "Organism name" on CLI lines
                def _inject_levels_after_genera(m: re.Match) -> str:
                    return m.group(0) + ', "--assembly-levels", "complete", "--section", "refseq",'
                new_code = re.sub(
                    r'(\"--genera\"\s*,\s*[^,\]]+)',
                    _inject_levels_after_genera,
                    code,
                )
                if new_code != code:
                    code = new_code
                    self._log(
                        "FIX_CLI",
                        body="Injected --assembly-levels complete after --genera (safety guard)",
                        node="generator",
                    )
                else:
                    # Fallback for shell-style single-line: append before the group arg
                    code = re.sub(
                        r"(ncbi-genome-download\b[^\n]*?)((?:\s+\b(?:all|archaea|bacteria|fungi|invertebrate|metagenomes|plant|protozoa|vertebrate_mammalian|vertebrate_other|viral)\b)?\s*$)",
                        r"\1 --assembly-levels complete --section refseq\2",
                        code,
                        flags=re.MULTILINE,
                    )
                    self._log(
                        "FIX_CLI",
                        body="Injected --assembly-levels complete (shell-style fallback)",
                        node="generator",
                    )

            # Ensure a valid group positional arg is present.
            # CRITICAL: check group presence in the FULL code, not per-line.
            # A multiline list like:
            #   cmd = ["ncbi-genome-download", "--genera", org,
            #          "--output-folder", d, "bacteria"]   ← group on continuation line
            # would be wrongly modified if we process line by line.
            _GROUPS = (
                r"\b(all|archaea|bacteria|fungi|invertebrate"
                r"|metagenomes|plant|protozoa"
                r"|vertebrate_mammalian|vertebrate_other|viral)\b"
            )
            if not re.search(_GROUPS, code):
                # Group missing from entire code block.
                # Only append to lines that are COMPLETE shell commands (not mid-list).
                # A line is a continuation if it ends with , [ \ or the bracket count
                # opened on that line is not balanced.
                fixed_lines = []
                for line in code.splitlines():
                    stripped = line.rstrip()
                    is_ncbi_line = "ncbi-genome-download" in stripped
                    is_continuation = (
                        stripped.endswith(",")
                        or stripped.endswith("[")
                        or stripped.endswith("\\")
                        or stripped.endswith("(")
                        or (stripped.count("[") + stripped.count("(") >
                            stripped.count("]") + stripped.count(")"))
                    )
                    if is_ncbi_line and not is_continuation:
                        stripped = stripped + " bacteria"
                    fixed_lines.append(stripped)
                code = "\n".join(fixed_lines)

        # ── subprocess.run without timeout ──────────────────────────────────────
        # Any subprocess.run call without timeout= can block forever on network I/O.
        # Inject timeout=300 deterministically. 300s covers typical genome downloads;
        # the outer run_with_timeout wrapper adds a second layer of protection.
        if "subprocess.run(" in code:
            def _add_timeout(m: re.Match) -> str:
                call = m.group(0)
                # Skip if timeout= already present
                if "timeout=" in call:
                    return call
                # Find the closing paren — add timeout before it
                # Simple approach: add before the last ) of the call
                # We match the entire subprocess.run(...) call up to the first closing paren
                # that balances the opening paren after "subprocess.run"
                return call.rstrip(")") + ", timeout=300)"

            code = re.sub(
                r"subprocess\.run\([^)]+\)",
                _add_timeout,
                code,
            )

        return code

    def _fix_gc_formula(self, code: str) -> str:
        """
        The LLM often computes GC% as total_length / (4 * n_contigs), which assumes
        uniform base distribution. Replace with the correct count-based formula.
        Detects two wrong patterns and rewrites them deterministically.
        """
        if not code:
            return code

        # Pattern A: gc = sum(len(r.seq) for r in contigs) / (4 * <anything>)
        # or:        gc = total_length / (4 * <anything>)
        code = re.sub(
            r'(\bgc(?:_content|_pct|_percent|_ratio)?\s*=\s*)'   # gc = ...
            r'([^\n]+?)'                                           # numerator (any expr)
            r'\s*/\s*\(\s*4\s*\*\s*[^\)]+\)',                    # / (4 * anything)
            lambda m: (
                m.group(1) +
                "sum(s.seq.count('G') + s.seq.count('C') for s in contigs) / "
                "max(sum(len(s.seq) for s in contigs), 1)"
            ),
            code,
            flags=re.IGNORECASE,
        )

        # Pattern B: gc = <expr> * 0.25  (assumes 25% each base)
        code = re.sub(
            r'(\bgc(?:_content|_pct|_percent|_ratio)?\s*=\s*)[^\n]+?\*\s*0\.25\b',
            lambda m: (
                m.group(1) +
                "sum(s.seq.count('G') + s.seq.count('C') for s in contigs) / "
                "max(sum(len(s.seq) for s in contigs), 1)"
            ),
            code,
            flags=re.IGNORECASE,
        )

        return code

    def _fix_subprocess_kwargs_in_str(self, code: str) -> str:
        """
        The LLM generates str(fna_path, timeout=300) when it means
        subprocess.run([..., str(fna_path)], timeout=300).

        str() and Path() only accept the value to convert — never keyword
        arguments like timeout=, check=, capture_output=, text=.  These
        belong to subprocess.run() and are silently misplaced by the LLM
        when it confuses the str() call site with the subprocess call site.

        This fix is NARROW: only strips the specific subprocess kwargs from
        str() and Path() calls.  It never touches:
          - subprocess.run() itself
          - str() with a valid single-argument call
          - any other function that legitimately uses these kwargs
        """
        import re as _re

        if not code:
            return code

        # Subprocess kwargs that have no business inside str() / Path()
        _BAD_KWS = (
            r'timeout', r'check', r'capture_output', r'text',
            r'shell', r'cwd', r'env', r'stdin', r'stdout', r'stderr',
            r'encoding', r'errors', r'bufsize',
        )
        # Build a pattern: str( or Path( ... bad_kw=<value> ...)
        # We match the entire str()/Path() call content and strip bad kwargs.
        _BAD_KW_PATTERN = _re.compile(
            r'\b(str|Path)\s*\(([^)]*?)\b(?:' + '|'.join(_BAD_KWS) + r')\s*=[^,)]*(?:,\s*)?([^)]*)\)',
            _re.DOTALL,
        )

        def _strip_bad_kwargs(m: _re.Match) -> str:
            func   = m.group(1)   # str or Path
            inner  = m.group(0)[len(func):]   # full (...) including parens

            # Re-parse: split on commas at paren-depth-0
            # to reliably strip bad kwargs
            body = inner.strip()
            if not (body.startswith('(') and body.endswith(')')):
                return m.group(0)
            args_str = body[1:-1]

            good_args = []
            depth = 0
            current = ''
            for ch in args_str:
                if ch in '([{':
                    depth += 1
                    current += ch
                elif ch in ')]}':
                    depth -= 1
                    current += ch
                elif ch == ',' and depth == 0:
                    arg = current.strip()
                    if arg:
                        # Check if it's a bad kwarg
                        kw_match = _re.match(
                            r'^(?:' + '|'.join(_BAD_KWS) + r')\s*=', arg
                        )
                        if not kw_match:
                            good_args.append(arg)
                    current = ''
                else:
                    current += ch
            # Last arg
            arg = current.strip()
            if arg:
                kw_match = _re.match(
                    r'^(?:' + '|'.join(_BAD_KWS) + r')\s*=', arg
                )
                if not kw_match:
                    good_args.append(arg)

            if len(good_args) < len(args_str.split(',')):   # actually stripped something
                fixed = f"{func}({', '.join(good_args)})"
                self._log(
                    "STR_KWARG_FIX",
                    body=f"{m.group(0)!r} → {fixed!r}",
                    node="generator",
                )
                return fixed
            return m.group(0)

        return _BAD_KW_PATTERN.sub(_strip_bad_kwargs, code)

    def _inject_print_sentinel(self, code: str) -> str:
        """
        If the generated Python code contains no print() or sys.stdout.write() call,
        append a minimal stdout line so the observer never sees empty output.

        Empty stdout triggers HARD BLOCK even when the step succeeded (files written
        correctly). This injection is the last line of defence — it fires only when
        the LLM forgot to add any print() despite the GENERATOR_PROMPT rule.
        """
        import re as _re
        if not code:
            return code
        # Already has stdout output — nothing to do.
        if _re.search(r'\bprint\s*\(|sys\.stdout\.write\s*\(', code):
            return code
        # Inject at the very end: list files present in run_dir + success message.
        sentinel = (
            '\n# Auto-injected stdout sentinel — prevents empty-output HARD BLOCK\n'
            'import glob as _g, os as _o\n'
            '_sentinel_dir = run_dir if "run_dir" in dir() else ""\n'
            '_sentinel_files = [_o.path.basename(f) for f in sorted(_g.glob(_o.path.join(_sentinel_dir, "*"))) if _o.path.isfile(f)] if _sentinel_dir else []\n'
            'print(f"Step completed. Files present: {_sentinel_files}")\n'
        )
        self._log("PRINT_SENTINEL", body="No print() found — injecting sentinel", node="generator")
        return code + sentinel

    def _fix_faa_line_counting(self, code: str) -> str:
        """
        Detects the common bug where the generator counts FASTA sequence LINES
        instead of SEQUENCES when parsing .faa protein files.

        Broken pattern (counts lines, not proteins):
            for line in fh:
                if line.startswith(">"):
                    continue
                protein_seqs.append(line)      ← one line per append ≠ one protein
            protein_count = len(protein_seqs)  ← WRONG (can be 5x too high)

        Correct pattern (counts header lines = sequences):
            protein_count = sum(1 for line in open(faa_path) if line.startswith(">"))

        The fix injects a corrected counter before the broken assignment so the
        variable is overwritten with the right value before use.
        """
        import re as _re
        if not code:
            return code
        # Only trigger for .faa files
        if not _re.search(r'\.faa', code):
            return code

        # Detect: appending non-header lines in a loop then using len() as count
        # Pattern: the list collects sequence lines and protein_count = len(list)
        _broken = _re.compile(
            r'(protein_seqs|prot_seqs|sequences?|seqs?)\s*\.\s*append\s*\(\s*(?:line|seq)\s*\)',
            _re.IGNORECASE,
        )
        if not _broken.search(code):
            return code

        # Find the corresponding len() call and inject correct counter before it
        _len_rx = _re.compile(
            r'(protein_count|prot_count|num_proteins?|n_proteins?)\s*=\s*len\s*\([^)]+\)',
        )
        if not _len_rx.search(code):
            return code

        # Inject a correct protein_count immediately before the broken len() call.
        # We look for a faa_path / proteins_faa / faa variable to use.
        _faa_var = "faa_path"
        for _cand in ("faa_path", "proteins_faa", "proteins_path", "faa", "faa_file"):
            if _cand in code:
                _faa_var = _cand
                break

        _correction = (
            f"\n# Auto-corrected: count header lines (= sequences), not sequence lines\n"
            f"protein_count = sum(1 for _l in open({_faa_var}) if _l.startswith('>'))\n"
        )

        def _inject(m: _re.Match) -> str:
            return _correction + m.group(0)

        fixed = _len_rx.sub(_inject, code, count=1)
        if fixed != code:
            self._log("FAA_COUNT_FIX", body="Injected header-line counter", node="generator")
        return fixed

    def _fix_quast_parsing(self, code: str) -> str:
        """
        QUAST report.tsv is a key-value file, not a header-row CSV.
        LLMs consistently try csv.DictReader / pandas.read_csv → KeyError: 'N50'.

        When we detect a read of a quast report file combined with csv.DictReader
        or pandas.read_csv, replace the whole CSV-reader block with the correct
        key-value parser and inject it before the existing (broken) read call.
        """
        import re as _re
        if not code:
            return code

        # Only trigger when the code mentions a quast report file
        if not _re.search(r'report\.tsv|quast.*report|quast_output', code, _re.IGNORECASE):
            return code

        # Only trigger when the code uses csv.DictReader or pandas.read_csv on it
        if not _re.search(r'csv\.DictReader|pd\.read_csv|pandas\.read_csv', code):
            return code

        # Inject the correct key-value parser as a helper function at the top of the code
        # and replace the first csv.DictReader(...report...) with a call to the helper.
        _kv_helper = (
            "\n# Auto-corrected: QUAST report.tsv is KEY-VALUE, not a header CSV\n"
            "def _parse_quast_report(path):\n"
            "    stats = {}\n"
            "    try:\n"
            "        with open(path) as _f:\n"
            "            for _line in _f:\n"
            "                if _line.startswith('#') or not _line.strip():\n"
            "                    continue\n"
            "                _parts = _line.rstrip().split('\\t')\n"
            "                if len(_parts) >= 2:\n"
            "                    stats[_parts[0].strip()] = _parts[1].strip()\n"
            "    except FileNotFoundError:\n"
            "        pass\n"
            "    return stats\n"
            "def _quast_contigs(stats):\n"
            "    # prefix match catches all QUAST key variants regardless of --min-contig\n"
            "    return next((v for k, v in stats.items() if k.startswith('# contigs')), 'NA')\n\n"
        )

        # Insert helper before the first import or at the very top after #!PY
        if code.startswith('#!PY'):
            code = code[:5] + _kv_helper + code[5:]
        else:
            code = _kv_helper + code

        self._log("QUAST_FIX", body="Injected _parse_quast_report() helper", node="generator")
        return code

    def _fix_fasta_reading(self, code: str) -> str:
        """
        Two deterministic fixes for common FASTA reading bugs:

        1. Glob order: models put *.fna.gz before *.fna. If the gz file exists
           and is picked first, SeqIO.parse crashes with UnicodeDecodeError (0x8b
           = gzip magic byte). Reorder so plain *.fna comes first.

        2. Gzip-safe SeqIO.parse: if code calls SeqIO.parse(fasta_path, ...) but
           has no gzip.open guard, wrap it so .gz files are decompressed first.
        """
        if not code:
            return code

        # Fix 0 — replace hardcoded accession-based paths with glob discovery
        # Pattern: SeqIO.parse(os.path.join(run_dir, f"{accession}.fna"), ...)
        # The model invents filenames like GCF_000009045.1.fna but the real file is
        # GCF_000009045.1_ASM904v1_genomic.fna — use glob to find the actual file.
        if "SeqIO.parse" in code and re.search(r'os\.path\.join\([^)]*accession[^)]*\.fna', code):
            code = re.sub(
                r'SeqIO\.parse\(os\.path\.join\([^,]+,\s*f?["\'][^"\']*accession[^"\']*\.fna["\'][^)]*\)',
                r'SeqIO.parse(fasta_path',
                code,
            )
            # Inject glob-based fasta_path discovery before the first SeqIO.parse call
            if "fasta_path" not in code or "glob.glob" not in code:
                glob_snippet = (
                    'import glob as _glob\n'
                    '_fna_files = (_glob.glob(os.path.join(run_dir, "*.fna")) +\n'
                    '              _glob.glob(os.path.join(run_dir, "*.fna.gz")))\n'
                    'fasta_path = _fna_files[0] if _fna_files else None\n'
                    'if not fasta_path:\n'
                    '    print("No FASTA file found in run_dir"); sys.exit(1)\n'
                )
                # Insert after the last import statement or after run_dir definition
                insert_m = re.search(r'(run_dir\s*=\s*r?["\'][^\n]+\n)', code)
                if insert_m:
                    pos = insert_m.end()
                    code = code[:pos] + glob_snippet + code[pos:]
            self._log("FIX_FASTA", body="Replaced hardcoded accession path with glob discovery", node="generator")

        # Fix 0b — undefined 'accessions' variable used in a for-loop
        # Pattern: "for accession in accessions:" where accessions is never defined.
        # Replace the whole loop with glob-based file discovery over all .fna in run_dir.
        if re.search(r'\bfor\s+\w+\s+in\s+accessions\b', code) and 'accessions' not in re.sub(
            r'for\s+\w+\s+in\s+accessions', '', code
        ):
            # Replace undefined 'accessions' with a glob list of .fna files in run_dir
            fna_glob = (
                'import glob as _glob\n'
                '_fna_files = sorted(_glob.glob(os.path.join(run_dir, "*.fna")))\n'
                'if not _fna_files:\n'
                '    print("No .fna files found in run_dir"); sys.exit(1)\n'
                'accessions = [os.path.basename(f) for f in _fna_files]\n'
            )
            insert_m = re.search(r'(run_dir\s*=\s*r?["\'][^\n]+\n)', code)
            if insert_m:
                pos = insert_m.end()
                code = code[:pos] + fna_glob + code[pos:]
                # Also fix the path construction inside the loop:
                # f"{accession}_genomic.fna" → use full path from _fna_files
                code = re.sub(
                    r'os\.path\.join\(run_dir,\s*f?["\'][^"\']*\{accession\}[^"\']*["\']?\)',
                    'os.path.join(run_dir, accession)',
                    code,
                )
                self._log("FIX_FASTA", body="Injected glob-based accessions list (undefined var fix)", node="generator")

        # Fix 0c — fasta_path used but never defined
        # Pattern: SeqIO.parse(fasta_path, ...) where fasta_path= never appears in code.
        # Inject glob-based discovery so fasta_path is always defined before use.
        if ("fasta_path" in code
                and "SeqIO.parse(fasta_path" in code
                and not re.search(r'fasta_path\s*=', code)):
            glob_snippet = (
                'import glob as _glob\n'
                '_fna_files = sorted(_glob.glob(os.path.join(run_dir, "*.fna"))) + \\\n'
                '             sorted(_glob.glob(os.path.join(run_dir, "*.fna.gz")))\n'
                'if not _fna_files:\n'
                '    print("No FASTA file found in run_dir"); sys.exit(1)\n'
                'fasta_path = _fna_files[0]\n'
                'if fasta_path.endswith(".gz"):\n'
                '    import gzip, shutil\n'
                '    _unzipped = fasta_path[:-3]\n'
                '    with gzip.open(fasta_path, "rb") as _fi, open(_unzipped, "wb") as _fo:\n'
                '        shutil.copyfileobj(_fi, _fo)\n'
                '    fasta_path = _unzipped\n'
            )
            insert_m = re.search(r'(run_dir\s*=\s*r?["\'][^\n]+\n)', code)
            if insert_m:
                pos = insert_m.end()
                code = code[:pos] + glob_snippet + code[pos:]
                self._log("FIX_FASTA", body="Injected fasta_path glob (undefined var fix)", node="generator")

        # Fix 0d — SeqIO.parse called multiple times on same fasta_path (iterator exhausted)
        # Replace with a single list() call stored in a variable, reused throughout.
        if code.count("SeqIO.parse(fasta_path") > 1:
            # Replace all occurrences with reference to a pre-materialised list
            code = re.sub(
                r'SeqIO\.parse\(fasta_path,\s*["\']fasta["\']\)',
                '_contigs_cache',
                code,
            )
            # Inject the cache definition after fasta_path definition or run_dir
            cache_line = '_contigs_cache = list(SeqIO.parse(fasta_path, "fasta"))\n'
            insert_m2 = re.search(r'(fasta_path\s*=\s*[^\n]+\n)', code)
            if insert_m2:
                pos2 = insert_m2.end()
                code = code[:pos2] + cache_line + code[pos2:]
                self._log("FIX_FASTA", body="Cached SeqIO.parse to avoid iterator exhaustion", node="generator")

        # Fix 1 — reorder glob so .fna comes before .fna.gz
        # ONLY applies to lines that are PART OF A CONTINUATION EXPRESSION
        # (i.e., they end with + or are inside a multi-glob parenthesised block).
        # NEVER swaps standalone assignment lines like:
        #   gz_files = sorted(glob.glob(..., "*.fna.gz"))
        #   fna_files = sorted(glob.glob(..., "*.fna"))
        # because swapping those moves the definition AFTER its first use → NameError.
        _lines = code.splitlines(keepends=True)
        # Only swap GLOB CONTINUATION lines (part of a multi-line expression).
        # A continuation glob line: stripped content starts with glob.glob (not an assignment).
        # The gz line must end with + (it's not last). The fna line may or may not.
        # NEVER swap standalone assignment lines (gz_files = ..., fna_files = ...).
        def _is_glob_continuation(l: str) -> bool:
            s = l.strip()
            return s.startswith('glob.glob') and '=' not in s.split('glob.glob')[0]
        _fna_idx   = next((i for i, l in enumerate(_lines)
                           if ('*.fna"' in l or "*.fna'" in l)
                           and '*.fna.gz' not in l
                           and _is_glob_continuation(l)), None)
        _fna_gz_idx = next((i for i, l in enumerate(_lines)
                            if '*.fna.gz' in l
                            and _is_glob_continuation(l)
                            and l.rstrip().endswith('+')), None)
        # Only reorder when gz comes STRICTLY before fna (wrong order)
        if _fna_gz_idx is not None and _fna_idx is not None and _fna_gz_idx < _fna_idx:
            # Swap the trailing continuation operators (+) along with the lines.
            _gz_ln  = _lines[_fna_gz_idx]
            _fna_ln = _lines[_fna_idx]
            _gz_trail  = '+' if _gz_ln.rstrip().endswith('+') else ''
            _fna_trail = '+' if _fna_ln.rstrip().endswith('+') else ''
            def _set_trail(ln: str, trail: str) -> str:
                stripped = ln.rstrip().rstrip('+').rstrip()
                nl = '\n' if ln.endswith('\n') else ''
                return stripped + (' +' if trail else '') + nl
            _lines[_fna_gz_idx] = _set_trail(_gz_ln, _fna_trail)
            _lines[_fna_idx]    = _set_trail(_fna_ln, _gz_trail)
            _lines[_fna_gz_idx], _lines[_fna_idx] = _lines[_fna_idx], _lines[_fna_gz_idx]
            self._log("FIX_FASTA", body="Reordered glob: .fna before .fna.gz (continuation lines only)", node="generator")
        code = "".join(_lines)

        # Fix 2 — gzip-safe SeqIO.parse
        # If code has SeqIO.parse(fasta_path, ...) but no gzip.open guard, inject one.
        # CRITICAL: preserve the surrounding indentation so the injected block is valid
        # inside loops and conditionals (a fixed 4-space indent causes IndentationError
        # when the original line was indented deeper).
        if "SeqIO.parse(fasta_path" in code and "gzip.open" not in code:
            def _gzip_guard(m: re.Match) -> str:
                # Detect indentation of the matched line by looking backwards
                start = m.start()
                line_start = code.rfind("\n", 0, start) + 1
                indent = ""
                for ch in code[line_start:start]:
                    if ch in (" ", "\t"):
                        indent += ch
                    else:
                        break
                # If the assignment itself is indented, honour that indentation
                i = indent
                i2 = indent + "    "
                i3 = indent + "        "
                var = m.group(1)
                return (
                    f'if fasta_path.endswith(".gz"):\n'
                    f'{i2}import gzip\n'
                    f'{i2}with gzip.open(fasta_path, "rt") as _gz_handle:\n'
                    f'{i3}{var} = list(SeqIO.parse(_gz_handle, "fasta"))\n'
                    f'{i}else:\n'
                    f'{i2}{var} = list(SeqIO.parse(fasta_path, "fasta"))'
                )

            new_code = re.sub(
                r'(\w+)\s*=\s*list\(SeqIO\.parse\(fasta_path,\s*["\']fasta["\']\)\)',
                _gzip_guard,
                code,
            )
            if new_code != code:
                code = new_code
                self._log("FIX_FASTA", body="Injected gzip-safe SeqIO.parse guard (indent-aware)", node="generator")

        # Fix 3 — universal gzip decompression guard before fasta_path assignment.
        # When the model sets fasta_path from a glob that may return .gz files,
        # inject an "if .gz → decompress to .fna" block right after the assignment.
        # This catches any code that does fasta_path = some_list[0] without a guard.
        if "SeqIO.parse(fasta_path" in code and "fasta_path.endswith" not in code:
            _guard = (
                'if fasta_path.endswith(".gz"):\n'
                '    import gzip as _gz_mod, shutil as _sh_mod\n'
                '    _fna_unzipped = fasta_path[:-3]\n'
                '    if not os.path.exists(_fna_unzipped):\n'
                '        with _gz_mod.open(fasta_path, "rb") as _fi, open(_fna_unzipped, "wb") as _fo:\n'
                '            _sh_mod.copyfileobj(_fi, _fo)\n'
                '    fasta_path = _fna_unzipped\n'
            )
            # Inject after the last fasta_path = ... assignment
            _m3 = list(re.finditer(r'^([ \t]*fasta_path\s*=\s*.+)$', code, re.MULTILINE))
            if _m3:
                _last = _m3[-1]
                _ins = _last.end()
                code = code[:_ins] + "\n" + _guard + code[_ins:]
                self._log("FIX_FASTA Fix-3", body="Injected universal gzip decompression guard", node="generator")

        return code

    def _auto_fix_fstring_quotes(self, code: str) -> str:
        """
        If code has a SyntaxError caused by conflicting f-string quotes, attempt a
        deterministic fix: for every f-string whose outer delimiter matches inner quotes,
        switch the outer delimiter to the other quote character.

        Strategy:
          f"...{"inner"}..."  →  f'...{"inner"}...'   (switch outer to single)
          f'...{'inner'}...'  →  f"...{'inner'}..."   (switch outer to double)

        If the fixed code still doesn't compile, return the original — the executor
        will surface a clear error and the observer will trigger repair.
        """
        try:
            compile(code, "<postcheck>", "exec")
            return code  # already valid, nothing to do
        except SyntaxError:
            pass

        def _swap_outer(m: re.Match) -> str:
            outer = m.group(1)          # f" or f'
            body  = m.group(2)
            close = m.group(3)          # matching close quote
            inner_q = '"' if outer[-1] == "'" else "'"
            other_q = '"' if outer[-1] == "'" else "'"
            # Only swap if body actually contains the conflicting quote
            if outer[-1] in body:
                new_outer = outer[:-1] + other_q
                new_close = other_q
                return new_outer + body + new_close
            return m.group(0)

        # Match f'...' or f"..." (non-greedy, single-line only — avoids multi-line edge cases)
        fixed = re.sub(
            r'(f["\'])((?:[^\\]|\\.)*?)(["\'])',
            _swap_outer,
            code,
        )

        try:
            compile(fixed, "<postcheck>", "exec")
            self._log("FSTRING AUTO-FIX", body="Quote conflict corrected", node="generator")
            return fixed
        except SyntaxError:
            return code  # give up — executor will surface the error cleanly

    def _fix_escaped_newlines_in_write(self, code: str) -> str:
        """
        Fix LLM-generated \\\\n (literal backslash+n) inside write() / writelines() calls.

        The LLM sometimes generates:
            out_f.write(f"value: {x:.4f}\\n")   ← writes backslash+n to file (wrong)
        instead of:
            out_f.write(f"value: {x:.4f}\\n")   ← writes newline character (correct)

        This pass replaces \\\\n → \\n ONLY inside the string argument of write() /
        writelines() calls. It is intentionally narrow to avoid touching:
          - regex patterns where \\\\n is a valid escaped newline pattern
          - raw strings (r"...")
          - \\\\n that appears outside of write() calls

        Strategy: match .write("...") or .write(f"...") and replace \\\\n with \\n
        inside the string argument only.
        """
        import re as _re

        # Match .write( or .writelines( followed by an optional f/b prefix and a quote,
        # then capture the string content up to the matching closing quote (single-line).
        # We replace \\\\n (4 chars in source, 2 in Python: backslash+n) with \\n (newline).
        _WRITE_RX = _re.compile(
            r'(\.\s*write(?:lines)?\s*\(\s*)'   # .write( or .writelines(
            r'([fFbBuU]?)'                        # optional prefix f/b/u
            r'("(?:[^"\\]|\\.)*?"'               # double-quoted string
            r'|\'(?:[^\'\\]|\\.)*?\')',           # or single-quoted string
            _re.DOTALL,
        )

        def _fix_string(m: re.Match) -> str:
            call_prefix = m.group(1)   # .write(
            str_prefix  = m.group(2)   # f / b / u / ""
            string_body = m.group(3)   # "..." or '...'
            # Replace \\n (two chars: \ + n) with \n (newline char) inside the string.
            # In the source text, \\n is stored as the two-char sequence \\ + n.
            # We must NOT touch \\\\n (intentional literal backslash + n).
            fixed_body = _re.sub(r'(?<!\\)\\n', '\n', string_body)
            return call_prefix + str_prefix + fixed_body

        return _WRITE_RX.sub(_fix_string, code)

    def _sanitize_output_paths(self, code: str, run_dir: str) -> str:
        """
        Replace absolute Windows/Unix paths used for OUTPUT files with run_dir-relative paths.
        Only targets write-mode opens and common DataFrame/array save calls — never touches
        read-mode opens (those are valid input paths that must stay absolute).
        """
        if not run_dir:
            return code

        # Pattern: open("C:\\...\\file.ext", "w"/"a"/"wb"/"ab") or open('...', 'w')
        def _replace_open(m):
            quote = m.group(1)
            path = m.group(2)
            mode = m.group(3)
            basename = os.path.basename(path.replace("\\\\", "\\").replace("\\", os.sep))
            new_path = os.path.join(run_dir, basename).replace("\\", "\\\\")
            return f"open({quote}{new_path}{quote}, {quote}{mode}{quote})"

        # Match: open("ABS_PATH", "w" or "a" or "wb" or "ab" or "x")
        code = re.sub(
            r"""open\((['"]) ([A-Za-z]:[/\\\\][^'"]+|/(?:[^/'"]+/)+[^/'"]+) \1\s*,\s*['"](w|a|wb|ab|x)['"]\)""",
            _replace_open,
            code,
            flags=re.VERBOSE,
        )

        # Pattern: pd.DataFrame.to_csv / to_excel / to_parquet / np.save / np.savetxt with absolute path
        def _replace_save(m):
            method = m.group(1)
            quote = m.group(2)
            path = m.group(3)
            rest = m.group(4)
            basename = os.path.basename(path.replace("\\\\", "\\").replace("\\", os.sep))
            new_path = os.path.join(run_dir, basename).replace("\\", "\\\\")
            return f"{method}({quote}{new_path}{quote}{rest}"

        code = re.sub(
            r"""(\.\s*(?:to_csv|to_excel|to_parquet|to_json|to_pickle|savetxt?|save))\((['"]) ([A-Za-z]:[/\\\\][^'"]+|/(?:[^/'"]+/)+[^/'"]+) \2 ([,)])""",
            _replace_save,
            code,
            flags=re.VERBOSE,
        )

        return code

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


    # ENV ROUTING — driven by registry provides_bins, not a hardcoded regex.
    # Source of truth: meta-env1.provides_bins in index.yaml.
    def _select_env(self, prompt: str) -> str:
        """
        Route to meta-env1 only when the prompt explicitly names a binary
        declared in meta-env1's provides_bins registry entry.
        Falls back to bio-agent-env1 for everything else.
        """
        try:
            from genomeer.runtime.env_resolver import get_meta_env_signals
            signals = get_meta_env_signals()
            prompt_lower = prompt.lower()
            if any(sig in prompt_lower for sig in signals):
                return "meta-env1"
        except Exception:
            pass
        return "bio-agent-env1"

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
            # Also stage any absolute file paths mentioned in the prompt text.
            staged += self._stage_prompt_files(prompt, tmp)
            # Keep basenames of user uploads so they survive a fatal-error cleanup.
            _staged_basenames = {os.path.basename(p) for p in staged}

            # Multi-turn isolation: compute turn_id BEFORE branching so it's
            # included in both bootstrap and follow-up inputs.
            _turn_id = self._next_turn_id(thread_id)

            # Upload-routing fix: when the user attaches files this turn, surface
            # their paths INSIDE the user prompt with an explicit routing directive
            # so the planner routes to ORCHESTRATOR instead of QA. Without this,
            # terse prompts like "??" / "explain this" route to QA and the file
            # is never opened. No-op when staged is empty -> identical to before.
            _enriched_prompt = self._enrich_prompt_with_uploads(prompt, staged)

            if not self._has_session_state(thread_id):
                # FIRST TURN OF THIS SESSION -> full bootstrap state
                _env = self._select_env(prompt)
                self._log("ENV ROUTING", body=f"prompt → {_env}", node="go")
                inputs = {
                    "messages": [HumanMessage(content=_enriched_prompt)],
                    "next_step": None,
                    "env_name": _env,
                    "env_ready": False,
                    "pending_code": None,
                    "manifest": {
                        "timeout_seconds": self.timeout_seconds,
                        "observations": [],
                        "attachments": staged,
                        "interaction_mode": getattr(self, "interaction_mode", "auto"),
                        # Multi-turn: cumulative step counter for UI labeling.
                        "step_offset": 0,
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
                    # bio_hint defaults — neutral, overwritten when bio_hint node runs
                    "bio_hint": None,
                    "bio_hint_step_idx": -1,
                    "bio_hint_mode": None,
                    "bio_hint_skipped": False,
                    "turn_id": _turn_id,
                }
            else:
                # FOLLOW-UP TURN -> only append the new message (with upload routing
                # directive baked in by _enrich_prompt_with_uploads when applicable).
                msg_block = [HumanMessage(content=_enriched_prompt)]
                inputs = {"messages": msg_block, "turn_id": _turn_id}
                # Fix C1 — reset run_started_at on resume so global timeout doesn't fire immediately
                inputs["run_started_at"] = time.time()

            config = {"recursion_limit": 500, "configurable": {"thread_id": thread_id}}
            self.log = []
            last_msg_text = None
            _fatal: BaseException | None = None

            try:
                for s in self.app.stream(inputs, stream_mode="values", config=config):
                    if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
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

                    curr_idx = s.get("current_idx", None)
                    next_step = s.get("next_step", None)
                    self._log("STEP SNAPSHOT", body=f"current_idx={curr_idx}\nnext_step={next_step}", node="driver")

            except BaseException as _e:
                _fatal = _e
                raise
            finally:
                if _fatal is not None:
                    # Fatal error — remove generated files but preserve user uploads.
                    try:
                        import shutil as _sh
                        for _entry in os.listdir(tmp):
                            if _entry in _staged_basenames:
                                continue  # preserve user's uploaded files
                            _fp = os.path.join(tmp, _entry)
                            if os.path.isfile(_fp):
                                os.unlink(_fp)
                            elif os.path.isdir(_fp):
                                _sh.rmtree(_fp, ignore_errors=True)
                    except Exception:
                        pass

            return self.log, last_msg_text
    
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
            staged += self._stage_prompt_files(prompt, tmp)
            _staged_basenames = {os.path.basename(p) for p in staged}

            # Multi-turn isolation: compute turn_id BEFORE branching so it's
            # included in both bootstrap and follow-up inputs.
            _turn_id = self._next_turn_id(thread_id)

            # Upload-routing fix: see _enrich_prompt_with_uploads docstring.
            # No-op when staged is empty -> identical behavior to before.
            _enriched_prompt = self._enrich_prompt_with_uploads(prompt, staged)

            if not self._has_session_state(thread_id):
                # FIRST TURN OF THIS SESSION -> full bootstrap state
                _env = self._select_env(prompt)
                self._log("ENV ROUTING", body=f"prompt → {_env}", node="go")
                inputs = {
                    "messages": [HumanMessage(content=_enriched_prompt)],
                    "next_step": None,
                    "env_name": _env,
                    "env_ready": False,
                    "pending_code": None,
                    "manifest": {
                        "timeout_seconds": self.timeout_seconds,
                        "observations": [],
                        "attachments": staged,
                        "interaction_mode": getattr(self, "interaction_mode", "auto"),
                        # Multi-turn: cumulative step counter for UI labeling.
                        "step_offset": 0,
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
                    # bio_hint defaults — neutral, overwritten when bio_hint node runs
                    "bio_hint": None,
                    "bio_hint_step_idx": -1,
                    "bio_hint_mode": None,
                    "bio_hint_skipped": False,
                    "turn_id": _turn_id,
                }
            else:
                # FOLLOW-UP TURN -> only append the new message (with upload routing
                # directive baked in by _enrich_prompt_with_uploads when applicable).
                msg_block = [HumanMessage(content=_enriched_prompt)]
                inputs = {"messages": msg_block, "turn_id": _turn_id}
                # Fix C1 — reset run_started_at on resume so global timeout doesn't fire immediately
                inputs["run_started_at"] = time.time()

            config = {"recursion_limit": 500, "configurable": {"thread_id": thread_id}}
            last_msg_text = None
            self.log = []
            _fatal: BaseException | None = None

            try:
                for s in self.app.stream(inputs, stream_mode="values", config=config):
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

            except BaseException as _e:
                _fatal = _e
                raise
            finally:
                if _fatal is not None:
                    try:
                        import shutil as _sh
                        for _entry in os.listdir(tmp):
                            if _entry in _staged_basenames:
                                continue
                            _fp = os.path.join(tmp, _entry)
                            if os.path.isfile(_fp):
                                os.unlink(_fp)
                            elif os.path.isdir(_fp):
                                _sh.rmtree(_fp, ignore_errors=True)
                    except Exception:
                        pass