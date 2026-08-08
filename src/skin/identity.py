import json, os

from skin import bots

_ROLES_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config", "roles.json")

def load_roles() -> dict:
    with open(_ROLES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def resolve_role(open_id: str, chat_id: str = "") -> str:
    roles = load_roles()
    if open_id in roles.get("boss_open_ids", []):
        return "老板"
    if open_id in roles.get("product_open_ids", []):
        return "产品"
    if open_id in roles.get("publisher_open_ids", []):
        return "发片同事"
    if open_id in roles.get("user_open_ids", []):
        return "用户"
    bot_role = bots.by_open_id(open_id)
    if bot_role:
        return bot_role
    # 外部群按群整体授权：chat_id 在白名单 → 该群成员按配置角色对待（等同内部群）
    group_role = roles.get("trusted_groups", {}).get(chat_id, "")
    if group_role:
        return group_role
    return "未知"

def resolve_owner_open_id(name: str) -> str:
    # 排期表负责人姓名 → open_id（发布提醒 @ 对应真人）；「发片同事」泛指回落第一个发片同事
    roles = load_roles()
    oid = roles.get("owner_ids", {}).get(name, "")
    if oid:
        return oid
    if name == "发片同事":
        pubs = roles.get("publisher_open_ids", [])
        return pubs[0] if pubs else ""
    return ""
