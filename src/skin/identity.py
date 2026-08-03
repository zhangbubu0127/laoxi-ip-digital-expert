import json, os

from skin import bots

_ROLES_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config", "roles.json")

def load_roles() -> dict:
    with open(_ROLES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def resolve_role(open_id: str) -> str:
    roles = load_roles()
    if open_id in roles.get("boss_open_ids", []):
        return "老板"
    if open_id in roles.get("product_open_ids", []):
        return "产品"
    if open_id in roles.get("publisher_open_ids", []):
        return "发片同事"
    bot_role = bots.by_open_id(open_id)
    if bot_role:
        return bot_role
    return "未知"
