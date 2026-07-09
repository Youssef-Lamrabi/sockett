import os
from typing import TYPE_CHECKING, Literal, Optional
from langchain_core.language_models.chat_models import BaseChatModel
if TYPE_CHECKING:
    from genomeer.config import GenomeerConfig

SourceType = Literal["OpenAI", "AzureOpenAI", "Anthropic", "Ollama", "Gemini", "Bedrock", "Groq", "DeepSeek", "Custom"]
ALLOWED_SOURCES: set[str] = set(SourceType.__args__)

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
            api_key = config.api_key or "EMPTY"

    # Use defaults if still not specified
    if model is None:
        model = "gpt-oss:20b"
    if temperature is None:
        temperature = 0.7
    if api_key is None:
        api_key = "EMPTY"
        
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
            elif base_url is None and "deepseek" in model.lower():
                # DeepSeek cloud API (OpenAI-compatible). Only when NO base_url is given —
                # a user-supplied base_url means they point at their own/proxy endpoint,
                # which must fall through to Custom below and be honored as-is.
                source = "DeepSeek"
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
                ]
            ):
                source = "Ollama"
            elif model.startswith(
                ("anthropic.claude-", "amazon.titan-", "meta.llama-", "mistral.", "cohere.", "ai21.", "us.")
            ):
                source = "Bedrock"
            else:
                raise ValueError("Unable to determine model source. Please specify 'source' parameter.")

    # INternal helper
    def _mk_openai_like(source, model, temperature, stop_sequences, api_key=None, base_url=None):
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError(  # noqa: B904
                f"langchain-openai package is required for "+source+" models. Install with: pip install langchain-openai"
            )
        # Hard timeout guardrail — prevents silent hangs when endpoint is slow or
        # the API key is invalid. Connect fails fast (10s); reads allow 300s for
        # generation. max_retries=1 so a real error surfaces instead of looping.
        try:
            import httpx
            _timeout = httpx.Timeout(connect=10, read=300, write=30, pool=10)
        except ImportError:
            _timeout = None
        kwargs = dict(
            model=model,
            temperature=temperature,
            stop=stop_sequences,
            max_retries=1,
            model_kwargs={
                # CRITICAL: don’t let the client infer/parse tool calls
                "tool_choice": "none",
                "response_format": {"type": "text"},
            },
        )
        if _timeout is not None:
            kwargs["timeout"] = _timeout
        if api_key is not None:
            kwargs["api_key"] = api_key
        if base_url is not None:
            kwargs["base_url"] = base_url
        return ChatOpenAI(**kwargs)

    # Create appropriate model based on source
    if source == "OpenAI":
        return _mk_openai_like("OpenAI", model, temperature, stop_sequences)
    
    elif source == "AzureOpenAI":
        try:
            from langchain_openai import AzureChatOpenAI
        except ImportError:
            raise ImportError(  # noqa: B904
                "langchain-openai package is required for Azure OpenAI models. Install with: pip install langchain-openai"
            )
        API_VERSION = "2024-12-01-preview"
        model = model.replace("azure-", "")
        return AzureChatOpenAI(
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            azure_endpoint=os.getenv("OPENAI_ENDPOINT"),
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
        try:
            import httpx
            _anth_timeout = httpx.Timeout(connect=10, read=300, write=30, pool=10)
        except ImportError:
            _anth_timeout = None
        _anth_kwargs = dict(
            model=model,
            temperature=temperature,
            max_tokens=8192,
            stop_sequences=stop_sequences,
            max_retries=1,
        )
        if _anth_timeout is not None:
            _anth_kwargs["timeout"] = _anth_timeout
        return ChatAnthropic(**_anth_kwargs)

    elif source == "Gemini":
        return _mk_openai_like(
            "Gemini",
            model, temperature, stop_sequences,
            api_key=os.getenv("GEMINI_API_KEY"),
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )

    elif source == "Groq":
        return _mk_openai_like(
            "Groq",
            model, temperature, stop_sequences,
            api_key=os.getenv("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1",
        )

    elif source == "DeepSeek":
        # DeepSeek cloud is OpenAI-compatible. Honor a caller-provided base_url (proxy /
        # self-host) but default to the official endpoint; key from param or DEEPSEEK_API_KEY.
        _ds_key = api_key if (api_key and api_key != "EMPTY") else os.getenv("DEEPSEEK_API_KEY")
        return _mk_openai_like(
            "DeepSeek",
            model, temperature, stop_sequences,
            api_key=_ds_key,
            base_url=base_url or "https://api.deepseek.com/v1",
        )

    elif source == "Ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError:
            raise ImportError(  # noqa: B904
                "langchain-ollama package is required for Ollama models. Install with: pip install langchain-ollama"
            )
        # Honor a user-provided base_url so a REMOTE/exposed Ollama works (previously
        # base_url was ignored → every Ollama model silently hit localhost:11434).
        _ollama_kwargs = {"model": model, "temperature": temperature}
        if base_url:
            _ollama_kwargs["base_url"] = base_url
        return ChatOllama(**_ollama_kwargs)

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
        try:
            import httpx
            _custom_timeout = httpx.Timeout(connect=10, read=300, write=30, pool=10)
        except ImportError:
            _custom_timeout = None
        _custom_kwargs = dict(
            model=model,
            temperature=temperature,
            max_tokens=8192,
            stop=stop_sequences,
            base_url=base_url,
            api_key=api_key,
            max_retries=1,
            # Fixed: To avoid automatic tools call by ChatOpenAI->we want it as plain text in <execute></execute>
            model_kwargs={
                "tool_choice": "none",
                "response_format": {"type": "text"},
            },
        )
        if _custom_timeout is not None:
            _custom_kwargs["timeout"] = _custom_timeout
        llm = ChatOpenAI(**_custom_kwargs)
        return llm

    else:
        raise ValueError(
            f"Invalid source: {source}. Valid options are 'OpenAI', 'AzureOpenAI', 'Anthropic', 'Gemini', 'Groq', 'DeepSeek', 'Bedrock', 'Custom', or 'Ollama'"
        )