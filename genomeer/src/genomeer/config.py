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
        self.timeout_seconds = int(os.getenv("GENOMEER_TIMEOUT_SECONDS", self.timeout_seconds))
        
        if os.getenv("GENOMEER_USE_TOOL_RETRIEVER"):
            self.use_tool_retriever = os.getenv("GENOMEER_USE_TOOL_RETRIEVER").lower() == "true"
        if os.getenv("GENOMEER_MODEL_TEMPERATURE"):
            self.temperature = float(os.getenv("GENOMEER_MODEL_TEMPERATURE"))
            
        self.base_url = os.getenv("CUSTOM_MODEL_BASE_URL", self.base_url)
        self.api_key  = os.getenv("CUSTOM_MODEL_API_KEY", self.api_key)
        self.source   = os.getenv("GENOMEER_MODEL_SOURCE", self.source)
        if self.source:
            self.source = self.source.strip("'\"") # BUG-38: handle quotes in .env

    def to_dict(self) -> dict:
        """Convert config to dictionary for easy access."""
        return asdict(self)
    
settings = GenomeerConfig()