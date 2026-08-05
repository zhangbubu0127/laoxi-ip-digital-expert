import re, sys, time, traceback

from log import get_logger
from pipe import parse_inbound
from skin import lark_bridge
from skin.bots import by_role
from brain.experts.dispatch import run_expert
from brain.experts.xiaone import split_review
from brain import context_store

log = get_logger("multi_main", console=True)

MASTER = "小席"
EXPERT_ROLES = ("席小题", "席小文", "席小核", "席小习", "席小盘")


def run(role: str) -> None:
    entry = by_role(role)
    if role == MASTER:
        lark_bridge.run_loop(f"events_{MASTER}", profile=entry["profile"])
        return
    _run_expert(role, entry)


def _run_expert(role: str, entry: dict) -> None:
    open_id = entry["open_id"]
    profile = entry["profile"]
    output_dir = f"events_{role}"
    lark_bridge.start_listener(output_dir, profile)
    log.info("%s 启动，profile=%s open_id=%s", role, profile, open_id)
    try:
        while True:
            for event in lark_bridge.poll_events(output_dir):
                try:
                    msg = parse_inbound(event)
                    if not _should_trigger(role, msg):
                        continue
                    lark_bridge.add_reaction(msg.chat_id, msg.message_id, profile=profile)
                    task, context = _split_task_context(msg.chat_id, role, msg.content)
                    if _basis_request(role, task):
                        topics, basis = context_store.load_basis(msg.chat_id, role)
                        if basis:
                            reply = _match_basis_question(task, topics, basis)
                            lark_bridge.send_reply(msg.chat_id, reply, profile)
                            log.info("%s 回依据（按题配对） chat=%s", role, msg.chat_id)
                            continue
                        text = run_expert(role, _to_explain(task), topics or context)
                        lark_bridge.send_reply(msg.chat_id, text, profile)
                        log.info("%s 回依据（LLM 重释） chat=%s", role, msg.chat_id)
                        continue
                    text = run_expert(role, task, context)
                    if role == "席小核":
                        # 双段输出：公开发结论，详情存档（老板要理由才展示），避免刷屏
                        public, details = split_review(text)
                        if details:
                            context_store.save_review(msg.chat_id, role, details)
                        text = public
                    if role == "席小题" and "【依据】" in text:
                        if "圆桌" in task:
                            lark_bridge.send_reply(msg.chat_id, text, profile)
                        else:
                            topics, basis = _split_topic_output(text)
                            if topics:
                                lark_bridge.send_reply(msg.chat_id, topics, profile)
                            if basis:
                                context_store.save_basis(msg.chat_id, role, topics, basis)
                                lark_bridge.send_reply(msg.chat_id, "需要我给你看选题的依据么？", profile)
                    else:
                        lark_bridge.send_reply(msg.chat_id, text, profile)
                    log.info("%s 回复 chat=%s 长度=%d", role, msg.chat_id, len(text))
                except Exception as e:
                    log.error("%s 处理异常: %s\n%s", role, e, traceback.format_exc())
            time.sleep(2.0)
    except KeyboardInterrupt:
        log.info("%s 收到中断，停止监听", role)


def _should_trigger(role: str, msg) -> bool:
    # 只响应主控（小席）派单。飞书 open_id 按应用隔离，跨应用比对会失配，
    # 改用群内显示名（mention_names）+ 发送者类型（bot 才派单）判定。
    if msg.sender_type != "bot" or role not in msg.mention_names:
        log.info("%s 跳过 sender_type=%r mentions=%r", role, msg.sender_type, msg.mention_names)
        return False
    return True


def _split_task_context(chat_id: str, role: str, text: str):
    task = text.strip()
    for name in EXPERT_ROLES + (MASTER,):
        if task.startswith("@" + name):
            task = task[len(name) + 1:].strip()
            break
    got = context_store.take(chat_id, role)
    return task, (got[1] if got else "")


def _split_topic_output(text: str):
    if "【依据】" in text:
        topics, _, basis = text.partition("【依据】")
        return topics.strip(), basis.strip()
    return text.strip(), ""


def _basis_request(role: str, task: str) -> bool:
    # 老板口语多变（"看看"/"讲下理由"/"凭啥"），只要席小题被追问就尝试读依据存档，命中即按题回
    if role != "席小题" or not task:
        return False
    return task.startswith("【追问】") or task.startswith("老板在群里直接找你聊")


_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

def _match_index(task: str):
    # 老板说「第2个/选题三」等指代 → 取序号，返回 int；没有返回 None
    m = re.search(r"(?:第|选题)\s*([\d一二两三四五六七八九十]+)\s*[个条只]?", task)
    if not m:
        return None
    raw = m.group(1)
    return int(raw) if raw.isdigit() else _CN_NUM.get(raw, 0)

def _match_basis_question(task: str, topics: str, basis: str) -> str:
    pairs = list(_paired_topics(topics, basis))
    if not pairs:
        return basis
    n = _match_index(task)
    if n:
        for num, topic, reason in pairs:
            if num == n:
                # 选题名与依据分两行，避免一长句糊在一起
                return f"选题{n}「{_strip_type(topic)}」\n依据：{reason}"
        return f"没找到第 {n} 个选题的依据。"
    # 每个选题+依据独立成块，块间空行分隔，观感清爽
    blocks = [f"选题{num}「{_strip_type(topic)}」\n依据：{reason}" for num, topic, reason in pairs]
    return "\n\n".join(blocks)


def _paired_topics(topics: str, basis: str):
    tmap = _numbered_lines(topics)
    bmap = _numbered_lines(basis)
    for k in sorted(set(tmap) | set(bmap)):
        yield k, tmap.get(k, "（未给出题目）"), bmap.get(k, "（未给出依据）")


def _numbered_lines(text: str) -> dict:
    out = {}
    for line in (text or "").splitlines():
        m = re.match(r"^\s*(\d+)\s*[.、.．]\s*(.*?)\s*$", line)
        if m:
            out[int(m.group(1))] = m.group(2).strip()
    return out


def _strip_type(text: str) -> str:
    return re.sub(r"^【[^】]*】\s*", "", text)


def _to_explain(task: str) -> str:
    if task.startswith("【追问】"):
        return task
    if task.startswith("老板在群里直接找你聊："):
        return f"【追问】{task[len('老板在群里直接找你聊：'):].strip()}"
    return f"【追问】{task.strip()}"


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "--role":
        sys.exit("用法：python3 -m skin.multi_main --role <小席|席小题|席小文|席小核|席小习|席小盘>")
    run(sys.argv[2])
