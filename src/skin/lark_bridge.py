import json, os, subprocess, time, glob, traceback

from log import get_logger
from pipe import parse_inbound, format_outbound, OutboundMessage
from skin.identity import resolve_role
from skin.group_history import pull_group_history
from brain.controller import handle_message, handle_bot_output, sweep_pipelines
import brain.controller as _controller
from brain import scheduler as _scheduler
from brain import context_store
from skin import base_bridge

log = get_logger("lark_bridge", console=True)

_EXPERT_ROLES = ("席小题", "席小文", "席小核", "席小习", "席小盘")

# 已读表情 emoji_type（飞书原生「奋斗」表情，已实测有效；可换 THUMBSUP/OK/HEART 等）
_READ_EMOJI = "STRIVE"

def add_reaction(chat_id: str, message_id: str, emoji: str = _READ_EMOJI, profile: str = None) -> None:
    if not message_id:
        return
    cmd = ["lark-cli", "im", "reactions", "create",
           "--message-id", message_id,
           "--data", json.dumps({"reaction_type": {"emoji_type": emoji}})]
    if profile:
        cmd += ["--profile", profile]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        detail = e.stderr.decode("utf-8", "ignore") if e.stderr else str(e)
        log.warning("已读表情添加失败 %s: %s", message_id, detail)

def send_reply(chat_id: str, text: str, profile: str = None) -> None:
    if not text:
        raise ValueError("回执文本为空，拒绝发送")
    cmd = ["lark-cli", "im", "+messages-send", "--chat-id", chat_id, "--text", text]
    if profile:
        cmd += ["--profile", profile]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        detail = e.stderr.decode("utf-8", "ignore") if e.stderr else str(e)
        raise RuntimeError("lark-cli 发送失败: " + detail) from e

def start_listener(output_dir: str, profile: str = None) -> subprocess.Popen:
    cmd = ["lark-cli", "event", "consume", "im.message.receive_v1", "--output-dir", output_dir]
    if profile:
        cmd += ["--profile", profile]
    return subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

def poll_events(output_dir: str) -> list[dict]:
    events = []
    for path in sorted(glob.glob(os.path.join(output_dir, "*.json"))):
        with open(path, "r", encoding="utf-8") as f:
            events.append(json.load(f))
        os.unlink(path)
    return events

def run_loop(output_dir: str, poll_interval: float = 2.0, profile: str = None) -> None:
    os.makedirs(output_dir, exist_ok=True)
    listener = start_listener(output_dir, profile)
    _controller._pull_history = pull_group_history
    _controller._emit = lambda chat_id, text, tag: send_reply(chat_id, format_outbound(OutboundMessage(chat_id, text, tag)), profile)
    _scheduler.set_base_sync(base_bridge.sync_rows)
    _controller.set_base_url(base_bridge.base_url())
    log.info("run_loop 启动，output_dir=%s profile=%s", output_dir, profile or "(默认)")
    try:
        while True:
            for event in poll_events(output_dir):
                try:
                    msg = parse_inbound(event)
                    msg.sender_role = resolve_role(msg.sender_open_id)
                    log.info("收到 id=%s role=%s chat=%s content=%r",
                             msg.message_id, msg.sender_role, msg.chat_id, msg.content[:60])
                    if msg.sender_role == "小席":
                        continue
                    if msg.sender_role in _EXPERT_ROLES:
                        for reply in handle_bot_output(msg):
                            if not reply.emitted:
                                send_reply(reply.chat_id, format_outbound(reply), profile)
                    else:
                        add_reaction(msg.chat_id, msg.message_id, profile=profile)
                        for reply in handle_message(msg):
                            if not reply.emitted:
                                send_reply(reply.chat_id, format_outbound(reply), profile)
                            log.info("回复 %s: %r", reply.agent_tag, reply.text[:60])
                except Exception as e:
                    log.error("处理异常: %s\n%s", e, traceback.format_exc())
                    try:
                        send_reply(event.get("chat_id", ""), "【小席】出错了：" + str(e), profile)
                    except Exception:
                        log.error("错误回复也失败: %s", e)
            for reply in sweep_pipelines():
                if not reply.emitted:
                    send_reply(reply.chat_id, format_outbound(reply), profile)
            context_store.sweep(600)
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        log.info("收到中断，停止监听")
        listener.terminate()

if __name__ == "__main__":
    import sys
    run_loop(sys.argv[1] if len(sys.argv) > 1 else "events")
