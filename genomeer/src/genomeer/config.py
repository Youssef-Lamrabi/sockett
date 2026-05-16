import os
from dataclasses import dataclass, asdict

@dataclass
class GenomeerConfig:
    path: str = "./data"
    run_dir: str = ".genomeer_runs"
    timeout_seconds: int = 3600  # BUG-51: increased to 1h for metagenomics assembly
    llm: str = "ollama/llama3.1"
    temperature: float = 0.7
    use_tool_retriever: bool = True
    base_url: str | None = None
    api_key: str | None = None
    source: str | None = None

    def __post_init__(self):
        self.path = os.getenv("GENOMEER_DATA_PATH", self.path)
        self.run_dir = os.getenv("GENOMEER_RUN_DIR", self.run_dir)
        self.llm = os.getenv("GENOMEER_LLM", self.llm)
        _timeout_env = os.getenv("GENOMEER_TIMEOUT_SECONDS")
        if _timeout_env is not None:
            try:
                _tv = int(_timeout_env)
                self.timeout_seconds = max(60, min(_tv, 86400))
            except ValueError:
                import logging as _logging
                _logging.getLogger("genomeer.config").error(
                    f"[Config] Invalid GENOMEER_TIMEOUT_SECONDS={_timeout_env!r}; using default {self.timeout_seconds}"
                )
        
        if os.getenv("GENOMEER_USE_TOOL_RETRIEVER"):
            self.use_tool_retriever = os.getenv("GENOMEER_USE_TOOL_RETRIEVER").lower() == "true"
        if os.getenv("GENOMEER_MODEL_TEMPERATURE"):
            self.temperature = float(os.getenv("GENOMEER_MODEL_TEMPERATURE"))
            
        self.base_url = os.getenv("CUSTOM_MODEL_BASE_URL", self.base_url)
        self.api_key  = os.getenv("CUSTOM_MODEL_API_KEY", self.api_key)
        self.source   = os.getenv("GENOMEER_MODEL_SOURCE", self.source)
        if self.source:
            self.source = self.source.strip("'\"")
            # INCONS-01: align with llm.py SourceType (mixed-case).
            # Also accept lowercase aliases and normalise to the canonical casing
            # so that GENOMEER_MODEL_SOURCE=azure works end-to-end.
            _ALIAS_MAP = {
                "openai":     "OpenAI",
                "azureopenai": "AzureOpenAI",
                "azure":      "AzureOpenAI",
                "anthropic":  "Anthropic",
                "ollama":     "Ollama",
                "gemini":     "Gemini",
                "bedrock":    "Bedrock",
                "groq":       "Groq",
                "custom":     "Custom",
            }
            _ALLOWED_SOURCES = set(_ALIAS_MAP.values())
            _normalised = _ALIAS_MAP.get(self.source.lower())
            if _normalised:
                self.source = _normalised        # canonicalise casing in-place
            elif self.source not in _ALLOWED_SOURCES:
                import logging as _logging
                _logging.getLogger("genomeer.config").warning(
                    f"[Config] Unexpected model source: {self.source!r}. "
                    f"Allowed (case-insensitive): {sorted(_ALIAS_MAP.keys())}"
                )

    def __repr__(self) -> str:
        fields = {k: ("***REDACTED***" if "key" in k.lower() or "secret" in k.lower() or "password" in k.lower() else v)
                  for k, v in self.__dict__.items()}
        return f"GenomeerConfig({fields})"

    def to_dict(self) -> dict:
        """Convert config to dictionary for easy access."""
        return asdict(self)
    
settings = GenomeerConfig()