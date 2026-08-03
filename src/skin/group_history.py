import json, subprocess
from log import get_logger

log = get_logger("group_history")

def pull_group_history(chat_id: str, max_count: int = 15) -> str:
    try:
        out = subprocess.run(
            ["lark-cli", "im", "+chat-messages-list",
             "--chat-id", chat_id,
             "--as", "bot",
             "--order", "desc",
             "--page-size", str(max_count),
             "--format", "json",
             "--no-reactions"],
            check=True, capture_output=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        log.error("拉群历史失败: %s", e)
        return ""
    try:
        data = json.loads(out.stdout.decode("utf-8"))
        msgs = data["data"]["messages"]
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        log.error("拉群历史解析失败: %s", e)
        return ""
    lines = []
    for m in reversed(msgs):
        if m.get("msg_type") != "text":
            continue
        sender = m.get("sender") or {}
        name = sender.get("name") or "群成员"
        text = (m.get("content") or "").replace("\n", " ").strip()
        if len(text) > 150:
            text = text[:150] + "…"
        if text:
            lines.append(f"【{name}】{text}")
    return "\n".join(lines)
