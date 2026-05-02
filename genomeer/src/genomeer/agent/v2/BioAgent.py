# -----------------------------------------------
# LIBRARY
# -----------------------------------------------
from pathlib import Path
import glob, inspect, os, re, threading, time, types, traceback, warnings
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

import hashlib
import json as _json_cache
from genomeer.utils.metrics import RunMetrics


from genomeer.agent.v2.utils.cache import get_cache

from genomeer.agent.v2.utils.structured_output import RobustLLMParser, patch_state_graph_helper
from genomeer.agent.v2.utils.state_graph import StateGraphHelper
patch_state_graph_helper(StateGraphHelper)   # active le parser robuste globalement
_robust_parser = RobustLLMParser(strict_validation=True)


# Nœuds pour lesquels le cache LLM est désactivé
_NOCACHE_LLM_NODES = frozenset({
    "planner",      # Plan dépend du contexte courant et des fichiers présents
    "observer",     # Observation dépend du résultat réel de l'exécution
    "finalizer",    # Rapport final dépend de TOUS les résultats du run
    "orchestrator", # Routing dépend de l'état courant du plan
})

# ── AXE 2.3: SqliteSaver for cross-session persistence ──
try:
    from langgraph.checkpoint.sqlite import SqliteSaver
    import sqlite3
    _SQLITE_OK = True
except ImportError:
    _SQLITE_OK = False

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
from genomeer.agent.v2.utils.quality_gate import check_quality, format_quality_message  # GAP4 fix
from genomeer.runtime.env_resolver import resolve_env_for_code
from genomeer.model.feedback import FeedbackParser
# ── AXE 3.3: Intelligent output parsers ──
try:
    from genomeer.tools.parsers import parse_tool_output as _smart_parse_output
    _PARSERS_OK = True
except ImportError:
    _PARSERS_OK = False
    def _smart_parse_output(tool, stdout, result=None, output_dir=None):
        return stdout[:2000] if stdout else ""
# ── AXE 2.1: Template Library ──
try:
    from genomeer.memory.template_library import TemplateLibrary as _TemplateLibrary
    _TEMPLATE_LIB = _TemplateLibrary()
    _TEMPLATE_OK = True
except Exception:
    _TEMPLATE_OK = False
    _TEMPLATE_LIB = None

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
    phase: int | None
    
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
    # ── Phase 2: multi-sample / batch support ──────────────────────────────
    batch_mode: bool | None
    batch_strategy: Literal["independent", "coassembly"] | None
    sample_manifest: List[Dict[str, Any]] | None  # [{id, r1, r2, metadata}]
    current_sample_idx: int | None
    current_sample_id: str | None
    per_sample_results: Dict[str, Any] | None     # {sample_id: {step_results}}
    
    
    # ---------------------------------------------------------------------------
# METAGENOMICS ENV AUTO-DETECTION
# ---------------------------------------------------------------------------
 
# CLI tools that require meta-env1
from genomeer.runtime.env_resolver import META_ENV_SIGNALS as _META_ENV_BINS
 
# Adaptive timeouts for heavy metagenomics steps (seconds)
_HEAVY_STEPS = {
    "assembly": 21600,      # 6h — metaSPAdes / MEGAHIT
    "assembl":  21600,
    "metaspades": 21600,
    "megahit":  14400,
    "flye":     21600,
    "assemble": 21600,
    "binning":  7200,       # 2h — MetaBAT2
    "metabat":  7200,
    "das_tool": 7200,
    "checkm":   3600,       # 1h
    "gtdbtk":   10800,      # 3h
    "humann":   10800,
    "diamond":  7200,
    "hmmer":    3600,
    "download": 3600,       # 1h — large DB downloads
    "kraken2":  1800,       # 30min
    "metaphlan":1800,
    "annotation": 3600,
    "annotate": 3600,
    "prokka":   3600,
}

def _estimate_timeout(step_title: str, input_files: list, default: int) -> int:
    """Dynamically estimates timeout based on step type and input file sizes (P2-B)."""
    import os
    
    # P2-B.3: GENOMEER_TIMEOUT_SCALE_FACTOR
    scale_factor = float(os.environ.get("GENOMEER_TIMEOUT_SCALE_FACTOR", "1.0"))
    max_timeout = 48 * 3600
    
    # Base timeout detection
    base_timeout = default
    step_lower = step_title.lower()
    for kw, t in _HEAVY_STEPS.items():
        if kw in step_lower:
            base_timeout = t
            break
            
    # Calculate input size in GB
    total_size_gb = 0.0
    for f in input_files:
        if os.path.exists(f):
            total_size_gb += os.path.getsize(f) / (1024**3)
            
    # Scale based on size (base assumes ~5GB of data)
    size_multiplier = max(1.0, total_size_gb / 5.0)
    
    timeout = int(base_timeout * size_multiplier * scale_factor)
    return min(timeout, max_timeout)

# ---------------------------------------------------------------------------
# Phase 3: Adaptive retry escalation
# When a step keeps failing, suggest a different strategy instead of retrying
# the exact same approach. Format: {keyword_in_step_title: {retry_n: hint}}
# ---------------------------------------------------------------------------
RETRY_ESCALATION: Dict[str, Dict[int, str]] = {
    "metaspades": {
        2: "[ESCALATION] metaSPAdes failed twice. Try MEGAHIT (lower RAM): run_megahit() instead.",
        3: "[ESCALATION] Both assemblers failed. Subsample reads to 20% and retry: seqtk sample -s 42 reads.fq 0.2",
    },
    "megahit": {
        2: "[ESCALATION] MEGAHIT failed. Try with fewer threads (--num-cpu-threads 4) or more memory.",
        3: "[ESCALATION] Assembly failed 3 times. Check read quality with FastQC before retrying.",
    },
    "kraken2": {
        2: "[ESCALATION] Kraken2 classification very low. Try --confidence 0.05 or switch to MetaPhlAn4: run_metaphlan4().",
        3: "[ESCALATION] Taxonomic classification failed. The database may be incompatible; try run_bracken() re-estimation on existing output.",
    },
    "metabat": {
        2: "[ESCALATION] MetaBAT2 produced 0 bins. Increase min-contig to 2500bp and ensure coverage file is correct.",
        3: "[ESCALATION] Binning failed repeatedly. Check coverage depth — must be >5X for reliable bins.",
    },
    "binning": {
        2: "[ESCALATION] Binning failed. Verify that jgi_summarize_bam_contig_depths was run and produced a valid depth file.",
        3: "[ESCALATION] Binning failed 3 times. Run DAS_Tool with existing bins from any previous partial runs.",
    },
    "checkm": {
        2: "[ESCALATION] CheckM2 failed. Ensure the database was downloaded: checkm2 database --download.",
        3: "[ESCALATION] Quality assessment failed. Try CheckM1 lineage_wf as fallback.",
    },
    "gtdbtk": {
        2: "[ESCALATION] GTDB-Tk failed. Check GTDBTK_DATA_PATH env var; database may not be downloaded.",
        3: "[ESCALATION] Taxonomy classification failed. Use NCBI lineage as fallback via run_ncbi_taxonomy().",
    },
    "humann": {
        2: "[ESCALATION] HUMAnN3 failed. Try with --bypass-nucleotide-index and --metaphlan-options '--index latest'.",
        3: "[ESCALATION] Functional profiling failed. Use KEGG annotation via run_diamond() + KEGG DB as fallback.",
    },
    "prokka": {
        2: "[ESCALATION] Prokka annotation failed. Try --compliant flag or check if contigs have non-standard characters.",
        3: "[ESCALATION] Prokka failed 3 times. Use Prodigal gene prediction as partial fallback: run_prodigal().",
    },
    "diamond": {
        2: "[ESCALATION] DIAMOND failed. Try --more-sensitive flag or reduce --threads.",
        3: "[ESCALATION] DIAMOND failed 3 times. Ensure database is compiled for this DIAMOND version: diamond makedb.",
    },
}
 
def _adaptive_timeout(step_title: str, default: int = 600) -> int:
    """Return an appropriate timeout in seconds based on the step title."""
    t = step_title.lower()
    for keyword, timeout in _HEAVY_STEPS.items():
        if keyword in t:
            return max(timeout, default)
    return default
 
 # FIX G5: resolve_env_for_code is already imported from genomeer.runtime.env_resolver (line 46)
 # The local re-definition below was silently masking that import — REMOVED.
 # Use the canonical version from env_resolver.py going forward.
 
    
    
    
    
    
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
        print("BioAgent_v2 CONFIGURATION")
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
            print("\n AGENT LLM (Constructor Override):")
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
        self._set_debug_log("./agent_debug.log")
        
        # CONSTANTS
        self.MAX_STEP_RETRIES = 3          # retries before diagnostics
        self.MAX_DIAG_ROUNDS_PER_STEP = 2  # how many times we allow re-entering diagnostics for the same step
        self.MAX_TOTAL_RUN_SECONDS = int(os.environ.get("GENOMEER_MAX_RUN_SECONDS", str(4 * 3600)))  # 4h global cap
        
        # Artifact server
        self.artifacts_base_url = os.getenv("PUBLIC_ARTIFACTS_URL", "http://localhost:8910/api/v1/artifacts")
        if auto_start_artifacts:
            self._start_artifacts_server_in_bg(host=artifacts_host, port=artifacts_port, prefix=artifacts_prefix)

        # BIO RAG
        import threading
        from genomeer.model.bio_rag import BioRAGStore, BioRAGRetriever
        self.bio_rag_store = BioRAGStore(persist_dir=str(Path(self.path) / ".genomeer_rag_cache"))
        
        self._bio_rag_status = "offline" if os.environ.get("GENOMEER_RAG_OFFLINE", "0") == "1" else "building"
        
        if self._bio_rag_status != "offline":
            def _build_rag():
                try:
                    self.bio_rag_store.build(sources=["card", "kegg_pathways", "quality_thresholds"])
                    if self.bio_rag_store.ready:
                        self._bio_rag_status = "ready"
                    else:
                        self._bio_rag_status = "partial"
                except Exception as e:
                    self._log("BIORAG THREAD", f"Failed to build BioRAG: {e}", type="file")
                    self._bio_rag_status = "offline"
                    
            self._rag_build_thread = threading.Thread(
                target=_build_rag,
                daemon=True,
                name="bio-rag-builder"
            )
            self._rag_build_thread.start()
            self._log("BIO RAG", body="Index build started in background (daemon thread)", node="init")
        else:
            self.bio_rag_store.build(sources=["card", "kegg_pathways", "quality_thresholds"])
            self._log("BIO RAG", body="Started in offline mode (GENOMEER_RAG_OFFLINE=1). Using cache only.", node="init")
            
        self.bio_retriever = BioRAGRetriever(self.bio_rag_store)

        # ── CACHE MULTI-COUCHE ──────────────────────────────────────────────────
        self._cache = get_cache()
        self._log("CACHE", body=f"Cache dir: {self._cache.cache_dir}", node="init")

    @property
    def bio_rag_status(self) -> str:
        return getattr(self, "_bio_rag_status", "offline")

    # LOGS UTILS [DEV-ONLY]
    def _set_debug_log(self, path: str | None = None):
        """Call once to set a log file. If None, uses ./bioagent_debug.log"""
        self.debug_log_path = path or os.path.abspath("./bioagent_debug.log")
        os.makedirs(os.path.dirname(self.debug_log_path), exist_ok=True)
        with open(self.debug_log_path, "w", encoding="utf-8") as f:
            f.write("\n===== NEW SESSION =====\n")
        self._log_buffer = []
        self._log_buffer_lock = threading.Lock()

    def _slim_manifest(self, manifest: dict, node: str) -> dict:
        if node in ("observer", "generator", "diagnostics"):
            out = {
                "input_state":      manifest.get("input_state"),
                "timeout_seconds":  manifest.get("timeout_seconds"),
                "retry_count":      manifest.get("retry_count"),
                "quality_signals":  manifest.get("quality_signals"),
                "repair_feedback":  manifest.get("repair_feedback"),
            }
            if node == "generator":
                out["diagnostics_rounds"] = manifest.get("diagnostics_rounds")
                out["file_registry"] = manifest.get("file_registry")
            return out
        if node == "finalizer":
            return {
                "quality_signals":      manifest.get("quality_signals"),
                "amr_genes_detected":   manifest.get("amr_genes_detected"),
                "top_pathways":         manifest.get("top_pathways"),
                "observations":         manifest.get("observations"),
            }
        return manifest

    def _trim_messages(self, messages: list, keep_first: int = 1, keep_last: int = 30) -> list:
        """P3-A: Trim messages to prevent context explosion on long runs."""
        import os
        keep_last = int(os.environ.get("GENOMEER_MAX_MESSAGES", str(keep_last)))
        
        if len(messages) <= keep_first + keep_last:
            return []
            
        first_msgs = messages[:keep_first]
        remaining_msgs = messages[keep_first:]
        
        cutoff_idx = len(remaining_msgs) - keep_last
        older_msgs = remaining_msgs[:cutoff_idx]
        
        # Priority 3: Do not delete AIMessages with <observe> tags containing biological metrics
        important_older_ids = set()
        for m in older_msgs:
            if hasattr(m, 'content') and getattr(m, 'type', '') == 'ai':
                if '<observe>' in m.content:
                    lower_content = m.content.lower()
                    if any(kw in lower_content for kw in ('n50', 'q30', 'contig', 'classified', 'genes', 'quality', 'metric', 'rate', 'reads')):
                        important_older_ids.add(getattr(m, 'id', None) or str(id(m)))
        
        try:
            from langchain_core.messages import RemoveMessage
        except ImportError:
            return []
            
        to_remove = []
        for m in older_msgs:
            m_id = getattr(m, 'id', None)
            if not m_id:
                # LangGraph usually assigns an ID. If missing, we use Python's id() as fallback.
                # However, RemoveMessage might fail to match if the state doesn't track this ID.
                self._log("TRIM MESSAGES", body=f"Warning: Message of type {getattr(m, 'type', 'unknown')} has no ID. Using id(m) fallback.", node="system")
                m_id = str(id(m))
                
            if m_id and m_id not in important_older_ids:
                to_remove.append(RemoveMessage(id=m_id))
                
        return to_remove

    def _log(self, title: str, body: str = "", node: str | None = None, type: str = 'file'):
        """Append a structured block to the debug log."""
        line = ""
        if title == "ENTER NODE":
            line += "\n" + (">"*60)
        line += f"\n[{node or '-'}] {title}\n{body}\n" + ("-"*60) + "\n"
        if type == 'file':
            with getattr(self, "_log_buffer_lock", threading.Lock()):
                if not hasattr(self, "_log_buffer"):
                    self._log_buffer = []
                self._log_buffer.append(line)
                if len(self._log_buffer) >= 20:
                    self._flush_log()
        elif type == 'stdout':
            print(line)

    def _flush_log(self):
        if not getattr(self, "_log_buffer", None):
            return
        lock = getattr(self, "_log_buffer_lock", None)
        if lock is None:
            return
        with lock:
            if not self._log_buffer:
                return
            path = getattr(self, "debug_log_path", os.path.abspath("./bioagent_debug.log"))
            with open(path, "a", encoding="utf-8") as f:
                f.writelines(self._log_buffer)
            self._log_buffer.clear()

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
        Central LLM call with cache layer.
        Cache skipped for: planner, orchestrator, finalizer (non-deterministic nodes).
        """
        # ── Cache lookup ────────────────────────────────────────────────────
        _cache_eligible = node not in _NOCACHE_LLM_NODES
        
        sys_msg  = next((getattr(m, "content", "") for m in msgs if getattr(m, "type", "") == "system"), "")
        user_msg = next((getattr(m, "content", "") for m in reversed(msgs) if getattr(m, "type", "") == "human"), "")
        model_name = str(getattr(self.llm, "model_name", self.llm.__class__.__name__))
        cache_key = self._cache.llm.make_key(model_name, sys_msg, user_msg)
 
        if _cache_eligible:
            cached = self._cache.llm.get(cache_key, node=node)
            if cached:
                self._log(f"LLM CACHE HIT ({purpose})", cached[:200], node=node)
                from langchain_core.messages import AIMessage as _AIMsg
                if hasattr(self, "_run_metrics") and self._run_metrics:
                    self._run_metrics.record_llm_call(cache_hit=True)
                return _AIMsg(content=cached)
        else:
            self._log(f"LLM CACHE SKIP ({purpose})", body=f"node={node} is non-deterministic", node=node)
        # ── End cache lookup ────────────────────────────────────────────────
 
        if verbose:
            prompt_txt = self._fmt_msgs(msgs)
            self._log(f"LLM REQUEST ({purpose})", prompt_txt, node=node)
 
        resp = self.llm.invoke(msgs)
        content = getattr(resp, "content", str(resp))
 
        if verbose:
            self._log(f"LLM RESPONSE ({purpose})", content, node=node)
 
        # ── Cache save (skips planner/orchestrator/finalizer automatiquement) ──
        if _cache_eligible:
            self._cache.llm.set(cache_key, content, model=model_name, node=node)
 
        if hasattr(self, "_run_metrics") and self._run_metrics:
            self._run_metrics.record_llm_call(cache_hit=False)

        return resp
    
    def _route_blocked_step(self, state, rc, diag_rounds, summary, step, new_manifest, node="observer"):
        """Unified routing for blocked steps. Handles retry counts, escalation hints, and the diag_rounds cap (T1.2/P1-C)."""
        rc[state["current_idx"]] = rc.get(state["current_idx"], 0) + 1
        current_retry = rc[state["current_idx"]]
        new_manifest["retry_count"] = current_retry
        new_manifest["repair_step_idx"] = state["current_idx"]

        # Phase 3: Adaptive escalation hint
        step_title_lower = step["title"].lower()
        escalation_hint = ""
        for keyword, hints in RETRY_ESCALATION.items():
            if keyword in step_title_lower:
                escalation_hint = hints.get(current_retry, "")
                if not escalation_hint and current_retry >= max(hints.keys()):
                    escalation_hint = hints[max(hints.keys())]
                break
        
        if escalation_hint:
            new_manifest["repair_feedback"] = f"{summary}\n\n{escalation_hint}"
            self._log("ESCALATION HINT", body=escalation_hint, node=node)
        else:
            new_manifest["repair_feedback"] = summary

        # T1.2 / P1-C: Check diag_rounds cap BEFORE routing to diagnostics
        _diag_count = diag_rounds.get(state["current_idx"], 0)
        if current_retry > self.MAX_STEP_RETRIES and _diag_count >= self.MAX_DIAG_ROUNDS_PER_STEP:
            self._log(
                "DIAG ROUNDS CAP",
                body=f"step_idx={state['current_idx']} diag_rounds={_diag_count} >= cap={self.MAX_DIAG_ROUNDS_PER_STEP}. Escalating to QA.",
                node=node,
            )
            new_manifest["route_hint"] = "finalize"
            new_manifest["qa_payload"] = (
                f"Step '{step['title']}' failed after {current_retry} retries "
                f"and {_diag_count} diagnostic rounds. Last error:\n{summary}\n\n"
                "The pipeline cannot continue automatically. Please review the logs and try a different approach."
            )
            return "qa"
        elif current_retry > self.MAX_STEP_RETRIES:
            return "diagnostics"
        else:
            return "generator"


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

        # ── Phase 1: Build FAISS semantic index (once, sub-ms per query after this) ──
        if self.use_tool_retriever and hasattr(self, "retriever"):
            # Build flat tool list from module2api for the retriever
            all_tools = []
            for module_path, api_list in self.module2api.items():
                for entry in api_list:
                    if isinstance(entry, dict):
                        all_tools.append(entry)
                    elif isinstance(entry, str):
                        all_tools.append({"name": entry, "description": "", "module": module_path})
            # Library list
            all_libs = [
                {"name": k, "description": v if isinstance(v, str) else str(v)}
                for k, v in self.library_content_dict.items()
            ]
            try:
                self.retriever.build_index(all_tools, data_lake_with_desc, all_libs)
                self._log("FAISS INDEX", body=f"Built index: {len(all_tools)} tools, {len(data_lake_with_desc)} data items, {len(all_libs)} libs", node="configure")
            except Exception as e:
                self._log("FAISS INDEX (warn)", body=f"Index build failed (fallback to LLM): {e}", node="configure")
        
        
        # Define the nodes(functions)
        # -------------------------------------------------------------------------------
        def _planner(self, state: AgentState) -> AgentState:
            node = "planner"
            self._log("ENTER NODE", body=f"state keys: {list(state.keys())}", node=node)

            # ------ RESUME FAST-PATH ------
            manifest = state.get("manifest") or {}

            # Si le plan existe déjà et des steps sont en cours, ne pas re-planifier
            existing_plan = state.get("plan", [])
            todo_steps = [s for s in existing_plan if s.get("status") == "todo"]
            if existing_plan and todo_steps and not manifest.get("route_hint"):
                self._log("PLANNER SKIP", body=f"Plan already exists with {len(todo_steps)} pending steps. Routing directly to orchestrator.", node=node)
                return {
                    "next_step": "orchestrator",
                    "messages": [AIMessage(content=f"<observe>Resuming existing plan ({len(todo_steps)} steps remaining).</observe>")],
                }

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
            batch_strategy = state.get("batch_strategy", "independent")
            msgs = [
                self.system_prompt,
                HumanMessage(content=f"CURRENT BATCH STRATEGY: {batch_strategy}\n\n" + instructions.PLANNER_PROMPT.format(temp_run_dir=state.get("run_temp_dir", ""))),
            ]
            # FIX G2a: Inject similar past pipelines from TemplateLibrary as few-shot examples
            if _TEMPLATE_OK and _TEMPLATE_LIB:
                try:
                    template_hints = _TEMPLATE_LIB.format_for_planner(user_prompt, n=3)
                    if template_hints:
                        msgs.append(HumanMessage(content=template_hints))
                        self._log("TEMPLATE HINTS", body=f"{_TEMPLATE_LIB.count()} templates available", node=node)
                except Exception as _te:
                    self._log("TEMPLATE HINTS (warn)", body=str(_te), node=node)
            
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


            # --- FIX 5: Sauvegarde du Checkpoint ---
            if status == "done":
                from genomeer.utils.checkpoint import CheckpointManager
                cp = CheckpointManager(state.get("run_temp_dir", ""), str(state.get("run_id", "unknown")))
                # Fusionner l'état courant avec les updates pour avoir l'état complet
                full_state = {**state, **updates}
                cp.save(full_state, state["current_idx"])
            
            # --- FIX 8: Version Tracker ---
            from genomeer.utils.version_tracker import VersionTracker
            tracker = VersionTracker()
            tracker.auto_record_from_step(step["title"], pending_code, env_name=state.get("env_name", "meta-env1"))
            
            if "version_tracker" not in new_manifest:
                new_manifest["version_tracker"] = tracker
            else:
                new_manifest["version_tracker"].tools.extend(tracker.tools)
                new_manifest["version_tracker"].databases.extend(tracker.databases)

            # --- FIX 9: Métriques ---
            if hasattr(self, "_metrics") and self._metrics:
                self._metrics.record_step_end(
                    step_idx=state["current_idx"],
                    step_title=step["title"],
                    status=status,
                    tool_name=step["title"], # or pending code tool
                    quality_level="fail" if status == "blocked" else "ok"
                )

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
            rc = state.get("retry_counts") or {}

            # ── T6.3: Global elapsed time guard ──────────────────────────────────
            # If the run has exceeded MAX_TOTAL_RUN_SECONDS, force finalizer immediately
            run_started_at = state.get("run_started_at")
            if run_started_at:
                elapsed = time.time() - run_started_at
                if elapsed > self.MAX_TOTAL_RUN_SECONDS:
                    self._log(
                        "GLOBAL TIMEOUT",
                        body=f"Run exceeded {self.MAX_TOTAL_RUN_SECONDS}s ({elapsed:.0f}s elapsed). Forcing finalizer.",
                        node=node,
                    )
                    return {
                        "next_step": "finalizer",
                        "messages": [AIMessage(content=f"<observe>[GLOBAL TIMEOUT] Run exceeded {self.MAX_TOTAL_RUN_SECONDS}s. Generating partial report.</observe>")],
                    }

            # ── T1.3: Global retry guard ──────────────────────────────────────────
            # If cumulative retries across all steps exceed the safety ceiling,
            # something is stuck in an undetected loop — force finalizer.
            _global_retry_ceiling = self.MAX_STEP_RETRIES * max(len(plan), 1) * 2
            if sum(rc.values()) > _global_retry_ceiling:
                self._log(
                    "GLOBAL RETRY CAP",
                    body=f"sum(retry_counts)={sum(rc.values())} > ceiling={_global_retry_ceiling}. Forcing finalizer.",
                    node=node,
                )
                return {
                    "next_step": "finalizer",
                    "messages": [AIMessage(content="<observe>[GLOBAL RETRY CAP] Too many retries across steps. Generating partial report.</observe>")],
                }
            # ─────────────────────────────────────────────────────────────────────

            if not plan:
                self._log("ORCHESTRATOR WARN", body="Empty plan received from planner. Routing to QA for clarification.", node=node)
                new_manifest = dict(state.get("manifest") or {})
                new_manifest["route_hint"] = "finalize"
                new_manifest["qa_payload"] = (
                    "The planner could not generate a step-by-step plan for your request. "
                    "Please rephrase your request or provide more details about the data and goal."
                )
                return {
                    "manifest": new_manifest,
                    "next_step": "qa",
                    "messages": [AIMessage(content="<observe>No plan generated. Asking user for clarification.</observe>")],
                }

            while idx < len(plan) and plan[idx]["status"] != "todo":
                idx += 1
            state["current_idx"] = idx
            
            if idx >= len(plan):
                # all steps are done -> hand off to FINALIZER
                self._log("EXIT NODE", body=f"all_done=True -> next_step=finalizer", node=node)
                trim_msgs = self._trim_messages(state.get("messages", []))
                return {
                    "current_idx": idx,
                    "per_sample_results": state.get("per_sample_results"),
                    "next_step": "finalizer",
                    "messages": trim_msgs + [AIMessage(content="<observe>All steps complete. Finalizing…</observe>")],
                }
            
            # P4-D: Parallel Batch Mode detection
            if state.get("batch_mode") and state.get("sample_manifest"):
                is_coassembly_phase2 = state.get("batch_strategy") == "coassembly" and plan[idx].get("phase") == 2
                is_independent = state.get("batch_strategy") == "independent"
                
                if is_coassembly_phase2 or is_independent:
                    self._log("BATCH DISPATCH", body=f"Delegating parallel execution to batch_orchestrator (idx={idx})", node=node)
                    return {
                        "current_idx": idx,
                        "next_step": "batch_orchestrator",
                        "messages": [AIMessage(content="<observe>Dispatching independent steps in parallel.</observe>")]
                    }

                self._log("EXIT NODE", body=f"all_done=True -> next_step=finalizer", node=node)
                trim_msgs = self._trim_messages(state.get("messages", []))
                return {
                    "current_idx": idx,
                    "per_sample_results": state.get("per_sample_results"),
                    "next_step": "finalizer",
                    "messages": trim_msgs + [AIMessage(content="<observe>All steps complete. Finalizing…</observe>")],
                }
            
            # otherwise go check inputs
            self._log("EXIT NODE", body=f"all_done=False\ncurrent_idx={idx}\nnext_step=input_guard", node=node)
            trim_msgs = self._trim_messages(state.get("messages", []))
            return {
                "current_idx": idx,
                "next_step": "input_guard",
                "messages": trim_msgs + [AIMessage(content=f"<running step={idx+1}/>\n<description>\n{plan[idx]['title']}\n</description>\n")],
            }

        def _batch_orchestrator(self, state: AgentState) -> AgentState:
            import os
            import copy
            import threading
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            node = "batch_orchestrator"
            self._log("ENTER NODE", body="Starting parallel execution of independent steps", node=node)
            
            samples = state.get("sample_manifest") or []
            if not samples:
                return {"next_step": "finalizer", "messages": [AIMessage(content="<observe>Error: batch_orchestrator called without sample_manifest.</observe>")]}

            # Bug 4 Fix: pre-install environment once before parallel threads
            env_name = state.get("env_name", "bio-agent-env1")
            from genomeer.runtime.env_manager import has_env, create_or_update_env
            from genomeer.utils.helper import preload_tool_versions
            
            if not has_env(env_name):
                self._log("BATCH ENV", body=f"Pre-installing environment '{env_name}' before launching parallel threads...", node=node)
                try:
                    create_or_update_env(env_name)
                except Exception as e:
                    self._log("BATCH ENV ERROR", body=f"Failed to install {env_name}: {e}", node=node)
                    return {"next_step": "finalizer", "messages": [AIMessage(content=f"<observe>Fatal error: Failed to prepare environment {env_name}. Error: {e}</observe>")]}
                
            # Bug 1 Fix: pre-warm version cache for common tools in a background thread
            tools_to_cache = [
                "fastp", "fastqc", "kraken2", "metaphlan4", "metaspades", "megahit", 
                "flye", "bowtie2", "samtools", "metabat2", "checkm2", "bracken", 
                "gtdbtk", "das_tool", "prokka", "diamond", "hmmer", "humann3"
            ]
            threading.Thread(target=preload_tool_versions, args=(env_name, tools_to_cache), daemon=True, name="tool-version-preloader").start()
                
            # P4-D.2: Semaphore for concurrency control
            concurrency = int(os.environ.get("GENOMEER_BATCH_CONCURRENCY", "4"))
            semaphore = threading.Semaphore(concurrency)
            
            per_sample = dict(state.get("per_sample_results") or {})
            idx = state["current_idx"]
            plan = state["plan"]
            
            def process_sample(sample_idx: int, sample_dict: dict) -> tuple:
                with semaphore:
                    sample_id = sample_dict.get("id", f"sample_{sample_idx}")
                    
                    # Clone state to isolate thread execution
                    local_state = dict(state)
                    local_state["manifest"] = copy.deepcopy(state.get("manifest", {}))
                    # Wipe global quality signals so they don't bleed between samples
                    local_state["manifest"].pop("quality_signals", None)
                    local_state["manifest"].pop("amr_genes_detected", None)
                    local_state["manifest"].pop("observations", None)
                    
                    local_state["plan"] = copy.deepcopy(plan)
                    local_state["messages"] = []  # Point Faible 1: prevent data race on messages list
                    import time as _time
                    local_state["run_started_at"] = _time.time()  # Point Faible 4: reset global timeout timer
                    local_state["current_idx"] = idx
                    local_state["retry_counts"] = {}
                    local_state["current_sample_id"] = sample_id
                    local_state["current_sample_idx"] = sample_idx
                    
                    # P4-D.3: Isolated run_temp_dir
                    local_state["run_temp_dir"] = os.path.join(state["run_temp_dir"], sample_id)
                    os.makedirs(local_state["run_temp_dir"], exist_ok=True)
                    
                    # Bug 3 Fix: global iterations cap to avoid infinite loops across step retries
                    max_iterations = self.MAX_STEP_RETRIES * len(local_state["plan"]) * 3
                    iteration_count = 0
                    
                    # Manual node chaining loop for this sub-plan
                    while local_state["current_idx"] < len(local_state["plan"]):
                        if iteration_count > max_iterations:
                            self._log("BATCH FATAL", body=f"Sample {sample_id} exceeded max iterations ({max_iterations}). Breaking out.", node=node)
                            break
                        iteration_count += 1
                        
                        curr_idx = local_state["current_idx"]
                        step = local_state["plan"][curr_idx]
                        if step["status"] != "todo":
                            local_state["current_idx"] += 1
                            continue
                            
                        # _input_guard
                        updates = self._input_guard(local_state)
                        local_state.update(updates)
                        if local_state.get("next_step") == "qa":
                            step["status"] = "error"
                            step["notes"] = "QA requested during batch parallel execution (unsupported)."
                            local_state["current_idx"] += 1
                            continue
                            
                        # _generator
                        updates = self._generator(local_state)
                        local_state.update(updates)
                        if local_state.get("next_step") == "qa":
                            step["status"] = "error"
                            step["notes"] = "QA requested during generation (unsupported)."
                            local_state["current_idx"] += 1
                            continue
                            
                        # _ensure_env
                        updates = self._ensure_env(local_state)
                        local_state.update(updates)
                        
                        # _executor
                        updates = self._executor(local_state)
                        local_state.update(updates)
                        
                        # _observer
                        updates = self._observer(local_state)
                        local_state.update(updates)
                        
                        # Bug 2: handle observer's next_step routing for retries
                        next_step = local_state.get("next_step")
                        
                        if next_step == "diagnostics":
                            updates = self._diagnostics(local_state)
                            local_state.update(updates)
                            next_step = local_state.get("next_step")
                            
                        if next_step == "generator":
                            # Force status to todo so the while loop picks it up for retry
                            local_state["plan"][curr_idx]["status"] = "todo"
                            # Do NOT increment current_idx
                        else:
                            # success (orchestrator) or final failure
                            local_state["current_idx"] += 1
                        
                    # Extract sample results
                    manifest = local_state.get("manifest", {})
                    result = {
                        "quality_signals": manifest.get("quality_signals", {}),
                        "amr_genes_detected": manifest.get("amr_genes_detected", []),
                        "observations": manifest.get("observations", []),
                        "retry_counts": local_state.get("retry_counts", {})
                    }
                    return sample_id, result
                    
            # Launch ThreadPool
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = [executor.submit(process_sample, i, s) for i, s in enumerate(samples)]
                for future in as_completed(futures):
                    try:
                        sample_id, res = future.result()
                        per_sample[sample_id] = res
                        self._log("BATCH ORCHESTRATOR", body=f"Sample {sample_id} completed successfully.", node=node)
                    except Exception as e:
                        self._log("BATCH ORCHESTRATOR ERROR", body=f"Future raised exception: {e}", node=node)
                        
            self._log("EXIT NODE", body="All parallel samples completed. Routing to finalizer.", node=node)
            return {
                "per_sample_results": per_sample,
                "current_idx": len(plan), # mark plan as complete
                "next_step": "finalizer",
                "messages": [AIMessage(content=f"<observe>Processed {len(samples)} samples in parallel mode.</observe>")]
            }
        
        def _input_guard(self, state: AgentState) -> AgentState:
            sid = state.get("current_sample_id")
            node = f"input_guard|{sid}" if sid else "input_guard"

            # T7: Safe step accessor
            step = self._get_current_step(state)
            if step is None:
                self._log("INPUT_GUARD GUARD", body="current_idx out of bounds — routing to finalizer", node=node)
                return {
                    "next_step": "finalizer",
                    "messages": [AIMessage(content="<observe>[GUARD] Plan index out of bounds in input_guard. Finalizing.</observe>")],
                }

            current_step_title = step["title"].strip()
            user_goal = state.get("last_prompt") or (state.get("messages", [HumanMessage(content="")])[0].content)
            manifest = dict(state.get("manifest") or {})

            # current run storage home lsdir
            temp_dir = state.get("run_temp_dir") or os.environ.get("BIOAGENT_TMP_DIR", "/tmp/bioagent")
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
            sid = state.get("current_sample_id")
            node = f"generator|{sid}" if sid else "generator"

            # T7: Safe step accessor
            step = self._get_current_step(state)
            if step is None:
                self._log("GENERATOR GUARD", body="current_idx out of bounds — routing to finalizer", node=node)
                return {
                    "next_step": "finalizer",
                    "messages": [AIMessage(content="<observe>[GUARD] Plan index out of bounds in generator. Finalizing.</observe>")],
                }
            env_name = state["env_name"]
            
            # detect repair mode
            manifest = state.get("manifest", {}) or {}
            repair_feedback = manifest.get("repair_feedback")
            is_diagnostic = isinstance(repair_feedback, str) and repair_feedback.strip().upper().startswith("DIAGNOSTICS_REQUEST:")
            temp_dir = state.get("run_temp_dir", "")
            files = self._list_ctx_files(temp_dir)
            files_str = "\n".join(f"- {f['name']} ({f['ext']}, {f['size_bytes']} bytes)" for f in files) or "<none>"

            # T8.4 (Residual): Format file registry for the context prompt in both normal and repair modes
            _file_registry = manifest.get("file_registry", {})
            _freg_str = "\n".join(
                f"  [{step_name}]: {', '.join(paths)}"
                for step_name, paths in _file_registry.items()
            ) or "<none>"
            files_str = f"{files_str}\n\nFiles produced by previous steps:\n{_freg_str}"
            
            _diag_rounds_dict = manifest.get("diagnostics_rounds", {})
            _diag_round = _diag_rounds_dict.get(state["current_idx"], 0)

            if repair_feedback:
                if is_diagnostic:
                    prompt = instructions.GENERATOR_PROMPT
                    content = instructions.GENERATOR_DIAGNOSTICS_MODE_PROMPT.format(
                        diagnostics_feedback=repair_feedback,
                        run_temp_dir=temp_dir,
                        file_registry=_freg_str,
                    )
                else:
                    prompt = instructions.GENERATOR_PROMPT_REPAIR
                    _slim_gen = self._slim_manifest(manifest, "generator")
                    _input_state_for_prompt = _slim_gen.get("input_state") or {"root_dir": temp_dir, "files": files}
                    content = instructions.GENERATOR_REPAIR_CTX_PROMPT.format(
                        user_goal=state['last_prompt'],
                        current_step_title=step['title'],
                        manifest=_input_state_for_prompt,
                        run_temp_dir=temp_dir,
                        repair_feedback=repair_feedback,
                        previous_code=(state.get("pending_code") or "").strip(),
                        last_result=(state.get("last_result") or "").strip(),
                        files_str=files_str,
                        file_registry=_freg_str,
                        diag_round=_diag_round,
                    )
            else:
                prompt = instructions.GENERATOR_PROMPT
                
                content = instructions.GENERATOR_CTX_PROMPT.format(
                    user_goal=state['last_prompt'],
                    current_step_title=step['title'],
                    manifest=self._slim_manifest(state['manifest'], "generator").get("input_state"),
                    run_temp_dir=temp_dir,
                    file_registry=_freg_str,
                    diag_round=_diag_round,
                )
            
            msgs = [
                self.system_prompt, 
                HumanMessage(content=prompt), 
                HumanMessage(content=content)
            ]
            
            self._log("ENTER NODE", body=f"step_idx={state['current_idx']}\nrepair_mode={bool(repair_feedback)}", node=node)
            resp = self._llm_invoke(node, "code_gen", msgs)
    
            sanitized_block = StateGraphHelper.sanitize_execute_block(resp.content)
            code, lang, env_hint = StateGraphHelper.parse_execute(sanitized_block)
        
            # AUTO-DETECT best environment from generated code
            resolved_env = resolve_env_for_code(
                code=code or "",
                lang=lang,
                env_hint=env_hint,
                current_env=env_name,
            )
            self._log("ENV RESOLVED", body=f"current={env_name} → resolved={resolved_env}", node=node)
        
            # ADAPTIVE TIMEOUT: long-running metagenomics steps get more time
            step_title_lower = step["title"].lower()
            adaptive_timeout = _adaptive_timeout(step_title_lower, manifest.get("timeout_seconds", 600))
            new_manifest = dict(manifest)
            new_manifest["timeout_seconds"] = adaptive_timeout
        
            code_key = "diagnostic_code" if is_diagnostic else "pending_code"
            updates = {
                code_key: code,
                "env_name": resolved_env,
                "manifest": new_manifest,
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
            

            # --- FIX 5: Sauvegarde du Checkpoint ---
            if status == "done":
                from genomeer.utils.checkpoint import CheckpointManager
                cp = CheckpointManager(state.get("run_temp_dir", ""), str(state.get("run_id", "unknown")))
                # Fusionner l'état courant avec les updates pour avoir l'état complet
                full_state = {**state, **updates}
                cp.save(full_state, state["current_idx"])
            
            # --- FIX 8: Version Tracker ---
            from genomeer.utils.version_tracker import VersionTracker
            tracker = VersionTracker()
            tracker.auto_record_from_step(step["title"], pending_code, env_name=state.get("env_name", "meta-env1"))
            
            if "version_tracker" not in new_manifest:
                new_manifest["version_tracker"] = tracker
            else:
                new_manifest["version_tracker"].tools.extend(tracker.tools)
                new_manifest["version_tracker"].databases.extend(tracker.databases)

            # --- FIX 9: Métriques ---
            if hasattr(self, "_metrics") and self._metrics:
                self._metrics.record_step_end(
                    step_idx=state["current_idx"],
                    step_title=step["title"],
                    status=status,
                    tool_name=step["title"], # or pending code tool
                    quality_level="fail" if status == "blocked" else "ok"
                )

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
            sid = state.get("current_sample_id")
            node = f"ensure_env|{sid}" if sid else "ensure_env"
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
            sid = state.get("current_sample_id")
            node = f"executor|{sid}" if sid else "executor"
            code = (state.get("pending_code") or "").strip()
            diagnostic_code = (state.get("diagnostic_code") or "").strip()
            diagnostic_mode = state.get("diagnostic_mode")

            # Point Faible 3: use external cancel_event if provided
            _cancel_event = getattr(self, "current_cancel_event", None) or threading.Event()

            # T2.5: Assert/fallback for run_temp_dir before any subprocess is launched.
            # Fix it at the process level so subprocesses always inherit RUN_TEMP_DIR.
            _run_temp_dir = state.get("run_temp_dir")
            if not _run_temp_dir:
                _run_temp_dir = os.environ.get("BIOAGENT_TMP_DIR", "/tmp/bioagent")
                self._log("RUN_TEMP_DIR FIX", body=f"run_temp_dir was None, using fallback: {_run_temp_dir}", node=node)
            # Always sync to os.environ so subprocess inheritance is guaranteed
            os.environ["RUN_TEMP_DIR"] = _run_temp_dir
            os.makedirs(_run_temp_dir, exist_ok=True)
            # Propagate the fix back into state for downstream nodes
            if not state.get("run_temp_dir"):
                state = {**state, "run_temp_dir": _run_temp_dir}

            # T2.3: Build extra_env dict — injected into every subprocess call
            _extra_env = {"RUN_TEMP_DIR": _run_temp_dir}

            env = state["env_name"]
            _default_timeout = state["manifest"].get("timeout_seconds", 600)
            
            # P2-B.2 & Point Faible 1: Extract input files including paths without quotes
            import re as _re
            _matches = _re.findall(
                r'(?:["\']([^"\']+\.(?:fastq|fq|fasta|fa|fna|bam|gz|fastq\.gz|fq\.gz))["\'])|((?:/[^/\s][^\n=]+|\w:[^\n=]+)\.(?:fastq|fq|fasta|fa|fna|bam|gz|fastq\.gz|fq\.gz))', 
                code or ''
            )
            _input_files = [m[0] or m[1] for m in _matches if os.path.exists(m[0] or m[1])]
            
            step = self._get_current_step(state)
            step_title = step["title"] if step else "unknown step"
            
            timeout = _estimate_timeout(step_title, _input_files, _default_timeout)
            
            last_result = ""

            if hasattr(self, "_metrics") and self._metrics:
                self._metrics.record_step_start(state.get("current_idx", 0), step_title)

            self._log("ENTER NODE", body=f"env={env}\ntimeout={timeout}s\nRUN_TEMP_DIR={_run_temp_dir}\ncode_preview=\n{code[:500] or '<no code>'}", node=node)

            if not code or (diagnostic_mode and not diagnostic_code):
                self._log("NO CODE", body="Skipping execution", node=node)
                self._log("EXIT NODE", body="next_step=observer", node=node)
                return {
                    "next_step": "observer",
                    "last_result": "No code produced by GENERATOR.",
                    "messages": [AIMessage(content="No code produced for this step.")],
                }

            if diagnostic_mode:
                code = diagnostic_code
                
            try:
                import threading
                _cancel_event = threading.Event()
                state["cancel_event"] = _cancel_event

                # ── TOOL OUTPUT CACHE (Fix 1) ──────────────────────────────────────────
                _tool_cache_key = None
                _cached_result = None
                if hasattr(self, "_cache") and self._cache and self._cache.tool:
                    try:
                        import hashlib
                        _code_hash = hashlib.sha256((state.get("pending_code") or "").encode()).hexdigest()[:16]
                        _env_hash = str(env)
                        _files_snapshot = sorted([
                            f"{f['name']}:{f.get('size_bytes', 0)}"
                            for f in self._list_ctx_files(state.get("run_temp_dir", ""))
                        ])
                        _tool_cache_key = self._cache.tool.make_key(
                            step_title,
                            {"code_hash": _code_hash, "env": _env_hash, "files": _files_snapshot},
                            {},
                        )
                        _cached_result = self._cache.tool.get(_tool_cache_key)
                        if _cached_result:
                            self._log("TOOL CACHE HIT", body=f"step={step_title}", node=node)
                            out = _cached_result
                    except Exception as _ce:
                        self._log("TOOL CACHE (warn)", body=str(_ce), node=node)
                        _tool_cache_key = None
                # ── END TOOL CACHE LOOKUP ──────────────────────────────────────────────

                if not _cached_result:
                    if (code.strip().startswith("#!R") or code.strip().startswith("# R code") or code.strip().startswith("# R script")):
                        r_code = re.sub(r"^#!R|^# R code|^# R script", "", code, 1).strip()  # noqa: B034
                        out = run_with_timeout(
                            run_r_code,
                            args=[r_code],
                            kwargs={
                                "env_name": env,
                                "extra_env": _extra_env,      # T2.3
                                "run_temp_dir": _run_temp_dir, # T2.1
                            },
                            timeout=timeout,
                            cancel_event=_cancel_event,
                        )
                    elif (code.strip().startswith("#!BASH") or code.strip().startswith("# Bash script") or code.strip().startswith("#!CLI")):
                        if code.strip().startswith("#!CLI"):
                            cli_command = re.sub(r"^#!CLI", "", code, 1).strip().replace("\n", " ")  # noqa: B034
                            out = run_with_timeout(
                                run_bash_script,
                                args=[cli_command],
                                kwargs={
                                    "env_name": env,
                                    "extra_env": _extra_env,      # T2.3
                                    "run_temp_dir": _run_temp_dir, # T2.1
                                },
                                timeout=timeout,
                                cancel_event=_cancel_event,
                            )

                # ── TOOL CACHE SAVE (Fix 1) ─────────────────────────────────────
                if _tool_cache_key and not _cached_result and out:
                    try:
                        self._cache.tool.set(_tool_cache_key, out, ttl_seconds=3600 * 24 * 7)  # TTL 7j
                    except Exception as _cse:
                        self._log("TOOL CACHE SAVE (warn)", body=str(_cse), node=node)
                # ── END TOOL CACHE SAVE ────────────────────────────────────────────────
                    else:
                        bash_script = re.sub(r"^#!BASH|^# Bash script", "", code, 1).strip()  # noqa: B034
                        out = run_with_timeout(
                            run_bash_script,
                            args=[bash_script],
                            kwargs={
                                "env_name": env,
                                "extra_env": _extra_env,      # T2.3
                                "run_temp_dir": _run_temp_dir, # T2.1
                            },
                            timeout=timeout,
                            cancel_event=_cancel_event,
                        )
                else:
                    # Inject custom functions into the Python execution environment
                    self._inject_custom_functions_to_repl()  # for _persistent_namespace only
                    code = re.sub(r"^\s*#!PY\s*\r?\n", "", code, count=1)
                    # T4: Build an isolated step namespace — prevents variable leakage between steps
                    _step_namespace = {
                        "__builtins__": __builtins__,
                        "run_dir": _run_temp_dir,          # always available to generated code
                    }
                    # Copy custom functions from _persistent_namespace if present (T4 preserves custom tools)
                    from genomeer.utils.helper import _persistent_namespace as _pns
                    for _k, _v in _pns.items():
                        if callable(_v):
                            _step_namespace[_k] = _v
                    out = run_with_timeout(
                        run_python_code,
                        args=[code],
                        kwargs={
                            "env_name": env,
                            "extra_env": _extra_env,           # T2.3
                            "run_temp_dir": _run_temp_dir,     # T2.1
                            "step_namespace": _step_namespace, # T4
                        },
                        timeout=timeout,
                        cancel_event=_cancel_event,
                    )

                # ── AXE 3.3: Intelligent output parser instead of blind truncation ──
                # FIX-2: retrieve step from state directly (step was undefined in this scope)
                _current_plan = state.get("plan", [])
                _current_idx  = state.get("current_idx", 0)
                _step_obj     = _current_plan[_current_idx] if _current_plan and _current_idx < len(_current_plan) else {}
                step_title    = _step_obj.get("title", "")
                out_dir = state.get("run_temp_dir", "")
                if _PARSERS_OK and out and len(out) > 2000:
                    parsed = _smart_parse_output(
                        step_title,
                        out,
                        result_dict=None,
                        output_dir=out_dir,
                    )
                    if parsed and len(parsed) < len(out):
                        out = parsed
                elif out and len(out) > 12000:
                    out = out[:12000] + "\n...<truncated>"
                    
                last_result = out or ""
                self._log("EXECUTION RESULT", body=last_result[:2000], node=node)
            except Exception as e:
                tb = traceback.format_exc()
                _etype = type(e).__name__
                # T6.2: Distinguish explicit timeout from other errors for clearer observer feedback
                if "Timeout" in _etype or "TimeoutError" in _etype:
                    _current_plan = state.get("plan", [])
                    _current_idx  = state.get("current_idx", 0)
                    _step_obj     = _current_plan[_current_idx] if _current_plan and _current_idx < len(_current_plan) else {}
                    step_title    = _step_obj.get("title", "unknown step")
                    last_result = (
                        f"[TIMEOUT] Step '{step_title}' exceeded {timeout}s time limit. "
                        f"No output produced. The subprocess may still be running in the background. "
                        f"Consider increasing the timeout or breaking this step into smaller sub-steps."
                    )
                else:
                    last_result = f"[EXECUTION ERROR] {_etype}: {e}\n"
                    last_result += f"traceback: {tb}"
                self._log("EXECUTION ERROR", body=last_result, node=node)
            
            
            self._log("EXIT NODE", body="next_step=observer", node=node)
            result_key = "diagnostic_observation" if diagnostic_mode else "last_result"
            
            _stored_result = last_result[:4000] + "\n...<truncated for state storage>" if len(last_result) > 4000 else last_result
            
            updates = {
                "next_step": "observer", #end
                result_key: last_result,
                "messages": [AIMessage(content=f"<observe>Code Execution output: '{_stored_result}'</observe>")],
            }
            return updates
        
        def _observer(self, state: AgentState) -> AgentState:
            sid = state.get("current_sample_id")
            node = f"observer|{sid}" if sid else "observer"

            # T7: Safe step accessor — prevents IndexError when current_idx >= len(plan)
            step = self._get_current_step(state)
            if step is None:
                self._log("OBSERVER GUARD", body="current_idx out of bounds — routing to finalizer", node=node)
                return {
                    "next_step": "finalizer",
                    "messages": [AIMessage(content="<observe>[GUARD] Plan index out of bounds. Finalizing.</observe>")],
                }

            diagnostic_mode = state.get("diagnostic_mode")
            
            if diagnostic_mode:
                payload = instructions.OBSERVER_DIAGNOSTIC_CTX_PROMPT.format(
                    user_goal=state['last_prompt'],
                    current_step_title=step['title'],
                    manifest=self._slim_manifest(state['manifest'], "observer"),
                    code=(state.get("pending_code") or "").strip(),
                    result=(state.get("last_result") or state.get("diagnostic_observation") or "(no output)"),
                    diagnostic_code=(state.get("diagnostic_code") or "").strip(),
                    diagnostic_output=(state.get("diagnostic_observation") or "").strip(),
                )
            else:
                payload = instructions.OBSERVER_CTX_PROMPT.format(
                    user_goal=state['last_prompt'],
                    current_step_title=step['title'],
                    manifest=self._slim_manifest(state['manifest'], "observer"),
                    code=(state.get("pending_code") or "").strip(),
                    result=(state.get("last_result") or state.get("diagnostic_observation") or "(no output)"),
                )
            msgs = [
                self.system_prompt,
                HumanMessage(content=instructions.OBSERVER_PROMPT),
                HumanMessage(content=payload),
            ]

            self._log("ENTER NODE", body=f"step_idx={state['current_idx']}\nstep_title={step['title']}", node=node)

            # ── Biological Quality Gate (GAP4 — multi-tool, FIX G10) ─────────────
            import json as _json_qg
            pending_code = (state.get("pending_code") or "").lower()
            last_result_text = (state.get("last_result") or "")
            quality_annotations = []
            first_fail_msg: str | None = None

            for tool_name in [
                "run_fastp", "run_fastqc", "run_kraken2", "run_metaphlan4",
                "run_metaspades", "run_megahit", "run_flye",
                "run_host_decontamination",
                "compute_coverage_samtools", "run_metabat2", "run_checkm2",
                "run_bracken", "run_gtdbtk", "run_das_tool",
            ]:
                if tool_name not in pending_code:
                    continue
                # Try to extract result dict (nested JSON-aware)
                result_dict = None
                try:
                    for _jm in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', last_result_text):
                        try:
                            result_dict = _json_qg.loads(_jm.group())
                            break
                        except Exception:
                            continue
                except Exception:
                    pass
                qa_level, qa_msg = check_quality(tool_name, result_dict, last_result_text)
                quality_annotations.append(format_quality_message(qa_level, qa_msg))
                self._log("QUALITY GATE", body=f"tool={tool_name} level={qa_level}\n{qa_msg}", node=node)
                # FIX G10: collect ALL fails instead of breaking at first match
                if qa_level == "fail" and first_fail_msg is None:
                    first_fail_msg = qa_msg

            quality_annotation = "\n".join(quality_annotations)

            # Hard fail: any gate failure forces blocked immediately (skip LLM)
            if first_fail_msg:
                rc = dict(state.get("retry_counts") or {})
                diag_rounds = dict(state.get("manifest", {}).get("diagnostics_rounds", {}))
                new_manifest = dict(state["manifest"])
                
                next_step = self._route_blocked_step(
                    state, rc, diag_rounds, first_fail_msg, step, new_manifest, node="observer"
                )
                
                plan = list(state["plan"])
                plan[state["current_idx"]] = {**plan[state["current_idx"]], "status": "blocked", "notes": first_fail_msg}
                self._log("QUALITY GATE BLOCK", body=f"Forcing blocked: {first_fail_msg}", node=node)
                return {
                    "plan": plan,
                    "current_idx": state["current_idx"],
                    "next_step": next_step,
                    "messages": [AIMessage(content=f"<observe>{quality_annotation}</observe>")],
                    "manifest": new_manifest,
                    "retry_counts": rc,
                    "diagnostic_mode": False,
                    "diagnostic_code": False,
                    "diagnostic_observation": False,
                }
            # ── End Quality Gate ─────────────────────────────────────────────────

            # Prepend quality annotation to observer payload if we have one
            if quality_annotation:
                payload = f"{quality_annotation}\n\n{payload}"

            resp = self._llm_invoke(node, "observe_and_status", msgs)

            status, summary = StateGraphHelper.parse_status(resp.content)

            # T11.2: Handle 'unknown' status (tag absent from LLM output) — ask again with a short prompt
            if status == "unknown":
                self._log("STATUS UNKNOWN", body="Observer omitted <STATUS> tag. Retrying with clarification prompt.", node=node)
                try:
                    _retry_msgs = [
                        self.system_prompt,
                        HumanMessage(content=(
                            f"You just wrote this observation:\n\n{resp.content[:2000]}\n\n"
                            "Was the step successful? Reply with EXACTLY ONE of the following lines (nothing else):\n"
                            "<STATUS:done>\n<STATUS:blocked>"
                        )),
                    ]
                    _retry_resp = self._llm_invoke(node, "status_clarification", _retry_msgs)
                    status, summary = StateGraphHelper.parse_status(_retry_resp.content)
                    if status == "unknown":
                        # Still unknown after retry — default to blocked to be safe
                        self._log("STATUS UNKNOWN \u2192 FORCED BLOCKED", body="Still unknown after retry. Defaulting to blocked.", node=node)
                        status = "blocked"
                        summary = f"[OBSERVER FALLBACK] Status unclear after two LLM calls. Treating as blocked. Raw: {_retry_resp.content[:200]}"
                    else:
                        self._log("STATUS UNKNOWN \u2192 CLARIFIED", body=f"Clarified as {status}", node=node)
                except Exception as _retry_exc:
                    self._log("STATUS UNKNOWN (retry error)", body=str(_retry_exc), node=node)
                    status = "blocked"
                    summary = "[STATUS UNKNOWN] Could not determine step status."

            next_step = "generator" if status == "blocked" else "orchestrator"
            next_idx = state["current_idx"] + (0 if status == "blocked" else 1)
            
            new_manifest = dict(state["manifest"])

            # ── Extraire et persister les quality_signals dans le manifest ──────────
            _obs_parsed = _robust_parser.parse_observer_output(resp.content, step_title=step["title"])
            if _obs_parsed.quality_signals:
                new_manifest["quality_signals"] = {
                    **dict(state["manifest"].get("quality_signals") or {}),
                    **_obs_parsed.quality_signals,
                }

            # ── Extraire les gènes AMR détectés et les persister ───────────────────
            _amr_pattern = re.compile(
                r'\b(bla[A-Z]{2,6}|van[A-Z]|mec[A-Z]|mcr-\d|erm[A-Z]|tet[A-Z]|qnr[A-Z]|sul\d|aac|aph|cfr)\b',
                re.IGNORECASE
            )
            _last_result_text = (state.get("last_result") or "")
            _found_amr = list(set(_amr_pattern.findall(_last_result_text)))
            if _found_amr:
                _existing_amr = list(new_manifest.get("amr_genes_detected") or [])
                new_manifest["amr_genes_detected"] = list(set(_existing_amr + _found_amr))

            rc = dict(state.get("retry_counts") or {})
            diag_rounds = dict(state.get("manifest", {}).get("diagnostics_rounds", {}))
            if status == "blocked":
                next_step = self._route_blocked_step(
                    state, rc, diag_rounds, summary, step, new_manifest, node="observer"
                )
                
                # logs
                self._log("STATUS", body=f"blocked=True\nnotes=\n{summary}", node=node)
                self._log("EXIT NODE", body=f"next_step={next_step} (retry same step)", node=node)

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
                # T8.1: Keep last 10 observations instead of 5 to avoid losing file context
                _all_obs = list(new_manifest.get("observations", [])) + [obs]
                new_manifest["observations"] = _all_obs[-10:]

                # T8.2/8.3: Populate file_registry with new files produced by this step
                try:
                    _temp_dir = state.get("run_temp_dir", "")
                    if _temp_dir:
                        _before_files = set(
                            f["name"] for obs_prev in new_manifest.get("observations", [])[:-1]
                            for f in obs_prev.get("files_snapshot", [])
                        )
                        _after_files = self._list_ctx_files(_temp_dir)
                        _new_files = [
                            os.path.join(_temp_dir, f["name"])
                            for f in _after_files
                            if f["name"] not in _before_files
                        ]
                        if _new_files:
                            _freg = dict(new_manifest.get("file_registry") or {})
                            _freg[step["title"]] = _new_files
                            new_manifest["file_registry"] = _freg
                            self._log("FILE REGISTRY", body=f"{len(_new_files)} new files registered for '{step['title']}'", node=node)
                except Exception as _freg_exc:
                    self._log("FILE REGISTRY (warn)", body=str(_freg_exc), node=node)


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
            

            # --- FIX 5: Sauvegarde du Checkpoint ---
            if status == "done":
                from genomeer.utils.checkpoint import CheckpointManager
                cp = CheckpointManager(state.get("run_temp_dir", ""), str(state.get("run_id", "unknown")))
                # Fusionner l'état courant avec les updates pour avoir l'état complet
                full_state = {**state, **updates}
                cp.save(full_state, state["current_idx"])
            
            # --- FIX 8: Version Tracker ---
            from genomeer.utils.version_tracker import VersionTracker
            tracker = VersionTracker()
            tracker.auto_record_from_step(step["title"], pending_code, env_name=state.get("env_name", "meta-env1"))
            
            if "version_tracker" not in new_manifest:
                new_manifest["version_tracker"] = tracker
            else:
                new_manifest["version_tracker"].tools.extend(tracker.tools)
                new_manifest["version_tracker"].databases.extend(tracker.databases)

            # --- FIX 9: Métriques ---
            if hasattr(self, "_metrics") and self._metrics:
                self._metrics.record_step_end(
                    step_idx=state["current_idx"],
                    step_title=step["title"],
                    status=status,
                    tool_name=step["title"], # or pending code tool
                    quality_level="fail" if status == "blocked" else "ok"
                )

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
            sid = state.get("current_sample_id")
            node = f"diagnostics|{sid}" if sid else "diagnostics"

            # T7: Safe step accessor
            step = self._get_current_step(state)
            if step is None:
                self._log("DIAGNOSTICS GUARD", body="current_idx out of bounds — routing to finalizer", node=node)
                return {
                    "next_step": "finalizer",
                    "messages": [AIMessage(content="<observe>[GUARD] Plan index out of bounds in diagnostics. Finalizing.</observe>")],
                }

            _slim = self._slim_manifest(state.get("manifest", {}), "diagnostics") or {}
            retry_count = _slim.get("retry_count", 0)
            observer_summary = _slim.get("repair_feedback", "").strip()
            last_code = (state.get("pending_code") or "").strip()

            prompt = instructions.DIAGNOSTICS_PROMPT
            ctx = instructions.DIAGNOSTICS_CTX_PROMPT.format(
                user_goal=state.get("last_prompt", ""),
                current_step_title=step["title"],
                retry_count=retry_count,
                observer_summary=observer_summary or "<none>",
                last_code=last_code or "<none>",
                run_temp_dir=state.get("run_temp_dir", ""),
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
            new_manifest = dict(state.get("manifest", {}))
            new_manifest["repair_feedback"] = f"DIAGNOSTICS_REQUEST:\n{resp.content}"
            new_manifest["repair_step_idx"] = state["current_idx"]

            self._log("EXIT NODE", body="next_step=generator (probe code)", node=node)

            # T1.1: Do NOT reset retry_counts here — rc.pop() was the root cause of the infinite loop.
            # Keeping rc intact means the observer can correctly detect when retries are exhausted
            # and route OUT of diagnostics instead of looping back endlessly.
            rc = dict(state.get("retry_counts") or {})

            # T1.1: Increment diag_rounds for this step so _observer can enforce MAX_DIAG_ROUNDS_PER_STEP
            diag_rounds = dict(new_manifest.get("diagnostics_rounds") or {})
            _current_diag_n = diag_rounds.get(state["current_idx"], 0) + 1
            diag_rounds[state["current_idx"]] = _current_diag_n
            new_manifest["diagnostics_rounds"] = diag_rounds
            self._log(
                "DIAG ROUND COUNTER",
                body=f"step_idx={state['current_idx']} diag_round={_current_diag_n}/{self.MAX_DIAG_ROUNDS_PER_STEP}",
                node=node,
            )

            return {
                "retry_counts": rc,      # preserved — NOT reset
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

            # --- FIX 8 & 9: Sauvegarde Metrics & VersionTracker ---
            if hasattr(self, "_metrics") and self._metrics:
                self._metrics.save(temp_dir)
            if "version_tracker" in manifest:
                manifest["version_tracker"].save(temp_dir)
                manifest["tool_versions"] = manifest["version_tracker"].as_dict()
                del manifest["version_tracker"]

            run_id = state.get("run_id")
            pub = manifest.get("publisher") or {}
            base_url = (pub.get("base_url") or "").rstrip("/")

            files = self._list_ctx_files(temp_dir)
            def _want(relname: str) -> bool:
                name = relname.lower()
                SKIP = (".cache/", "__pycache__", ".ipynb_checkpoints", ".mamba", ".micromamba")
                return not any(x in name for x in SKIP)

            expose_paths = [f["name"] for f in files if _want(f["name"])]

            # Auto-évaluation quantifiée post-pipeline
            try:
                from genomeer.evaluation.benchmark import PipelineOutputEval
                _eval_metrics = {
                    **(manifest.get("quality_signals") or {}),
                    "amr_genes_detected": manifest.get("amr_genes_detected", []),
                    "output_files": [str(Path(temp_dir) / p) for p in expose_paths[:20]],
                }
                _eval_report = PipelineOutputEval().evaluate(_eval_metrics)
                manifest["eval_score"]  = round(_eval_report.overall_score, 3)
                manifest["eval_fails"]  = _eval_report.fail_count
                manifest["eval_warns"]  = _eval_report.warn_count
                self._log(
                    "PIPELINE EVAL",
                    body=_eval_report.summary(),
                    node=node
                )
            except Exception as _eval_exc:
                self._log("PIPELINE EVAL (warn)", body=str(_eval_exc), node=node)

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

            # ── RAG CONTEXT pour interprétation biologique sourcée ──────────────
            rag_context = ""
            if hasattr(self, "bio_retriever"):
                if hasattr(self, "_rag_build_thread") and self._rag_build_thread.is_alive():
                    self._rag_build_thread.join(timeout=10)
                    if self._rag_build_thread.is_alive():
                        self._log("BIO RAG (warn)", body="RAG build still in progress after 10s — using partial index", node=node)
                try:
                    from genomeer.model.bio_rag import build_finalizer_rag_context
                    _pipeline_results = {
                        "amr_genes":         manifest.get("amr_genes_detected", []),
                        "pathways":          manifest.get("top_pathways", []),
                        "assembly_n50":      manifest.get("quality_signals", {}).get("n50_bp"),
                        "mean_completeness": manifest.get("quality_signals", {}).get("mean_completeness"),
                        "classified_pct":    manifest.get("quality_signals", {}).get("classified_pct"),
                    }
                    
                    self._log("FINALIZER", body=f"BioRAG status: {self.bio_rag_status}", node=node)
                    if self.bio_rag_status != "ready":
                        rag_context += f"Note: Biological context database is incomplete (status: {self.bio_rag_status}). Interpretations may rely on fallback offline thresholds.\n\n"
                    
                    rag_context += build_finalizer_rag_context(self.bio_retriever, _pipeline_results)
                    if rag_context:
                        self._log("RAG CONTEXT", body=f"Injected {len(rag_context)} chars of bio context", node=node)
                except Exception as _rag_exc:
                    self._log("RAG CONTEXT (warn)", body=str(_rag_exc), node=node)
            # ── END RAG CONTEXT ──────────────────────────────────────────────────
        
            msgs = [
                SystemMessage(content=instructions.FINALIZER_PROMPT),
                HumanMessage(content=instructions.FINALIZER_CTX_PROMPT.format(
                    user_goal=state.get("last_prompt"),
                    plan=state.get("plan"),
                    observation=observations,
                    artifacts=artifacts,
                    biological_context=rag_context,
                ))
            ]
            resp = self._llm_invoke(node, "final_report", msgs)
            self._log("EXIT NODE", body="final report generated", node=node)

            # FIX G2b: persist successful pipeline to TemplateLibrary for future few-shot reuse
            if _TEMPLATE_OK and _TEMPLATE_LIB:
                try:
                    plan_steps = state.get("plan", [])
                    all_done = plan_steps and all(s.get("status") == "done" for s in plan_steps)
                    if all_done:
                        tools_used = [obs.get("title", "") for obs in observations]
                        _TEMPLATE_LIB.save(
                            task_summary=(state.get("last_prompt") or "")[:200],
                            steps=plan_steps,
                            tools_used=tools_used,
                            success_metrics=manifest.get("quality_signals") or {},
                        )
                        self._log("TEMPLATE SAVED", body=f"Saved pipeline. Total templates: {_TEMPLATE_LIB.count()}", node=node)
                except Exception as _te:
                    self._log("TEMPLATE SAVE (warn)", body=str(_te), node=node)

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
        self.batch_orchestrator = types.MethodType(_batch_orchestrator, self)
        
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
        workflow.add_node("batch_orchestrator", self.batch_orchestrator)

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
                "batch_orchestrator": "batch_orchestrator",
                "finalizer": "finalizer",
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

        # ── AXE 2.3: SqliteSaver for cross-session persistence (fallback to MemorySaver) ──
        if _SQLITE_OK:
            try:
                _sqlite_path = str(
                    Path.home() / ".genomeer" / "checkpoints.db"
                )
                Path(_sqlite_path).parent.mkdir(parents=True, exist_ok=True)
                _conn = sqlite3.connect(_sqlite_path, check_same_thread=False)
                self._sqlite_path = _sqlite_path
                self.checkpointer = SqliteSaver(_conn)
                self._log("CHECKPOINTER", body=f"SqliteSaver → {_sqlite_path}", node="init")
            except Exception as _e:
                self.checkpointer = MemorySaver()
                self._log("CHECKPOINTER", body=f"SqliteSaver failed ({_e}), fallback to MemorySaver", node="init")
        else:
            self.checkpointer = MemorySaver()
            self._log("CHECKPOINTER", body="MemorySaver (install langgraph-checkpoint-sqlite for persistence)", node="init")
        self.app.checkpointer = self.checkpointer

        # Purge sessions LangGraph plus vieilles que 30 jours (non-bloquant)
        def _purge_old_sessions(db_path, days=30):
            try:
                import sqlite3, time
                cutoff = time.time() - (days * 86400)
                conn = sqlite3.connect(db_path)
                for table in ("checkpoints", "writes"):
                    try:
                        conn.execute(f"DELETE FROM {table} WHERE created_at < ?", (cutoff,))
                    except Exception:
                        pass
                conn.commit()
                conn.close()
            except Exception:
                pass

        if _SQLITE_OK and hasattr(self, '_sqlite_path'):
            threading.Thread(
                target=_purge_old_sessions,
                args=(self._sqlite_path,),
                daemon=True
            ).start()


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

        # FIX G1: use FAISS semantic_retrieval (sub-ms, no tokens wasted)
        # Falls back automatically to prompt_based_retrieval if index not built
        if hasattr(self, "retriever") and self.retriever._index is not None:
            step_query = prompt  # caller already combines user_goal + step title
            selected_resources = self.retriever.semantic_retrieval(step_query, k=15)
        else:
            # Fallback: LLM-based retrieval (FAISS index not ready)
            selected_resources = self.retriever.prompt_based_retrieval(prompt, resources, llm=self.llm)

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
            try:
                import uvicorn
                from fastapi import FastAPI
                from genomeer.agent.v2.utils.artifacts_service import create_artifacts_router
            except ImportError:
                self._log(
                    "ARTIFACTS SERVER (warn)",
                    body="fastapi/uvicorn not installed. Run: pip install genomeer[server]",
                    node="init"
                )
                return
            app = FastAPI()
            app.include_router(create_artifacts_router(prefix=prefix))
            uvicorn.run(app, host=host, port=port, log_level="warning")
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
                    module_name = "genomeer.tools.function.metagenomics"  # T10: correct fallback module
                    tool["module"] = module_name
                    warnings.warn(
                        f"[T10] Could not resolve module for tool '{tool.get('name', '?')}'. "
                        "Falling back to 'genomeer.tools.function.metagenomics'.",
                        stacklevel=2,
                    )
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
                    module_name = "genomeer.tools.function.metagenomics"  # T10: correct fallback module
                    tool.module_name = module_name
                    warnings.warn(
                        f"[T10] Could not resolve module for tool '{getattr(tool, 'name', '?')}'. "
                        "Falling back to 'genomeer.tools.function.metagenomics'.",
                        stacklevel=2,
                    )

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
    def _get_current_step(self, state: "AgentState") -> dict | None:
        """T7: Safe accessor for the current plan step.

        Returns None if current_idx is out of bounds instead of raising IndexError.
        All nodes should use this instead of state["plan"][state["current_idx"]] directly.
        """
        plan = state.get("plan") or []
        idx = state.get("current_idx", 0)
        if idx < 0 or idx >= len(plan):
            self._log(
                "_get_current_step: OOB",
                body=f"current_idx={idx} len(plan)={len(plan)} — routing to finalizer",
                node="guard",
            )
            return None
        return plan[idx]

    def _build_initial_state(self, prompt: str, staged: list, session_id: str | None, tmp: str) -> dict:
        """
        FIX-1: Single source of truth for the initial AgentState.
        Eliminates the verbatim duplication between go() and go_stream().
        """
        import os as _os
        # T2.5: ensure RUN_TEMP_DIR is set at the process level immediately
        if tmp:
            os.environ["RUN_TEMP_DIR"] = tmp
        return {
            "messages": [HumanMessage(content=prompt)],
            "next_step": None,
            "env_name": "bio-agent-env1",
            "env_ready": False,
            "pending_code": None,
            "manifest": {
                "timeout_seconds": self.timeout_seconds,
                "observations": [],
                "file_registry": {},   # T8.2: persistent file registry {step_title: [abs_paths]}
                "attachments": staged,
                "interaction_mode": getattr(self, "interaction_mode", "auto"),
                "diagnostics_rounds": {},  # T1.1: {step_idx: n_diag_rounds}
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
            "run_id": tmp.split(_os.sep)[-1],
            "run_started_at": time.time(),    # T6.3: global elapsed time guard
            "batch_mode": None,
            "batch_strategy": "independent",
            "sample_manifest": None,
            "current_sample_idx": None,
            "current_sample_id": None,
            "per_sample_results": None,
        }

    def go(self, prompt, mode: str = "dev", attachments: list[str] | None = None, session_id: str | None = None, cancel_event: Any = None):
        """Execute the agent with the given prompt.
        Args:
            prompt: The user's query
            mode: 'dev' (default) shows everything; 'prod' hides HumanMessage outputs.
        """
        self.critic_count = 0
        self.user_task = prompt
        self.current_cancel_event = cancel_event
        thread_id = session_id or str(uuid4())
        
        assert mode in ("dev", "prod"), "mode must be 'dev' or 'prod'"
        def _is_human(msg) -> bool:
            return getattr(msg, "type", "").lower() == "human"

        if self.use_tool_retriever:
            selected_resources_names = self._prepare_resources_for_retrieval(prompt)
            self.update_system_prompt_with_selected_resources(selected_resources_names)

        with run_workdir("run", session_id) as tmp:
            staged = self._stage_attachments(tmp, attachments or [])
            
            # --- FIX 3: Purge des modules & Fix 5: Checkpoints ---
            from genomeer.utils.helper import _persistent_namespace
            _persistent_namespace.clear()
            
            from genomeer.utils.checkpoint import CheckpointManager
            from genomeer.utils.metrics import RunMetrics
            
            cp = CheckpointManager(tmp, thread_id)
            if cp.exists():
                inputs = cp.load()
                self._log("CHECKPOINT", body=f"Resuming from step {inputs.get('current_idx', 0)}", node="driver")
                if not hasattr(self, "_metrics") or not self._metrics:
                    self._metrics = RunMetrics(thread_id, tmp)
            else:
                if not self._has_session_state(thread_id):
                    # FIRST TURN OF THIS SESSION -> full bootstrap state
                    inputs = self._build_initial_state(prompt, staged, session_id, tmp)
                    self._metrics = RunMetrics(thread_id, tmp)
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

            self._flush_log()
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
            
            # --- FIX 3: Purge des modules & Fix 5: Checkpoints ---
            from genomeer.utils.helper import _persistent_namespace
            _persistent_namespace.clear()
            
            from genomeer.utils.checkpoint import CheckpointManager
            from genomeer.utils.metrics import RunMetrics
            
            cp = CheckpointManager(tmp, thread_id)
            if cp.exists():
                inputs = cp.load()
                self._log("CHECKPOINT", body=f"Resuming from step {inputs.get('current_idx', 0)}", node="driver")
                if not hasattr(self, "_metrics") or not self._metrics:
                    self._metrics = RunMetrics(thread_id, tmp)
            else:
                if not self._has_session_state(thread_id):
                    # FIRST TURN OF THIS SESSION -> full bootstrap state
                    inputs = self._build_initial_state(prompt, staged, session_id, tmp)
                    self._metrics = RunMetrics(thread_id, tmp)
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
                    self._flush_log()
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
            self._flush_log()
