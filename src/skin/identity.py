import json, os

_ROLES_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config", "roles.json")

def load_roles() -> dict:
    with open(_ROLES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def resolve_role(open_id: str) -> str:
    roles = load_roles()
    if open_id and open_id == roles.get("boss_open_id"):
        return "老板"
    if open_id in roles.get("publisher_open_ids", []):
        return "发片同事"
    return "未知"
