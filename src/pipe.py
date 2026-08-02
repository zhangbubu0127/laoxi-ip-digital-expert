from dataclasses import dataclass, field

@dataclass
class InboundMessage:
    message_id: str
    chat_id: str
    content: str
    sender_open_id: str
    sender_role: str
    mentions: list = field(default_factory=list)

@dataclass
class OutboundMessage:
    chat_id: str
    text: str
    agent_tag: str
    need_boss: bool = False

def parse_inbound(event: dict) -> InboundMessage:
    sender = event.get("sender") or {}
    return InboundMessage(
        message_id=event.get("id", ""),
        chat_id=event.get("chat_id", ""),
        content=event.get("content", ""),
        sender_open_id=sender.get("id", ""),
        sender_role="未知",
        mentions=[m.get("key", "") for m in event.get("mentions", [])],
    )

def format_outbound(msg: OutboundMessage) -> str:
    prefix = "【" + msg.agent_tag + "】"
    body = msg.text
    if msg.need_boss:
        body = "（请老板确认）" + body
    return prefix + body
