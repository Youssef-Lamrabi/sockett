from pydantic import BaseSettings, Field

class Settings(BaseSettings):
    ollama_host: str = Field(default="http://localhost:11434")
    model: str = Field(default="llama3.1:70b")
    tool_timeout_s: int = 600
    work_dir: str = ".genomeer_runs"
    rscript_path: str = "Rscript"   # fallback if no rpy2
    allow_network_tools: bool = False

settings = Settings()
