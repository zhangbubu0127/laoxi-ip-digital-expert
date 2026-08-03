from dataclasses import dataclass, field

@dataclass
class InboundMessage:
    message_id: str
    chat_id: str
    content: str
    sender_open_id: str
    sender_role: str
    mentions: list = field(default_factory=list)
    mention_names: list = field(default_factory=list)
    sender_type: str = ""

@dataclass
class OutboundMessage:
    chat_id: str
    text: str
    agent_tag: str
    need_boss: bool = False
    emitted: bool = False

def parse_inbound(event: dict) -> InboundMessage:
    sender = event.get("sender") or {}
    mentions = []
    mention_names = []
    for m in event.get("mentions", []):
        mentions.append(m.get("id") or m.get("key") or "")
        mention_names.append(m.get("name") or "")
    return InboundMessage(
        message_id=event.get("id") or event.get("message_id", ""),
        chat_id=event.get("chat_id", ""),
        content=event.get("content", ""),
        sender_open_id=sender.get("id", "") or event.get("sender_id", ""),
        sender_role="未知",
        mentions=mentions,
        mention_names=mention_names,
        sender_type=sender.get("type", "") or event.get("sender_type", ""),
    )

def format_outbound(msg: OutboundMessage) -> str:
    body = msg.text
    if msg.need_boss:
        body = "（请老板确认）" + body
    return body
