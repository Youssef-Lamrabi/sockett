import json, os

CONF_PATH = os.getenv("AGENT_COPILOT_CONF", os.path.abspath("./conf.json"))

_EMPTY = {
    "model": None,
    "source": None,
    "base_url": None,
    "api_key": None,
}

def system_default() -> dict:
    try:
        with open(CONF_PATH, "r") as f:
            data = json.load(f) or {}
        out = _EMPTY.copy()
        out["model"] = data.get("default_model")
        out["source"] = data.get("default_source")
        out["base_url"] = data.get("default_base_url")
        out["api_key"] = data.get("default_api_key")
        return out
    except Exception:
        return _EMPTY


def bio_hint_default() -> dict:
    """Optional secondary LLM used for bio_hint (domain hints).

    Enabled only when `bio_hint_model` is set in conf.json. Source/base_url/api_key
    fall back to the main provider when omitted (common case: same OpenRouter key).
    Returns {"model": None, ...} when bio_hint is not configured (= disabled).
    """
    try:
        with open(CONF_PATH, "r") as f:
            data = json.load(f) or {}
        out = _EMPTY.copy()
        out["model"] = data.get("bio_hint_model")
        out["source"] = data.get("bio_hint_source")
        out["base_url"] = data.get("bio_hint_base_url")
        out["api_key"] = data.get("bio_hint_api_key")
        return out
    except Exception:
        return _EMPTY
