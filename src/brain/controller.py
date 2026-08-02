import re
from pipe import InboundMessage, OutboundMessage
from brain.scheduler import mark_published, render_schedule, add_entry
from brain.circuit import CircuitBreaker
from brain.experts.xiaoti import XiaotiExpert
from brain.experts.xiaowen import XiaowenExpert
from brain.experts.xiaone import XiaoneExpert

_b = CircuitBreaker()

def handle_message(msg: InboundMessage) -> list[OutboundMessage]:
    role = msg.sender_role
    content = msg.content
    replies = []
    tripped = _b.record(1_000)

    if tripped:
        replies.append(OutboundMessage(msg.chat_id, "熔断触发，token已到阈值，建议暂停", "主控调度"))
        return replies

    if "已发布" in content:
        if _is_admin(role) or role == "发片同事":
            ok = _try_mark_published(content)
            replies.append(OutboundMessage(msg.chat_id, f"已发状态：{ok}", "主控调度"))
        else:
            replies.append(OutboundMessage(msg.chat_id, "无权限：只有老板、产品或发片同事能确认已发布", "主控调度"))
        return replies

    if not _is_admin(role):
        if _looks_like_command(content):
            replies.append(OutboundMessage(msg.chat_id, "无权限：只有老板或产品能派单或改排期", "主控调度"))
            return replies
        if role == "未知" and "排期表" in content:
            replies.append(OutboundMessage(msg.chat_id, "无权限：当前身份未识别", "主控调度"))
            return replies

    if "选题" in content:
        replies.append(OutboundMessage(msg.chat_id, XiaotiExpert().handle(content), "席小题"))
    elif "脚本" in content:
        replies.append(OutboundMessage(msg.chat_id, XiaowenExpert().handle(content), "席小文"))
        script = "【脚本v1】新加坡留学，老席帮你算笔账"
        replies.append(OutboundMessage(msg.chat_id, XiaoneExpert().handle(script), "席小核"))
    elif "排期表" in content:
        replies.append(OutboundMessage(msg.chat_id, "当前排期表：\n" + render_schedule(), "主控调度"))
    elif "3条曝光" in content:
        for i in range(3):
            add_entry({"date": "下周", "content_type": "曝光", "topic": f"曝光选题{i+1}",
                       "goal": "拉新", "status": "待产", "owner": "席小文", "data": "—"})
        replies.append(OutboundMessage(msg.chat_id, "已排3条曝光：\n" + render_schedule(), "主控调度"))
    else:
        replies.append(OutboundMessage(msg.chat_id, "需要我做什么？出选题/写脚本/看排期/讨论？", "主控调度"))
    return replies

def _is_admin(role: str) -> bool:
    return role == "老板" or role == "产品"

def _looks_like_command(content: str) -> bool:
    return any(k in content for k in ["选题", "脚本", "排期表", "3条曝光"])

def _try_mark_published(content: str) -> str:
    parts = content.replace("已发布", "").strip()
    m = re.match(r"(\d{1,2}/\d{1,2})\s*(.*)", parts)
    if m and m.group(2).strip():
        date, topic = m.group(1), m.group(2).strip()
        return "确认" if mark_published(date, topic) else "未找到对应待发条目"
    return "格式不对，示例：8/3 普娃逆袭 已发布"
