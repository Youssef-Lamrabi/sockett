import os
from typing import TYPE_CHECKING, Literal, Optional
from langchain_core.language_models.chat_models import BaseChatModel
if TYPE_CHECKING:
    from genomeer.config import GenomeerConfig


class _SecretStr:
    """Wrapper that prevents API key from appearing in logs or tracebacks."""
    __slots__ = ('_value',)
    def __init__(self, value: str): self._value = value
    def __repr__(self): return '***REDACTED***'
    def __str__(self): return '***REDACTED***'
    def get_secret_value(self) -> str: return self._value

SourceType = Literal["OpenAI", "AzureOpenAI", "Anthropic", "Ollama", "Gemini", "Bedrock", "Groq", "Custom"]
ALLOWED_SOURCES: set[str] = set(SourceType.__args__)

_MODEL_MAX_OUTPUT: dict[str, int] = {
    # BUG-32: "claude-opus-4-7" was a typo; correct name is "claude-opus-4-6"
    "claude-opus-4-6": 8192,
    "claude-sonnet-4-6": 8192,
    "claude-haiku-4-5": 4096,
    "claude-haiku-4-5-20251001": 4096,
    "claude-3-opus-20240229": 4096,
    "claude-3-sonnet-20240229": 4096,
    "claude-3-haiku-20240307": 4096,
    "gpt-4o": 4096,
    "gpt-4-turbo": 4096,
    "gpt-4": 4096,
    "gpt-3.5-turbo": 4096,
}
_DEFAULT_MAX_OUTPUT = 4096

def get_llm(
    model: str | None = None,
    temperature: float | None = None,
    stop_sequences: list[str] | None = None,
    source: SourceType | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    config: Optional["GenomeerConfig"] = None,
) -> BaseChatModel:
    """
    Get a language model instance based on the specified model name and source.
    This function supports models from OpenAI, Azure OpenAI, Anthropic, Ollama, Gemini, Bedrock, and custom model serving.
    Args:
        model (str): The model name to use
        temperature (float): Temperature setting for generation
        stop_sequences (list): Sequences that will stop generation
        source (str): Source provider: "OpenAI", "AzureOpenAI", "Anthropic", "Ollama", "Gemini", "Bedrock", or "Custom"
                      If None, will attempt to auto-detect from model name
        base_url (str): The base URL for custom model serving (e.g., "http://localhost:8000/v1"), default is None
        api_key (str): The API key for the custom llm
        config (GenomeerConfig): Optional configuration object. If provided, unspecified parameters will use config values
    """
    # Use config values for any unspecified parameters
    if config is not None:
        if model is None:
            model = config.llm
        if temperature is None:
            temperature = config.temperature
        if source is None:
            source = config.source
        if base_url is None:
            base_url = config.base_url
        if api_key is None:
            _raw_key = getattr(config, 'api_key', None) or "EMPTY"
            api_key = _SecretStr(_raw_key)

    # Use defaults if still not specified
    if model is None:
        model = "gpt-oss:20b"
    if temperature is None:
        temperature = 0.7
    if api_key is None:
        api_key = _SecretStr("EMPTY")
        
    # BUG-31: validate model name before slicing — an empty string or a name
    # shorter than the prefix being tested gives a misleading ValueError.
    if not model or not model.strip():
        raise ValueError(
            "model name must be a non-empty string. "
            "Set GENOMEER_LLM (env var) or pass model= explicitly."
        )
    model = model.strip()

    # Auto-detect source from model name if not specified
    if source is None:
        env_source = os.getenv("GENOMEER_MODEL_SOURCE")
        if env_source in ALLOWED_SOURCES:
            source = env_source
        else:
            if model[:7] == "claude-":
                source = "Anthropic"
            elif model[:7] == "gpt-oss":
                source = "Ollama"
            elif model[:4] == "gpt-":
                source = "OpenAI"
            elif model.startswith("azure-"):
                source = "AzureOpenAI"
            elif model[:7] == "gemini-":
                source = "Gemini"
            elif "groq" in model.lower():
                source = "Groq"
            elif base_url is not None:
                source = "Custom"
            elif "/" in model or any(
                name in model.lower()
                for name in [
                    "llama",
                    "mistral",
                    "qwen",
                    "gemma",
                    "phi",
                    "dolphin",
                    "orca",
                    "vicuna",
                    "deepseek",
                ]
            ):
                source = "Ollama"
            elif model.startswith(
                ("anthropic.claude-", "amazon.titan-", "meta.llama-", "mistral.", "cohere.", "ai21.", "us.")
            ):
                source = "Bedrock"
            else:
                raise ValueError(
                    f"Unable to determine provider for model {model!r}. "
                    "Pass source='Anthropic'|'OpenAI'|'Ollama'|'AzureOpenAI'|'Gemini'|'Groq'|'Bedrock'|'Custom' "
                    "or set GENOMEER_MODEL_SOURCE env var."
                )

    # INternal helper
    def _mk_openai_like(source, model, temperature, stop_sequences, api_key=None, base_url=None):
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError(  # noqa: B904
                f"langchain-openai package is required for "+source+" models. Install with: pip install langchain-openai"
            )
            
        kwargs = dict(
            model=model,
            temperature=temperature,
            stop=stop_sequences,
            model_kwargs={
                # CRITICAL: don’t let the client infer/parse tool calls
                "tool_choice": "none",
                "response_format": {"type": "text"},
            },
        )
        if api_key is not None:
            kwargs["api_key"] = api_key.get_secret_value() if isinstance(api_key, _SecretStr) else api_key
        if base_url is not None:
            kwargs["base_url"] = base_url
        return ChatOpenAI(**kwargs)

    # Create appropriate model based on source
    if source == "OpenAI":
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY environment variable is required for OpenAI models.")
        return _mk_openai_like("OpenAI", model, temperature, stop_sequences)
    
    elif source == "AzureOpenAI":
        try:
            from langchain_openai import AzureChatOpenAI
        except ImportError:
            raise ImportError(  # noqa: B904
                "langchain-openai package is required for Azure OpenAI models. Install with: pip install langchain-openai"
            )
        if not os.getenv("AZURE_OPENAI_API_KEY") and not os.getenv("OPENAI_API_KEY"):
            raise ValueError("AZURE_OPENAI_API_KEY (or OPENAI_API_KEY) is required for AzureOpenAI models.")
        if not os.getenv("AZURE_OPENAI_ENDPOINT"):
            raise ValueError("AZURE_OPENAI_ENDPOINT is required for AzureOpenAI models.")
            
        API_VERSION = "2024-12-01-preview"
        model = model.replace("azure-", "")
        return AzureChatOpenAI(
            openai_api_key=os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"),
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            azure_deployment=model,
            openai_api_version=API_VERSION,
            temperature=temperature,
        )

    elif source == "Anthropic":
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ImportError(  # noqa: B904
                "langchain-anthropic package is required for Anthropic models. Install with: pip install langchain-anthropic"
            )
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise ValueError("ANTHROPIC_API_KEY environment variable is required for Anthropic models.")
        llm = ChatAnthropic(
            model=model,
            temperature=temperature,
            max_tokens=_MODEL_MAX_OUTPUT.get(model, _DEFAULT_MAX_OUTPUT),
            stop_sequences=stop_sequences,
        )
        try:
            llm = llm.with_retry(stop_after_attempt=3)
        except AttributeError:
            pass  # older langchain version without with_retry
        return llm

    elif source == "Gemini":
        if not os.getenv("GEMINI_API_KEY"):
            raise ValueError("GEMINI_API_KEY environment variable is required for Gemini models.")
        return _mk_openai_like(
            "Gemini",
            model, temperature, stop_sequences,
            api_key=os.getenv("GEMINI_API_KEY"),
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )

    elif source == "Groq":
        if not os.getenv("GROQ_API_KEY"):
            raise ValueError("GROQ_API_KEY environment variable is required for Groq models.")
        return _mk_openai_like(
            "Groq",
            model, temperature, stop_sequences,
            api_key=os.getenv("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1",
        )

    elif source == "Ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError:
            raise ImportError(  # noqa: B904
                "langchain-ollama package is required for Ollama models. Install with: pip install langchain-ollama"
            )
        # BUG-33: pass stop sequences so structured output delimiters are respected
        ollama_kwargs: dict = dict(model=model, temperature=temperature)
        if stop_sequences:
            ollama_kwargs["stop"] = stop_sequences
        return ChatOllama(**ollama_kwargs)

    elif source == "Bedrock":
        try:
            from langchain_aws import ChatBedrock
        except ImportError:
            raise ImportError(  # noqa: B904
                "langchain-aws package is required for Bedrock models. Install with: pip install langchain-aws"
            )
        return ChatBedrock(
            model=model,
            temperature=temperature,
            stop_sequences=stop_sequences,
            region_name=os.getenv("AWS_REGION", "us-east-1"),
        )

    elif source == "Custom":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError(  # noqa: B904
                "langchain-openai package is required for custom models. Install with: pip install langchain-openai"
            )
        # Custom LLM serving such as SGLang or Ollama. Must expose an openai compatible API
        assert base_url is not None, "base_url must be provided for customly served LLMs"
        llm = ChatOpenAI(
            model=model,
            temperature=temperature,
            max_tokens=_MODEL_MAX_OUTPUT.get(model, _DEFAULT_MAX_OUTPUT),
            stop=stop_sequences,
            base_url=base_url,
            api_key=api_key.get_secret_value() if isinstance(api_key, _SecretStr) else api_key,
            # Fixed: To avoid automatic tools call by ChatOpenAI->we want it as plain text in <execute></execute>
            model_kwargs={
                "tool_choice": "none",
                "response_format": {"type": "text"},
            },
        )
        try:
            llm = llm.with_retry(stop_after_attempt=3)
        except AttributeError:
            pass  # older langchain version without with_retry
        return llm

    else:
        raise ValueError(
            f"Invalid source: {source}. Valid options are 'OpenAI', 'AzureOpenAI', 'Anthropic', 'Gemini', 'Groq', 'Bedrock', 'Ollama', or 'Custom'"
        )