import os

from log import get_logger

_RULES_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "memory", "学习规则.md")
_log = get_logger("rules")
_HEADER = "| id | 来源改动 | 提炼规则 | level | 状态 | 确认时间 |\n|------|---------|------|------|------|--------|\n"
_FIELDS = ["id", "change", "rule", "level", "status", "time"]

def load_rules() -> list[dict]:
    try:
        with open(_RULES_PATH, "r", encoding="utf-8") as f:
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
    return rows

def add_rule(change: str, rule: str, level: str = "L1") -> str:
    rows = load_rules()
    rid = f"rule_{len(rows) + 1:04d}"
    rows.append({
        "id": rid, "change": _clean(change), "rule": _clean(rule),
        "level": level, "status": "待确认", "time": _today(),
    })
    _rewrite(rows)
    _log.info("规则待确认 %s: %s", rid, rule[:40])
    return rid

def add_confirmed_rule(change: str, rule: str, level: str = "L1") -> str:
    # 老板答复审核疑问给的确定答案：本身就是确认动作，直接落「已确认」，席小核/席小文下次产出生效
    rows = load_rules()
    rid = f"rule_{len(rows) + 1:04d}"
    rows.append({
        "id": rid, "change": _clean(change), "rule": _clean(rule),
        "level": level, "status": "已确认", "time": _today(),
    })
    _rewrite(rows)
    _log.info("规则已确认 %s: %s", rid, rule[:40])
    return rid

def confirm_pending(confirm: bool = True) -> int:
    rows = load_rules()
    n = 0
    for r in rows:
        if r["status"] == "待确认":
            r["status"] = "已确认" if confirm else "被否"
            r["time"] = _today()
            n += 1
    if n:
        _rewrite(rows)
        _log.info("规则 %s %d 条", "已确认" if confirm else "被否", n)
    return n

def has_pending() -> bool:
    return any(r["status"] == "待确认" for r in load_rules())

def confirmed_rules() -> list[dict]:
    return [r for r in load_rules() if r["status"] == "已确认"]

def render_rules(rules: list[dict] = None) -> str:
    rules = confirmed_rules() if rules is None else rules
    if not rules:
        return "（暂无已确认规则）"
    return "\n".join(f"- {r['rule']}（来源：{r['change']}）" for r in rules)

def _clean(text: str) -> str:
    return " ".join(text.replace("|", "／").split())

def _today() -> str:
    import time
    return time.strftime("%Y-%m-%d")

def _rewrite(rows: list[dict]) -> None:
    with open(_RULES_PATH, "w", encoding="utf-8") as f:
        f.write(_HEADER)
        for r in rows:
            f.write("| " + " | ".join(r.get(k, "") for k in _FIELDS) + " |\n")
