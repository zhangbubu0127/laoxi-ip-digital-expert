import os, time

from log import get_logger

_REFLOW_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "memory", "数据回流.md")
_CONCLUSION_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "memory", "验证结论.md")
_log = get_logger("reflow")

def append_reflow(date: str, topic: str, data: str) -> None:
    _append(_REFLOW_PATH, f"- {_ts()} | {date} | {topic} | {data}\n")
    _log.info("数据回流入库 %s「%s」", date, topic)

def load_conclusions() -> str:
    try:
        with open(_CONCLUSION_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""

def append_conclusion(text: str) -> None:
    _append(_CONCLUSION_PATH, f"- {_ts()} | {text}\n")
    _log.info("验证结论入库 %s", text[:40])

def _append(path: str, line: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)

def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M")
