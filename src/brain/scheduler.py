import os, re, json, datetime

from log import get_logger

_SCHEDULE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "workspace", "排期表.md")
_REMINDED_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "workspace", "已提醒发布.json")
_log = get_logger("scheduler")
_HEADER = "| 日期 | 发布时间 | 内容类型 | 选题 | 目标 | 状态 | 负责人 | 数据回流 | 脚本内容 | 来源群 |\n|------|---------|---------|------|------|------|--------|---------|---------|--------|\n"
# source_chat 仅本地记录（供发布提醒定位群），不同步多维表格
_FIELDS = ["date", "publish_time", "content_type", "topic", "goal", "status", "owner", "data", "script", "source_chat"]
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
        if not line.startswith("|"):
            continue
        first = line.strip("|").split("|", 1)[0].strip()
        if first == "日期" or first.startswith("-"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) >= 8:
            # 兼容旧 8 列行：补齐新增列（脚本内容/来源群）为空串
            cells = (cells + [""] * len(_FIELDS))[:len(_FIELDS)]
            rows.append(dict(zip(_FIELDS, cells)))
    return rows

def add_entry(entry: dict) -> None:
    entry.setdefault("publish_time", "12:00")
    # 脚本内容压成单行，避免换行/竖线撑破 markdown 表格；多维表格侧同形
    entry["script"] = _flatten(entry.get("script", ""))
    row = "| " + " | ".join(str(entry.get(k, "")) for k in _FIELDS) + " |\n"
    with open(_SCHEDULE_PATH, "a", encoding="utf-8") as f:
        f.write(row)
    _sync(load_schedule())
    _log.info("排期新增 %s %s「%s」状态=%s",
              entry.get("date"), entry.get("content_type"), entry.get("topic"), entry.get("status"))

def mark_published(date: str, topic: str) -> bool:
    rows = load_schedule()
    for r in rows:
        if r["date"] == date and r["topic"] == topic and r["status"] in ("待产", "待发"):
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


def _flatten(text: str) -> str:
    return re.sub(r"\s*\|\s*", " ", (text or "")).replace("\n", " ").replace("\r", " ").strip()


def _today() -> str:
    return f"{datetime.date.today().month}/{datetime.date.today().day}"

def _load_reminded() -> set:
    # 只保留今天的 key：昨天的提醒记录跨天后无意义，顺带让文件瘦身
    today = _today()
    try:
        with open(_REMINDED_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return set()
    return {tuple(k) for k in data if len(k) == 3 and str(k[0]) == today}

def _save_reminded(reminded: set) -> None:
    try:
        with open(_REMINDED_PATH, "w", encoding="utf-8") as f:
            json.dump([[d, t, pt] for d, t, pt in sorted(reminded)], f, ensure_ascii=False)
    except OSError as e:
        _log.warning("保存已提醒发布失败: %s", e)

_REMINDED = _load_reminded()  # (date, topic, publish_time)：已提醒过的发布，落盘持久化，进程重启不重发
_LAST_REMINDED = {}  # source_chat → (date, topic, publish_time)：最近一条发布提醒，供「已确认排期」停止提醒

def check_upcoming_publish(now: "datetime.datetime" = None) -> list[dict]:
    """距发布 ≤60 分钟的待产/待发行（需有 source_chat 才知道发到哪个群）。同一条只提醒一次。"""
    now = now or datetime.datetime.now()
    today = f"{now.month}/{now.day}"
    out = []
    for r in load_schedule():
        if not (r.get("source_chat") and r["date"] == today and r.get("publish_time")):
            continue
        if r.get("status") not in ("待产", "待发"):
            continue
        try:
            hh, mm = map(int, r["publish_time"].split(":"))
        except (ValueError, AttributeError):
            continue
        t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        mins = (t - now).total_seconds() / 60
        key = (r["date"], r["topic"], r["publish_time"])
        if 0 <= mins <= 60 and key not in _REMINDED:
            _REMINDED.add(key)
            _save_reminded(_REMINDED)
            _LAST_REMINDED[r["source_chat"]] = (r["date"], r["topic"], r["publish_time"])
            r["mins_left"] = mins
            out.append(r)
    return out

def confirm_publish(chat_id: str) -> bool:
    """群内人工回「已确认排期」→ 停止该条发布提醒（写入已提醒集合落盘，跨重启不重发）。"""
    key = _LAST_REMINDED.get(chat_id)
    if not key:
        return False
    _REMINDED.add(key)
    _save_reminded(_REMINDED)
    _log.info("已确认排期，停止提醒 chat=%s %s", chat_id, key)
    return True
