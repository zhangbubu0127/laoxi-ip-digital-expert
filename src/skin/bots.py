import json, os

_BOTS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config", "bots.json")

def load_bots() -> dict:
    try:
        with open(_BOTS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return {}

def by_role(role: str) -> dict:
    entry = load_bots().get(role)
    if not entry or not entry.get("open_id"):
        raise KeyError(f"config/bots.json 未配置角色「{role}」的 open_id")
    return {"profile": entry.get("profile") or role, "open_id": entry["open_id"]}

def by_open_id(open_id: str):
    if not open_id:
        return None
    for role, entry in load_bots().items():
        if entry.get("open_id") == open_id:
            return role
    return None
