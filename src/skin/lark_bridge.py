import json, os, subprocess, time, glob, traceback

from log import get_logger
from pipe import parse_inbound, format_outbound, OutboundMessage
from skin.identity import resolve_role
from skin.group_history import pull_group_history
from brain.controller import handle_message, handle_bot_output, sweep_pipelines
import brain.controller as _controller
from brain import scheduler as _scheduler
from brain import context_store
from skin import base_bridge, identity, bots

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


def _maybe_send_publish_reminders(last_check: float, profile: str) -> bool:
    """距发布 ≤60 分钟的排期提醒负责人核对（30s 节流；去重由 scheduler 内存集合承担）。返回是否真做了检查，run_loop 只在 True 时重置节流计时。"""
    if time.time() - last_check < 30:
        return False
    try:
        rows = _scheduler.check_upcoming_publish()
    except Exception as e:
        log.warning("发布提醒检查失败: %s", e)
        return False
    for r in rows:
        if not r.get("source_chat"):
            continue
        mins = r.get("mins_left")
        if mins is None or mins >= 60:
            label = "约 1 小时"
        elif mins <= 0:
            label = "即将发布"
        else:
            label = f"{int(round(mins))} 分钟"
        text = (f"⏰ 距发布还有 {label}：{r['date']} {r.get('publish_time', '')}"
                f"《{r['topic']}》待发布，核对是否准备好。")
        oid = identity.resolve_owner_open_id(r.get("owner", ""))
        if oid:
            text += f" <at user_id=\"{oid}\"></at>"
        try:
            send_reply(r["source_chat"], text, profile)
            log.info("发布提醒 %s「%s」→ %s", r["date"], r["topic"], r["source_chat"])
        except Exception as e:
            log.error("发布提醒发送失败: %s", e)
    return True

def _mentioned_xiaoxi(msg) -> bool:
    # 群消息必须先 @ 小席 才响应；拿不到小席 open_id（单bot兜底/配置缺失）时放行不拦截
    try:
        oid = bots.by_role("小席")["open_id"]
    except KeyError:
        log.warning("读取小席 open_id 失败，群消息未@小席也放行")
        return True
    return (oid and oid in msg.mentions) or any(n == "小席" for n in msg.mention_names)

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
    last_remind = 0.0
    try:
        while True:
            if _maybe_send_publish_reminders(last_remind, profile):
                last_remind = time.time()
            for event in poll_events(output_dir):
                try:
                    msg = parse_inbound(event)
                    msg.sender_role = resolve_role(msg.sender_open_id, msg.chat_id)
                    log.info("收到 id=%s role=%s chat=%s content=%r",
                             msg.message_id, msg.sender_role, msg.chat_id, msg.content[:60])
                    if msg.sender_role == "小席":
                        continue
                    if msg.sender_role in _EXPERT_ROLES:
                        for reply in handle_bot_output(msg):
                            if not reply.emitted:
                                send_reply(reply.chat_id, format_outbound(reply), profile)
                    else:
                        if not _mentioned_xiaoxi(msg):
                            log.info("未@小席，忽略 chat=%s content=%r", msg.chat_id, msg.content[:30])
                            continue
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
