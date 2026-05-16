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
