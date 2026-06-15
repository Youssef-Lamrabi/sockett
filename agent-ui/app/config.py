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
    """Optional secondary LLM for bio_hint node.

    Reads bio_hint_model / bio_hint_source / bio_hint_base_url / bio_hint_api_key
    from conf.json. If bio_hint_model is missing/empty, the agent runs without
    the bio_hint graph node (UI behaviour unchanged).
    """
    try:
        with open(CONF_PATH, "r") as f:
            data = json.load(f) or {}
        return {
            "model":   (data.get("bio_hint_model") or None),
            "source":  data.get("bio_hint_source"),
            "base_url": data.get("bio_hint_base_url"),
            "api_key": data.get("bio_hint_api_key"),
        }
    except Exception:
        return {"model": None, "source": None, "base_url": None, "api_key": None}
