import os

from log import get_logger

_SCHEDULE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "workspace", "排期表.md")
_log = get_logger("scheduler")
_HEADER = "| 日期 | 发布时间 | 内容类型 | 选题 | 目标 | 状态 | 负责人 | 数据回流 |\n|------|---------|---------|------|------|------|--------|---------|\n"
_FIELDS = ["date", "publish_time", "content_type", "topic", "goal", "status", "owner", "data"]
_base_sync = None  # 皮肤层注入的多维表格同步回调；大脑层不直接调 lark-cli


def set_base_sync(cb) -> None:
    global _base_sync
    _base_sync = cb


def _sync(rows: list) -> None:
    if _base_sync is None:
        return
    try:
        _base_sync(rows)
    except Exception as e:
        _log.warning("排期表同步多维表格失败: %s", e)

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
    entry.setdefault("publish_time", "12:00")
    row = "| " + " | ".join(str(entry.get(k, "")) for k in _FIELDS) + " |\n"
    with open(_SCHEDULE_PATH, "a", encoding="utf-8") as f:
        f.write(row)
    _sync(load_schedule())
    _log.info("排期新增 %s %s「%s」状态=%s",
              entry.get("date"), entry.get("content_type"), entry.get("topic"), entry.get("status"))

def mark_published(date: str, topic: str) -> bool:
    rows = load_schedule()
    for r in rows:
        if r["date"] == date and r["topic"] == topic and r["status"] == "待发":
            r["status"] = "已发"
            r["data"] = "待回流"
            _rewrite(rows)
            _sync(rows)
            _log.info("已发布确认 %s「%s」", date, topic)
            return True
    _log.warning("已发布未找到待发条目 %s「%s」", date, topic)
    return False

def record_data(date: str, topic: str, data_str: str) -> bool:
    rows = load_schedule()
    for r in rows:
        if r["date"] == date and r["topic"] == topic and r["status"] in ("已发", "回流完成"):
            r["data"] = data_str
            r["status"] = "回流完成"
            _rewrite(rows)
            _sync(rows)
            _log.info("数据回流 %s「%s」 data=%s", date, topic, data_str)
            return True
    _log.warning("数据回流未找到已发条目 %s「%s」", date, topic)
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
