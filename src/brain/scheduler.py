import os

_SCHEDULE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "workspace", "排期表.md")
_HEADER = "| 日期 | 内容类型 | 选题 | 目标 | 状态 | 负责人 | 数据回流 |\n|------|---------|------|------|------|--------|---------|\n"
_FIELDS = ["date", "content_type", "topic", "goal", "status", "owner", "data"]

def load_schedule() -> list[dict]:
    with open(_SCHEDULE_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    rows = []
    for line in lines:
        line = line.strip()
        if not line.startswith("|") or "---" in line or "日期" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == len(_FIELDS):
            rows.append(dict(zip(_FIELDS, cells)))
    return rows

def add_entry(entry: dict) -> None:
    row = "| " + " | ".join(str(entry.get(k, "")) for k in _FIELDS) + " |\n"
    with open(_SCHEDULE_PATH, "a", encoding="utf-8") as f:
        f.write(row)

def mark_published(date: str, topic: str) -> bool:
    rows = load_schedule()
    for r in rows:
        if r["date"] == date and r["topic"] == topic and r["status"] == "待发":
            r["status"] = "已发"
            r["data"] = "待回流"
            _rewrite(rows)
            return True
    return False

def _rewrite(rows: list[dict]) -> None:
    with open(_SCHEDULE_PATH, "w", encoding="utf-8") as f:
        f.write(_HEADER)
        for r in rows:
            f.write("| " + " | ".join(r.get(k, "") for k in _FIELDS) + " |\n")

def render_schedule() -> str:
    return _HEADER + "".join(
        "| " + " | ".join(r.get(k, "") for k in _FIELDS) + " |\n" for r in load_schedule()
    )
