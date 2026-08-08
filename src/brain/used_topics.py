import os

from log import get_logger

_USED_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "workspace", "已用选题.md")
_log = get_logger("used_topics")
_HEADER = "# 已用选题库（排上排期表的选题自动登记，出题时降权避免重复推荐）\n"


def load_used() -> list[str]:
    if not os.path.exists(_USED_PATH):
        return []
    topics, seen = [], set()
    with open(_USED_PATH, "r", encoding="utf-8") as f:
        for line in f:
            t = line.strip()
            if t.startswith("- "):
                t = t[2:].strip()
            if not t or t.startswith("#"):
                continue
            if t not in seen:
                seen.add(t)
                topics.append(t)
    return topics


def add_used(topic: str) -> None:
    topic = (topic or "").strip()
    if not topic or topic in load_used():
        return
    if not os.path.exists(_USED_PATH):
        with open(_USED_PATH, "w", encoding="utf-8") as f:
            f.write(_HEADER)
    with open(_USED_PATH, "a", encoding="utf-8") as f:
        f.write(f"- {topic}\n")
    _log.info("已用选题登记 %s", topic)
