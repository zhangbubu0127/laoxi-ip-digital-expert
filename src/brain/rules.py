import os

from log import get_logger

_RULES_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "memory", "学习规则.md")
_log = get_logger("rules")
# 规则类型：帮下游专家校准每条的「份量」——禁止事项最硬、事实规则要核对、内容策略/表达偏好是方向不是绝对真理
_RULES_TYPES = ("表达偏好", "事实规则", "内容策略", "禁止事项")
_DEFAULT_TYPE = "内容策略"
_TYPE_ORDER = ("禁止事项", "事实规则", "内容策略", "表达偏好")
_HEADER = "| id | 来源改动 | 提炼规则 | type | level | 状态 | 确认时间 |\n|------|---------|------|------|------|------|--------|\n"
_FIELDS = ["id", "change", "rule", "type", "level", "status", "time"]
_LEGACY_FIELDS = ["id", "change", "rule", "level", "status", "time"]  # 旧 6 列（无 type）

def _rules_path(style_user: str = "") -> str:
    # 学习规则按风格用户分库：老席（缺省）沿用 memory/学习规则.md，其余 memory/学习规则-<用户>.md
    user = (style_user or "").strip()
    if not user or user == "老席":
        return _RULES_PATH
    return os.path.join(os.path.dirname(_RULES_PATH), f"学习规则-{user}.md")

def load_rules(style_user: str = "") -> list[dict]:
    path = _rules_path(style_user)
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        line = line.strip()
        if not line.startswith("|") or "---" in line or "id" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == len(_FIELDS):
            rows.append(dict(zip(_FIELDS, cells)))
        elif len(cells) == len(_LEGACY_FIELDS):  # 旧 6 列（无 type）文件兼容，回填默认类型
            row = dict(zip(_LEGACY_FIELDS, cells))
            row["type"] = _DEFAULT_TYPE
            rows.append(row)
    return rows

def add_rule(change: str, rule: str, level: str = "L1", style_user: str = "", rtype: str = "") -> str:
    rows = load_rules(style_user)
    rid = f"rule_{len(rows) + 1:04d}"
    rows.append({
        "id": rid, "change": _clean(change), "rule": _clean(rule),
        "type": rtype if rtype in _RULES_TYPES else _DEFAULT_TYPE,
        "level": level, "status": "待确认", "time": _today(),
    })
    _rewrite(rows, style_user)
    _log.info("规则待确认 %s [%s]: %s", rid, style_user or "老席", rule[:40])
    return rid

def add_confirmed_rule(change: str, rule: str, level: str = "L1", style_user: str = "", rtype: str = "") -> str:
    # 老板答复审核疑问给的确定答案：本身就是确认动作，直接落「已确认」，席小核/席小文下次产出生效
    rows = load_rules(style_user)
    rid = f"rule_{len(rows) + 1:04d}"
    rows.append({
        "id": rid, "change": _clean(change), "rule": _clean(rule),
        "type": rtype if rtype in _RULES_TYPES else _DEFAULT_TYPE,
        "level": level, "status": "已确认", "time": _today(),
    })
    _rewrite(rows, style_user)
    _log.info("规则已确认 %s [%s]: %s", rid, style_user or "老席", rule[:40])
    return rid

def confirm_pending(confirm: bool = True, style_user: str = "") -> int:
    rows = load_rules(style_user)
    n = 0
    for r in rows:
        if r["status"] == "待确认":
            r["status"] = "已确认" if confirm else "被否"
            r["time"] = _today()
            n += 1
    if n:
        _rewrite(rows, style_user)
        _log.info("规则 %s %d 条 [%s]", "已确认" if confirm else "被否", n, style_user or "老席")
    return n

def has_pending(style_user: str = "") -> bool:
    return any(r["status"] == "待确认" for r in load_rules(style_user))

def confirmed_rules(style_user: str = "") -> list[dict]:
    return [r for r in load_rules(style_user) if r["status"] == "已确认"]

def render_rules(rules: list[dict] = None, style_user: str = "") -> str:
    rules = confirmed_rules(style_user) if rules is None else rules
    if not rules:
        return "（暂无已确认规则）"
    groups = {}
    for r in rules:
        t = r.get("type") or _DEFAULT_TYPE
        groups.setdefault(t, []).append(r)
    lines = []
    for t in _TYPE_ORDER:
        if t in groups:
            lines.append(f"【{t}】")
            lines.extend(f"- {r['rule']}（来源：{r['change']}）" for r in groups[t])
    return "\n".join(lines)

def _clean(text: str) -> str:
    return " ".join(text.replace("|", "／").split())

def _today() -> str:
    import time
    return time.strftime("%Y-%m-%d")

def _rewrite(rows: list[dict], style_user: str = "") -> None:
    with open(_rules_path(style_user), "w", encoding="utf-8") as f:
        f.write(_HEADER)
        for r in rows:
            f.write("| " + " | ".join(r.get(k, "") for k in _FIELDS) + " |\n")
