import datetime, json, re, time
from log import get_logger
from pipe import InboundMessage, OutboundMessage
from brain.circuit import CircuitBreaker
from brain.intent import recognize, by_keyword, IntentResult, WRITE_SCRIPT_HINTS
from brain.session import store, render_history
from brain import material, rules
from brain.scheduler import mark_published, render_schedule, add_entry, load_schedule, record_data
from brain.experts.xiaoti import XiaotiExpert
from brain.experts.xiaowen import XiaowenExpert
from brain.experts.xiaone import XiaoneExpert, split_review
from brain.experts.xiaoxi import XiaoxiExpert
from brain.experts.fupan import FupanExpert
from brain.experts.dispatch import run_expert, EXPLAIN_MARK, LEARN_MARK
from brain import context_store, market
from skin import bots, identity

_b = CircuitBreaker()
_log = get_logger("controller")
_ADMIN_ONLY = ("出选题", "写脚本", "审核脚本", "改排期", "看排期表", "反馈修改", "追问", "记素材", "看完整讨论", "圆桌讨论", "学规则", "确认规则", "数据回流", "更新市场情报")
_EXPERT_ROLES = ("席小题", "席小文", "席小核", "席小习", "席小盘")

def _fallback_recognize(content: str, history_text: str = "") -> IntentResult:
    try:
        return recognize(content, history_text)
    except Exception as e:
        _log.error("意图识别失败，降级关键词路由: %s", e)
        return by_keyword(content)

_recognize = _fallback_recognize
_pull_history = None  # 皮肤层启动时注入；大脑层不直接调 lark-cli
_emit = None  # 皮肤层注入的即时发送接缝；大脑层不直接调 lark-cli
_context_put = context_store.put  # 派单上下文写入共享文件（测试可替换，避免落盘）

def _default_chat_generate(content: str, history_text: str = "") -> str:
    from brain.llm import generate
    system = (
        "你是小席，老席留学IP系统的主控调度——老板的左右手。老板这句没被识别成明确派单/操作指令，你要像真人一样接住，并且「说到做到」。\n"
        "\n"
        "【回复结构】\n"
        "第一段：共情——接住老板的情绪（安慰/理解/说点暖心的），像真关心他，别摆架子念菜单。\n"
        "第二段：绕回工作——从他这句话里抓一个能落地的角度，主动给具体下一步（角度/方向/数量）。\n"
        "\n"
        "【说话即行动（铁律）】\n"
        "你说的每一句承诺都必须兑现，绝不停在嘴上：\n"
        "1. 你当场能查的（选题记录/排期表）：直接给结果，不许说「我去核对」然后不核对。\n"
        "2. 需要老板点头才能做的派活：在回复最末尾单独一行追加【行动】JSON 登记，老板回「好/要/可以」系统会真执行。\n"
        "3. 答应查却没查到：明说没找到，给方向让老板定，不许假装查过。\n"
        "\n"
        "【行动JSON格式】（只写在回复最末尾，单独一行，0-2 行；这行是给系统的执行契约，不是给老板说的话）\n"
        "【行动】{\"action\":\"<动作>\",\"task\":\"<具体任务>\",\"when\":\"立即|待确认\"}\n"
        "动作白名单：\n"
        "- 核对选题记录（立即）：系统会真读选题存档/会话记忆，把回执补到你回复后面。\n"
        "- 看排期表（立即）：系统会把当前排期表补到你回复后面。\n"
        "- 重新出选题/出选题（待确认）：派席小题。task 写清数量+角度。\n"
        "- 写脚本（待确认）：派席小文+席小核。task 写清选题。\n"
        "- 审核脚本（待确认）：派席小核。\n"
        "只登记你真能调动的动作：不提议改排期（聊天锁定不了具体日期选题）。\n"
        "\n"
        "【示例】\n"
        "老板：刚才那个预算对比的选题，我找不到了。\n"
        "小席：老板，别急，我这就翻存档给您核对。\n"
        "【行动】{\"action\":\"核对选题记录\",\"task\":\"\",\"when\":\"立即\"}\n"
        "\n"
        "老板：这个方向是不错，就是太生硬了。\n"
        "小席：懂了，太生硬——家长不是被说服的，是被共情到的。要不要让席小题换个「家长视角」重新出一版？\n"
        "【行动】{\"action\":\"重新出选题\",\"task\":\"按预算对比方向、家长视角重出3个选题\",\"when\":\"待确认\"}\n"
        "\n"
        "【禁止】\n"
        "- 不编造事实；数字/记录拿不准就说没找到，给方向让老板定。\n"
        "- 不提议改排期（需具体日期选题，聊天锁定不了）。\n"
        "- 正文简洁，120字以内（【行动】行不算）；直接给最终答复，不要输出思考过程。"
    )
    user = f"最近对话：\n{history_text}\n\n老板说：{content}" if history_text else f"老板说：{content}"
    # 推理型模型先消耗 reasoning 再出正文，预算小会被思考吃满空输出退模板；拉高到 10000 防空（调研见 2026-08-03 变更记录）
    return generate(system, user, max_tokens=10000, temperature=0.7)

_chat_generate = _default_chat_generate

def _chat_reply(msg: InboundMessage, content: str, rounds: list, replies: list) -> None:
    try:
        text = _chat_generate(content, render_history(rounds))
    except Exception as e:
        _log.error("对话回复失败，降级模板: %s", e)
        text = ""
    _register_actions(msg.chat_id, text, rounds, replies)
    _register_proposal(msg.chat_id, text)
    text = _strip_action_lines(text)
    text = _strip_proposal_marker(text)
    replies.append(OutboundMessage(msg.chat_id, text.strip() or "需要我做什么？出选题/写脚本/看排期/讨论？", "小席"))

def _register_actions(chat_id: str, text: str, rounds: list, replies: list) -> None:
    """解析小席聊天回复里的【行动】JSON：立即动作当场执行，待确认动作登记，老板点头后真跑。"""
    for m in _ACTION_RE.finditer(text or ""):
        try:
            act = json.loads(m.group(1))
        except ValueError:
            continue
        action = str(act.get("action") or "").strip()
        if not action:
            continue
        task = str(act.get("task") or "").strip()
        if str(act.get("when") or "") == "立即":
            _run_immediate(chat_id, action, task, rounds, replies)
        else:
            _register_action_pending(chat_id, action, task)

def _register_action_pending(chat_id: str, action: str, task: str) -> None:
    intent = _ACTION_INTENTS.get(action)
    if not intent:
        _log.info("行动未登记：未知动作 %s", action)
        return
    if intent == "出选题":
        task = task or "重新出一版选题"
    _pending_action[chat_id] = {"intent": intent, "params": {}, "task": task, "ts": time.time()}
    _log.info("登记待定动作 chat=%s intent=%s task=%r", chat_id, intent, task)

def _run_immediate(chat_id: str, action: str, task: str, rounds: list, replies: list) -> None:
    """小席承诺当场兑现的动作：真去读记录/排期，把回执补进回复，不空头承诺。"""
    if action in ("核对选题记录", "核对记录"):
        reply = _lookup_topic_record(chat_id, "核对选题记录", rounds, replies, force=True)
        if reply:
            replies.append(OutboundMessage(chat_id, reply, "小席"))
    elif action in ("看排期表",):
        replies.append(OutboundMessage(chat_id, "当前排期表：\n" + render_schedule() + _base_link(), "小席"))

def _register_expert_proposal(chat_id: str, text: str) -> None:
    """专家回复里带【动作名】的建议下一步 → 登记待确认动作，老板点头即执行。"""
    m = _EXPERT_ACTION_RE.search(text or "")
    if not m:
        return
    action = m.group(1)
    intent = _ACTION_INTENTS.get(action)
    if not intent:
        return
    line = next((l for l in text.split("\n") if m.group(0) in l), "")
    task = line.split(m.group(0), 1)[-1].strip(" ？?！!。，,；;")
    if intent == "出选题":
        task = task or "重新出一版选题"
    _pending_action[chat_id] = {"intent": intent, "params": {}, "task": task, "ts": time.time()}
    _log.info("专家提议登记 chat=%s intent=%s task=%r", chat_id, intent, task)

def _strip_action_lines(text: str) -> str:
    return _ACTION_RE.sub("", text or "").strip()

_PROPOSAL_RE = re.compile(r"【提议】\s*([^：:]+?)\s*[：:]\s*(.*)")
_PROPOSAL_INTENTS = (("重新出选题", "出选题"), ("重新整理", "出选题"), ("重新出一版", "出选题"),
                     ("出个选题", "出选题"), ("出条选题", "出选题"), ("出选题", "出选题"),
                     ("写条脚本", "写脚本"), ("写条", "写脚本"), ("写脚本", "写脚本"), ("出文案", "写脚本"), ("文案", "写脚本"),
                     ("审核", "审核脚本"), ("审一遍", "审核脚本"), ("复审", "审核脚本"),
                     ("看排期表", "看排期表"), ("排期表", "看排期表"))

# 「说话即行动」契约：小席聊天回复末尾的机器可执行行动行（行首锚定，避免正文误配）
_ACTION_RE = re.compile(r"^【行动】\s*(\{.*\})\s*$", re.M)
_ACTION_INTENTS = {
    "重新出选题": "出选题", "重新整理选题": "出选题", "重新出一版": "出选题", "出选题": "出选题",
    "写脚本": "写脚本", "写条脚本": "写脚本", "出条脚本": "写脚本",
    "审核脚本": "审核脚本", "审核": "审核脚本", "审一遍": "审核脚本", "复审": "审核脚本",
}
# 专家回复里建议下一步用的可读标记：把动作名用【】括进建议句，主控识别后登记待确认动作，老板点头即执行
_EXPERT_ACTION_RE = re.compile(r"【(重新出选题|重新整理选题|重新出一版|出选题|写脚本|写条脚本|出条脚本|审核脚本|审核)】")

def _register_proposal(chat_id: str, text: str) -> None:
    m = _PROPOSAL_RE.search(text or "")
    if not m:
        return
    action, detail = m.group(1).strip(), m.group(2).strip()
    for kw, intent in _PROPOSAL_INTENTS:
        if kw in action:
            task = detail
            if intent == "出选题":
                task = task or "重新出一版选题"
            _pending_action[chat_id] = {"intent": intent, "params": {}, "task": task, "ts": time.time()}
            _log.info("登记待定动作 chat=%s intent=%s task=%r", chat_id, intent, task)
            return

def _strip_proposal_marker(text: str) -> str:
    return re.sub(r"【提议】.*", "", text or "").strip()

def _write_script(msg: InboundMessage, content: str, task: str, rounds: list, replies: list) -> None:
    topic, ctype = _pipe_ctx(msg.chat_id, content, rounds, task)
    if topic:
        _draft_ctx[msg.chat_id] = (topic, ctype)
    task = _resolve_script_task(task, content, rounds, msg.chat_id)
    _log.info("派单 席小文 + 席小核")
    _post(msg.chat_id, replies, "小席", "写脚本进行中：席小文写作 → 席小核审核，两段约30-60秒…")
    wen = _dispatch_expert(msg.chat_id, replies, "席小文", task or content, _recent_context(rounds))
    if wen is None:
        _pipelines[msg.chat_id] = {"step": "wait_wen", "content": content, "ts": time.time()}
    else:
        _post(msg.chat_id, replies, "席小文", wen)
        he = _dispatch_expert(msg.chat_id, replies, "席小核", "审核这条脚本", f"【脚本原文】\n{wen}")
        if he is None:
            _pipelines[msg.chat_id] = {"step": "wait_he", "script": wen, "ts": time.time()}
        else:
            _post_xiaone(msg.chat_id, replies, he)
            _post(msg.chat_id, replies, "小席", "写脚本流水线完成：席小文出稿 + 席小核审核已展示，要改直接说。")

def _post_xiaone(chat_id: str, replies: list, text: str) -> None:
    # 席小核输出含公开结论 + 详情段：公开发群，详情存档（老板要理由才展示）
    public, details = split_review(text)
    if details:
        context_store.save_review(chat_id, "席小核", details)
    _post(chat_id, replies, "席小核", public)

def _resolve_script_task(task: str, content: str, rounds: list, chat_id: str = "") -> str:
    # 把「第2个」「选题三」等指代解析成具体选题文字，避免席小文凭感觉猜错选题
    m = re.search(r"(?:第|选题)([\d一二两三四五六七八九十]+)[条个]?", task or content)
    if not m:
        return task or content
    topic = _topic_by_index(_to_int(m.group(1)), rounds, chat_id)
    if not topic:
        return task or content
    return f"写「{topic}」这条的脚本"


def _topic_by_index(n: int, rounds: list, chat_id: str = "") -> str:
    # 优先读席小题的依据存档（含完整选题列表），再回落会话记忆里的上一条产出
    if chat_id:
        topics, _basis = context_store.load_basis(chat_id, "席小题")
        if topics:
            item = _pick_item(topics, n)
            if item:
                return _clean_topic(item)
    speaker, prev = _last_expert_output(rounds)
    if speaker == "席小题" and prev:
        item = _pick_item(prev, n)
        if item:
            return _clean_topic(item)
    return ""


def _clean_topic(item: str) -> str:
    topic = re.sub(r"^\d+[.、）)]\s*", "", item.strip())
    topic = re.sub(r"^【[^】]*】\s*", "", topic).strip()
    return topic.split("（")[0].strip()

def _type_from_line(line: str) -> str:
    # 从选题清单行首解析类型标签（1.【信任】xxx → 信任），无标签默认曝光
    m = re.search(r"【(曝光|留资|信任)】", line or "")
    return m.group(1) if m else "曝光"

def _pipe_ctx(chat_id: str, content: str, rounds: list, task: str = "") -> tuple:
    # 写脚本任务开始时解析对应选题 + 类型（「第X个/选题X」→ basis 清单定位），定稿时供追溯
    # 优先 content（原始消息含「选题3」），task 常被 LLM 规范化成「写「具体选题」」丢了序号
    m = re.search(r"(?:第|选题)([\d一二两三四五六七八九十]+)[条个]?", content or "") \
        or re.search(r"(?:第|选题)([\d一二两三四五六七八九十]+)[条个]?", task or "")
    if not m:
        return "", ""
    n = _to_int(m.group(1))
    if chat_id:
        topics, _basis = context_store.load_basis(chat_id, "席小题")
        if topics:
            item = _pick_item(topics, n)
            if item:
                return _clean_topic(item), _type_from_line(item)
    speaker, prev = _last_expert_output(rounds)
    if speaker == "席小题" and prev:
        item = _pick_item(prev, n)
        if item:
            return _clean_topic(item), _type_from_line(item)
    return "", ""

def _record_passed(chat_id: str) -> None:
    # 脚本定稿（审核通过 / 老板放行）时记录「刚定稿选题」，供「这条选题通过了/排期发布」追溯
    if chat_id in _draft_ctx:
        topic, ctype = _draft_ctx[chat_id]
        if topic:
            _last_passed[chat_id] = {"topic": topic, "content_type": ctype}

def _force_script_task(content: str, rounds: list) -> str:
    # 老板口语「根据这个出内容/按这个出」被意图识别误归「其他」时的兜底：
    # 命中产出指令且有上一条产出可依据 → 强制进入写脚本派单，别只空头承诺
    if not any(h in content for h in WRITE_SCRIPT_HINTS):
        return ""
    speaker, prev = _last_expert_output(rounds)
    if not prev:
        return ""
    return f"根据「{speaker}」的以下产出写脚本：\n{prev}"

_PIPE_TIMEOUT = 180.0
_MAX_FIX_ROUNDS = 2  # 席小核审核不通过时，自动 @席小文 修改的轮次上限，超了交给老板
_REASON_YES = ("要", "要吧", "要的", "可以", "好的", "行", "看看", "看下", "展示", "想看")
_pipelines = {}  # chat_id -> 异步编排状态（写脚本/圆桌/数据回流）
_draft_ctx = {}  # chat_id -> (topic, content_type)：最近一次写脚本任务对应的选题，定稿时升格为 _last_passed
_last_passed = {}  # chat_id -> {"topic", "content_type"}：最近一次定稿的选题，供「这条选题通过了/排期发布」追溯
_pending_action = {}  # chat_id -> {"intent", "params", "task", "ts"}：小席提议待老板点头后真执行的动作

def _controller_factories() -> dict:
    return {
        "席小题": XiaotiExpert, "席小文": XiaowenExpert, "席小核": XiaoneExpert,
        "席小习": XiaoxiExpert, "席小盘": FupanExpert,
    }

def _is_script_output(text: str) -> bool:
    return text.startswith("【席小文】") and "## 脚本" in text

def _last_script(rounds: list) -> str:
    # 找会话记忆里最近一条席小文脚本产出，供老板「审核一下」时派席小核
    for rnd in reversed(rounds):
        for turn in reversed(rnd):
            if turn["speaker"] == "席小文" and _is_script_output(turn["text"]):
                return turn["text"]
    return ""

def _review_script(msg: InboundMessage, content: str, rounds: list, replies: list) -> None:
    prev = _last_script(rounds)
    if not prev:
        replies.append(OutboundMessage(msg.chat_id,
            "要审核哪条？先让席小文写条脚本，再喊「审核一下」，或直接 @席小核。", "小席"))
        return
    _log.info("审核脚本 派单 席小核")
    he = _dispatch_expert(msg.chat_id, replies, "席小核", "审核这条脚本", f"【脚本原文】\n{prev}")
    if he is not None:
        _post_xiaone(msg.chat_id, replies, he)

def _dispatch_expert(chat_id: str, replies: list, role: str, task: str, context: str = ""):
    try:
        bot = bots.by_role(role)
    except KeyError:
        bot = None
    if bot is None or _emit is None:
        try:
            return run_expert(role, task, context, factories=_controller_factories())
        except Exception as e:
            _log.error("专家 %s 调用失败: %s", role, e)
            return "（该专家未给出意见）"
    if context:
        _context_put(chat_id, role, task, context)
    _emit(chat_id, f"<at user_id=\"{bot['open_id']}\"></at> {task}", role)
    return None

def handle_bot_output(msg: InboundMessage) -> list:
    """主控收到专家 bot 消息：写入会话记忆并推进异步流水线。"""
    replies = []
    store.add_round(msg.chat_id, [{"speaker": msg.sender_role, "text": msg.content}])
    pipe = _pipelines.get(msg.chat_id)
    role = msg.sender_role
    if pipe is None:
        # 流水线被超时清理、但席小文迟到脚本才到：仍兑现「出稿→席小核审核」
        if role == "席小文" and _is_script_output(msg.content):
            _log.info("流水线已超时，席小文迟到脚本补派席小核")
            he = _dispatch_expert(msg.chat_id, replies, "席小核", "审核这条脚本", f"【脚本原文】\n{msg.content}")
            if he is None:
                _pipelines[msg.chat_id] = {"step": "wait_he", "script": msg.content, "fix_rounds": 0, "ts": time.time()}
            else:
                _post_xiaone(msg.chat_id, replies, he)
        # 多bot模式下专家自回的建议下一步 → 登记待确认动作，老板点头即执行
        if role in _EXPERT_ROLES:
            _register_expert_proposal(msg.chat_id, msg.content)
        return replies
    step = pipe.get("step")
    if step == "wait_wen" and role == "席小文":
        _log.info("写脚本流水线 wait_wen→wait_he")
        he = _dispatch_expert(msg.chat_id, replies, "席小核", "审核这条脚本", f"【脚本原文】\n{msg.content}")
        if he is None:
            _pipelines[msg.chat_id] = {"step": "wait_he", "script": msg.content, "fix_rounds": 0, "ts": time.time()}
        else:
            _post(msg.chat_id, replies, "席小核", he)
            _pipelines.pop(msg.chat_id, None)
            _post(msg.chat_id, replies, "小席", "写脚本流水线完成：席小文出稿 + 席小核审核已展示，要改直接说。")
    elif step == "wait_he" and role == "席小核":
        verdict = _verdict_classify(msg.content)
        public, details = split_review(msg.content)
        if details:
            context_store.save_review(msg.chat_id, "席小核", details)
        rounds_fix = pipe.get("fix_rounds", 0)
        if verdict == "fail" and rounds_fix < _MAX_FIX_ROUNDS:
            _log.info("写脚本流水线 审核未过→席小文修改（第%d轮）", rounds_fix + 1)
            fix_task = f"审核未通过，修改这条脚本。\n审核意见：{msg.content.strip().split(chr(10), 1)[0]}\n"
            details = context_store.load_review(msg.chat_id, "席小核")
            if details:
                fix_task += f"审核详情：\n{details}\n"
            fix_task += "直接输出改好的完整新版脚本，不用解释过程。"
            _dispatch_expert(msg.chat_id, replies, "席小文", fix_task, pipe["script"])
            _pipelines[msg.chat_id] = {"step": "wait_fix", "script": pipe["script"],
                                       "reviewer": msg.content, "fix_rounds": rounds_fix + 1, "ts": time.time()}
            _post(msg.chat_id, replies, "小席",
                  f"席小核审核未通过：{_cap(msg.content)}\n已让席小文按意见修改，改完再复审。")
        elif verdict == "fail":
            _pipelines.pop(msg.chat_id, None)
            _post(msg.chat_id, replies, "小席",
                  f"席小核复审仍不过（已改 {rounds_fix} 轮）：{_cap(msg.content)}\n请老板定夺，或手动 @席小文 继续改。")
        elif verdict == "doubt":
            _log.info("写脚本流水线 审核有疑问→暂停，@老板拍板")
            _pipelines[msg.chat_id] = {"step": "wait_boss", "script": pipe["script"],
                                       "reviewer": msg.content, "fix_rounds": rounds_fix, "ts": time.time()}
            _post(msg.chat_id, replies, "小席",
                  f"{_admin_at_mentions()}席小核审核有疑问，需要您定夺：\n{_cap(msg.content, 240)}\n"
                  "给个确定答案（例：费用按8万没问题 / 这句删掉），我会让席小核记住、席小文按答案修改再复审；\n"
                  "回「可以了/继续」＝按当前脚本通过定稿；回「不用了」＝取消本次修改。")
        else:
            _record_passed(msg.chat_id)
            _pipelines.pop(msg.chat_id, None)
            _post(msg.chat_id, replies, "小席", "写脚本流水线完成：席小文出稿 + 席小核审核通过，要改直接说。")
    elif step == "wait_fix" and role == "席小文":
        _log.info("写脚本流水线 wait_fix→wait_he（复审）")
        he = _dispatch_expert(msg.chat_id, replies, "席小核", "审核这条脚本", f"【脚本原文】\n{msg.content}")
        if he is None:
            _pipelines[msg.chat_id] = {"step": "wait_he", "script": msg.content,
                                       "fix_rounds": pipe.get("fix_rounds", 0), "ts": time.time()}
        else:
            _pipelines.pop(msg.chat_id, None)
            _post_xiaone(msg.chat_id, replies, he)
    elif step == "roundtable" and role in pipe["waiting"]:
        pipe["waiting"].remove(role)
        pipe["views"].append((role, msg.content))
        if not pipe["waiting"]:
            _pipelines.pop(msg.chat_id, None)
            _post(msg.chat_id, replies, "小席", _roundtable_summary(pipe))
    elif step == "wait_fupan" and role == "席小盘":
        conclusion = msg.content
        _pipelines.pop(msg.chat_id, None)
        date, topic, data = pipe["date"], pipe["topic"], pipe["data"]
        _post(msg.chat_id, replies, "小席", f"数据已回流：{date}「{topic}」 {data}\n\n【席小盘】\n{conclusion}")
        feed_ti = f"你的选题「{topic}」发布效果：{data}。复盘分析：{conclusion[:120]}\n据此迭代下次的选题方向。"
        _dispatch_expert(msg.chat_id, replies, "席小题", feed_ti, "")
        feed_wen = f"你写的「{topic}」脚本发布效果：{data}。复盘分析：{conclusion[:120]}\n据此迭代下次的脚本写法。"
        _dispatch_expert(msg.chat_id, replies, "席小文", feed_wen, "")
    return replies

def sweep_pipelines() -> list:
    """超时扫描：超过 _PIPE_TIMEOUT 未推进的流水线，补纪要/超时提示并清掉。"""
    replies = []
    now = time.time()
    expired = [cid for cid, p in _pipelines.items() if now - p.get("ts", 0) > _PIPE_TIMEOUT]
    for cid in expired:
        p = _pipelines.pop(cid)
        if p.get("step") == "wait_boss":
            # 疑问等老板拍板：不丢弃，重 @老板提醒并重置计时
            at = _admin_at_mentions()
            _pipelines[cid] = dict(p, ts=now)
            _post(cid, replies, "小席",
                  f"{at}席小核审核的疑问还在等您拍板：\n{_cap(p.get('reviewer', ''), 120)}\n"
                  "给个确定答案；回「可以了/继续」按当前脚本通过；回「不用了」取消。")
            continue
        if p.get("step") == "roundtable":
            views = "\n".join(f"- {r}: {_cap(v)}" for r, v in p["views"])
            missing = "、".join(p["waiting"])
            _post(cid, replies, "小席",
                  f"【圆桌纪要】议题：{p['subject']}\n{views}\n未给出意见：{missing}（超时）")
        else:
            _post(cid, replies, "小席", "流水线超时：专家未在时限内回复，要重跑说一声。")
    return replies

def _roundtable_summary(pipe: dict) -> str:
    views = "\n".join(f"- {r}: {_cap(v)}" for r, v in pipe["views"])
    return f"【圆桌纪要】议题：{pipe['subject']}\n{views}\n三个视角已出，请老板定夺下一步（写脚本/再讨论）。"

def _post(chat_id: str, replies: list, tag: str, text: str) -> None:
    m = OutboundMessage(chat_id, text, tag)
    if _emit is not None:
        _emit(chat_id, text, tag)
        m.emitted = True
    replies.append(m)

def _history_context(chat_id: str) -> str:
    if _pull_history is None:
        return ""
    try:
        return _pull_history(chat_id, 15)
    except Exception as e:
        _log.error("拉取群历史失败: %s", e)
        return ""

_AFFIRM_WORDS = ("好", "要", "可以", "行", "嗯", "好呀", "好啊", "好的", "要的", "来吧",
                 "就这么办", "就这么定", "按这个来", "就这么着", "安排", "ok", "OK", "Ok", "可以啊", "行吧", "嗯嗯", "对")

def _is_affirmative(content: str) -> bool:
    c = content.strip()
    return 0 < len(c) <= 8 and any(c == w or c.startswith(w) for w in _AFFIRM_WORDS)

def _pop_pending(chat_id: str) -> dict | None:
    pending = _pending_action.get(chat_id)
    if not pending or time.time() - pending.get("ts", 0) > 3600:
        _pending_action.pop(chat_id, None)
        return None
    _pending_action.pop(chat_id, None)
    return pending

def _route_intent(msg: InboundMessage, result: IntentResult, rounds: list, replies: list) -> None:
    intent = result.intent
    content = msg.content
    if intent == "出选题":
        if not (result.task or result.params.get("count") or result.params.get("topic")):
            replies.append(OutboundMessage(msg.chat_id, "出选题可以。想让我出几个、从哪个角度出？（比如：3个，预算对比）", "小席"))
        else:
            _log.info("派单 席小题")
            task = (result.task or "").strip() or _topic_task(result.params)
            out = _dispatch_expert(msg.chat_id, replies, "席小题", task, _xiaoti_context(content, rounds, msg.chat_id))
            if out is not None:
                replies.append(OutboundMessage(msg.chat_id, out, "席小题"))
    elif intent == "写脚本":
        _write_script(msg, content, (result.task or "").strip(), rounds, replies)
    elif intent == "审核脚本":
        _review_script(msg, content, rounds, replies)
    elif intent == "看排期表":
        replies.append(OutboundMessage(msg.chat_id, "当前排期表：\n" + render_schedule() + _base_link(), "小席"))
    elif intent == "改排期":
        picked = _try_pick_topic(content, result.params, rounds, msg.chat_id)
        if picked is not None:
            add_entry(picked)
            replies.append(OutboundMessage(msg.chat_id,
                f"已排上：{picked['date']} {picked['publish_time']} 发「{picked['topic']}」（类型：{picked['content_type']}）\n" + render_schedule() + _base_link(), "小席"))
        elif any(w in content for w in _PASSED_HINTS):
            # 老板确认「这条选题/排期发布」但没追溯到刚定稿的选题（如进程重启）→ 明说失败，绝不生成占位
            replies.append(OutboundMessage(msg.chat_id,
                "没找到刚定稿的选题（可能刚重启或脚本还没走完审核）。请直接说「排上第1个」指定具体选题，或重新写一条脚本。", "小席"))
        else:
            entries = _schedule_entries(result.params, content)
            for e in entries:
                add_entry(e)
            replies.append(OutboundMessage(msg.chat_id, f"已排{len(entries)}条：\n" + render_schedule() + _base_link(), "小席"))
    elif intent == "记素材":
        _log.info("记素材入库")
        replies.append(OutboundMessage(msg.chat_id, _record_material(content), "小席"))
    elif intent == "看完整讨论":
        hist = _history_context(msg.chat_id)
        replies.append(OutboundMessage(msg.chat_id,
                                       "最近群讨论：\n" + hist if hist else "完整讨论拉取未接入或暂无",
                                       "小席"))
    elif intent == "查记录":
        _log.info("查记录 → 真核对回执")
        record_reply = _lookup_topic_record(msg.chat_id, content, rounds, replies, force=True)
        if record_reply:
            replies.append(OutboundMessage(msg.chat_id, record_reply, "小席"))
    elif intent == "圆桌讨论":
        _log.info("圆桌讨论 消息=%r", content[:60])
        _roundtable(msg, content, result.params, rounds, replies)
    elif intent == "反馈修改":
        _revise(msg, content, rounds, replies)
    elif intent == "追问":
        _ask(msg, content, rounds, replies)
    elif intent == "学规则":
        _learn_rules(msg, content, rounds, replies)
    elif intent == "确认规则":
        _confirm_rules(msg, content, replies)
    elif intent == "数据回流":
        _reflow(msg, content, replies)
    elif intent == "更新市场情报":
        _refresh_market(msg, replies)
    else:
        record_reply = _lookup_topic_record(msg.chat_id, content, rounds, replies)
        if record_reply:
            _log.info("查选题记录 → 真核对回执")
            replies.append(OutboundMessage(msg.chat_id, record_reply, "小席"))
        else:
            reason_reply = _show_review_reasons(msg.chat_id, content, rounds)
            if reason_reply:
                _log.info("老板要审核理由，直发存档详情")
                replies.append(OutboundMessage(msg.chat_id, reason_reply, "席小核"))
            else:
                target = next((n for n in msg.mention_names if n in _EXPERT_ROLES), "")
                if target:
                    _log.info("老板直接 @%s 闲聊，派单该角色自然接话", target)
                    out = _dispatch_expert(msg.chat_id, replies, target,
                                           f"老板在群里直接找你聊：{content}\n"
                                           "先自然共情接住老板的话，别摆架子；然后绕回你的本职工作，主动建议一个具体下一步。",
                                           _recent_context(rounds))
                    if out is not None:
                        _register_expert_proposal(msg.chat_id, out)
                        replies.append(OutboundMessage(msg.chat_id, out, target))
                else:
                    force = _force_script_task(content, rounds)
                    if force:
                        _log.info("意图识别落「其他」但命中产出指令，强制写脚本派单")
                        _write_script(msg, content, force, rounds, replies)
                    else:
                        _chat_reply(msg, content, rounds, replies)

def handle_message(msg: InboundMessage) -> list[OutboundMessage]:
    role = msg.sender_role
    content = msg.content
    replies = []
    _log.info("路由 角色=%s 聊天=%s 内容=%r", role, msg.chat_id, content[:60])
    tripped = _b.record(1_000)

    if tripped:
        _log.warning("熔断触发，token 累计达阈值")
        replies.append(OutboundMessage(msg.chat_id, "熔断触发，token已到阈值，建议暂停", "小席"))
        _remember(msg, replies)
        return replies

    rounds = store.history(msg.chat_id)
    history_text = render_history(rounds)
    result = _recognize(content, history_text)
    intent = result.intent
    _log.info("意图 %s 参数=%s", intent, result.params)

    if _pipelines.get(msg.chat_id, {}).get("step") == "wait_boss" and _is_admin(role):
        _resolve_doubt(msg, content, rounds, replies)
        return replies

    if _is_admin(role) and not rules.has_pending() and _is_affirmative(content):
        pending = _pop_pending(msg.chat_id)
        if pending is not None:
            _log.info("老板确认提议 → 执行待定动作 intent=%s", pending["intent"])
            _route_intent(msg, IntentResult(pending["intent"], pending.get("params") or {},
                                            content, pending.get("task") or ""), rounds, replies)
            _remember(msg, replies)
            return replies

    if intent == "确认已发布":
        if _is_admin(role) or role == "发片同事":
            ok = _try_mark_published(content)
            replies.append(OutboundMessage(msg.chat_id, f"已发状态：{ok}", "小席"))
        else:
            replies.append(OutboundMessage(msg.chat_id, "无权限：只有老板、产品或发片同事能确认已发布", "小席"))
        _remember(msg, replies)
        return replies

    if intent in _ADMIN_ONLY and not _is_admin(role):
        if role == "未知" and intent == "看排期表":
            replies.append(OutboundMessage(msg.chat_id, "无权限：当前身份未识别", "小席"))
        else:
            replies.append(OutboundMessage(msg.chat_id, "无权限：只有老板或产品能派单或改排期", "小席"))
        _remember(msg, replies)
        return replies

    _route_intent(msg, result, rounds, replies)
    _remember(msg, replies)
    return replies

def _remember(msg: InboundMessage, replies: list[OutboundMessage]) -> None:
    turns = [{"speaker": msg.sender_role, "text": msg.content}]
    for r in replies:
        turns.append({"speaker": r.agent_tag, "text": r.text})
    store.add_round(msg.chat_id, turns)

def _recent_context(rounds: list) -> str:
    return render_history(rounds[-2:]) if rounds else ""

def _xiaoti_context(content: str, rounds: list, chat_id: str) -> str:
    parts = []
    if content.strip():
        parts.append(f"【本条消息原文（可能是用户提供的泛类素材）】\n{content}")
    hist = _history_context(chat_id)
    if hist:
        parts.append(f"【最近群讨论（完整讨论记录）】\n{hist}")
    recent = _recent_context(rounds)
    if recent:
        parts.append(recent)
    return "\n\n".join(parts)

_SAVE_HINTS = ("记素材", "收素材", "存素材", "素材入库", "收下")

def _record_material(content: str) -> str:
    body = content
    for k in _SAVE_HINTS:
        body = body.replace(k, "")
    body = body.strip(" ：:，,。")
    try:
        name = material.save_material(body)
    except ValueError:
        return "素材内容为空，示例：记素材：这篇讲预算对比的文章"
    return f"已存素材：{name}"

_ROUNDTABLE_MAX = 180

def _roundtable(msg: InboundMessage, content: str, params: dict, rounds: list, replies: list) -> None:
    subject = _roundtable_subject(content, params, rounds)
    if not subject:
        _post(msg.chat_id, replies, "小席",
              "圆桌需要一个议题，例如：大家讨论下第二条选题 / 关于预算对比开个圆桌")
        return
    _log.info("圆桌 议题=%r", subject[:60])
    _post(msg.chat_id, replies, "小席",
          f"【圆桌进行中】议题：{subject}\n席小题（选题）/席小文（内容）/席小核（审核）正在分别出观点，每段约5-25秒…")
    tasks = {
        "席小题": (f"圆桌议题：{subject}。从选题视角给判断与依据，{_ROUNDTABLE_MAX}字内",
                   _xiaoti_context(content, rounds, msg.chat_id)),
        "席小文": (f"圆桌议题：{subject}。从内容创作视角给角度与钩子建议，{_ROUNDTABLE_MAX}字内",
                   _recent_context(rounds)),
        "席小核": (f"圆桌议题：{subject}。从审核视角指出风险与红线，{_ROUNDTABLE_MAX}字内",
                   subject),
    }
    views = {r: _dispatch_expert(msg.chat_id, replies, r, t, c) for r, (t, c) in tasks.items()}
    missing = [r for r, v in views.items() if v is None]
    if missing:
        _pipelines[msg.chat_id] = {
            "step": "roundtable", "subject": subject,
            "waiting": missing,
            "views": [(r, v) for r, v in views.items() if v is not None],
            "ts": time.time(),
        }
        return
    for r, v in views.items():
        _post(msg.chat_id, replies, r, _cap(v))
    _post(msg.chat_id, replies, "小席",
          f"【圆桌纪要】议题：{subject}\n三个视角已出，请老板定夺下一步（写脚本/再讨论）。")

_CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

def _to_int(text: str) -> int:
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "十" in text:
        tens, ones = text.split("十", 1)
        return (_CN_DIGITS.get(tens, 1) * 10 if tens else 10) + (_CN_DIGITS.get(ones, 0) if ones else 0)
    return _CN_DIGITS.get(text, 0)

def _roundtable_subject(content: str, params: dict, rounds: list) -> str:
    topic = (params.get("topic") or "").strip()
    if topic:
        return topic[:150]
    _, prev = _last_expert_output(rounds)
    if prev:
        m = re.search(r"第([\d一二两三四五六七八九十]+)[条个]", content)
        if m:
            item = _pick_item(prev, _to_int(m.group(1)))
            if item:
                return item
        line = prev.split("\n", 1)[0].strip()
        if line:
            return line[:150]
    return _strip_roundtable_words(content)[:150]

def _pick_item(text: str, n: int) -> str:
    for line in text.split("\n"):
        line = line.strip()
        if re.match(rf"^{n}[.、）)]", line):
            return line[:150]
    return ""

def _strip_roundtable_words(content: str) -> str:
    body = content
    for w in ("开圆桌会议", "圆桌会议", "开圆桌", "圆桌一下", "圆桌", "大家", "讨论一下", "讨论下", "讨论", "一下"):
        body = body.replace(w, "")
    return body.strip(" ：:，,。")

def _verdict_classify(text: str) -> str:
    # 三态判定：通过/不通过/需老板确认。绝不能把「无红线，通过」误判成需改——
    # 只认明确拒稿信号（不通过/未通过/❌/需改）与疑问信号（需老板确认/待确认），不扫「红线」词面。
    head = text.strip().split("\n", 1)[0]
    if any(k in head for k in ("不通过", "未通过", "❌", "需改")):
        return "fail"
    if any(k in head for k in ("需老板确认", "需确认", "待确认", "请确认")):
        return "doubt"
    if any(k in text for k in ("不通过", "未通过", "❌", "需改")):
        return "fail"
    if any(k in text for k in ("需老板确认", "需确认", "待确认", "请确认")):
        return "doubt"
    return "pass"

def _cap(text: str, n: int = _ROUNDTABLE_MAX) -> str:
    text = text.strip()
    return text if len(text) <= n else text[:n] + "…"

def _revise(msg: InboundMessage, content: str, rounds: list, replies: list) -> None:
    speaker, prev = _last_expert_output(rounds)
    if not prev:
        replies.append(OutboundMessage(msg.chat_id, "没有找到可修改的上一条产出", "小席"))
        return
    expert_role = speaker if speaker in ("席小题", "席小文") else "席小文"
    ctx = f"【上一条产出 · {speaker}】\n{prev}"
    _log.info("反馈修改 重写 %s + 学差异 席小习", speaker)
    out = _dispatch_expert(msg.chat_id, replies, expert_role, content, ctx)
    if out is not None:
        replies.append(OutboundMessage(msg.chat_id, out, expert_role))
    learned = _dispatch_expert(msg.chat_id, replies, "席小习", content, ctx)
    if learned is not None:
        replies.append(OutboundMessage(msg.chat_id, learned, "席小习"))

def _ask(msg: InboundMessage, content: str, rounds: list, replies: list) -> None:
    speaker, prev = _last_any_output(rounds)
    if speaker == "席小核":
        # 追问席小核审核 → 直发存档的审核详情；详情缺失（模型省略详情段/存档超时被清）时现生成简洁理由
        reason = _review_reasons(msg.chat_id, rounds)
        if reason:
            replies.append(OutboundMessage(msg.chat_id, reason, "席小核"))
        else:
            replies.append(OutboundMessage(msg.chat_id, "席小核本次审核没存详情，可重新 @席小核 审一遍", "小席"))
        return
    if not prev:
        replies.append(OutboundMessage(msg.chat_id, "没有找到可追问的上一条产出", "小席"))
        return
    expert_role = speaker if speaker in ("席小题", "席小文") else "席小文"
    ctx = f"【你上次的产出 · {speaker}】\n{prev}"
    if speaker == "席小题":
        topics, basis = context_store.load_basis(msg.chat_id, "席小题")
        if basis:
            ctx += f"\n\n【你上次的选题与依据（固定存档，可直接引用）】\n{topics}\n【依据】\n{basis}"
    _log.info("追问 派单 %s", speaker)
    out = _dispatch_expert(msg.chat_id, replies, expert_role, f"{EXPLAIN_MARK}{content}", ctx)
    if out is not None:
        replies.append(OutboundMessage(msg.chat_id, out, expert_role))

_TOPIC_LOOKUP_PAIRS = (("选题", "看到"), ("选题", "还能看到"), ("选题", "能看见"), ("选题", "还能看见"),
                       ("选题", "看见"), ("选题", "还在"), ("选题", "记录"), ("选题", "找不回来"),
                       ("选题", "找不回"), ("选题", "丢了"), ("选题", "丢"), ("选题", "原始"),
                       ("选题", "回顾"), ("选题", "哪去了"), ("选题", "哪里"), ("选题", "还记得"),
                       ("选题", "想起来"), ("选题", "找不到"),
                       ("脚本", "还在"), ("脚本", "还能看到"), ("脚本", "哪去了"),
                       ("上次", "还能看到"), ("上次", "还能看见"), ("刚才", "还能看到"), ("刚才", "还能看见"))

def _lookup_topic_record(chat_id: str, content: str, rounds: list, replies: list, force: bool = False) -> str:
    # 老板问「刚才的选题还能看到么」这类：必须真读记录回执，不能闲聊承诺「我去核对」
    # force=True 时跳过关键词对（意图已判定为查记录，或来自小席【行动】立即动作）
    if not force and not any(a in content and b in content for a, b in _TOPIC_LOOKUP_PAIRS):
        return ""
    topics, _basis = context_store.load_basis(chat_id, "席小题")
    if topics:
        _log.info("查选题记录 → 真核对回执（选题存档）")
        return (f"刚才席小题出的选题记录，我核对到了（选题存档）：\n{topics}\n\n"
                "要排期 / 写脚本，或按某条继续，直接说就行。")
    speaker, prev = _last_expert_output(rounds)
    if speaker == "席小题" and prev:
        _log.info("查选题记录 → 真核对回执（会话记忆）")
        return (f"刚才席小题出的选题记录，我核对到了（会话记忆）：\n{prev}\n\n"
                "要排期 / 写脚本，或按某条继续，直接说就行。")
    if speaker and prev:
        _log.info("查选题记录 → 无选题存档，按上次方向派单席小题重出")
        out = _dispatch_expert(chat_id, replies, "席小题",
                               f"按上次方向重新整理一版选题，上次方向：\n{prev}", _recent_context(rounds))
        if out is not None:
            replies.append(OutboundMessage(chat_id, out, "席小题"))
        return "存档里没找到选题记录，我按上次方向让席小题重新整理一版了，马上就好。"
    _log.info("查选题记录 → 无记录，等老板方向")
    return ("我把记录核对了：选题存档（只留最近一版）和会话记忆里都没找到，这轮找不回来了。"
            "给我个方向（或直接说「重新出选题」），我马上派单。")

def _is_probe(text: str) -> bool:
    # 席小题拆分选题后发的探询句（"需要我给你看选题的依据么？"）不算产出，追问时不能当上一条内容
    return text.startswith("需要我") or "需要我给你看" in text


def _last_expert_output(rounds: list):
    for rnd in reversed(rounds):
        for turn in reversed(rnd):
            if turn["speaker"] in ("席小题", "席小文") and not _is_probe(turn["text"]):
                return turn["speaker"], turn["text"]
    return None, ""


def _last_any_output(rounds: list):
    # 追问/要理由可能指向审核结论：与 _last_expert_output 的区别是含席小核
    for rnd in reversed(rounds):
        for turn in reversed(rnd):
            if turn["speaker"] in ("席小题", "席小文", "席小核") and not _is_probe(turn["text"]):
                return turn["speaker"], turn["text"]
    return None, ""


def _show_review_reasons(chat_id: str, content: str, rounds: list) -> str:
    # 席小核刚审完（最近一条专家产出），老板回短确认词要理由 → 直发存档详情；详情缺失则现生成简洁理由
    if not any(w in content for w in _REASON_YES):
        return ""
    speaker, prev = _last_any_output(rounds)
    if speaker != "席小核" or not prev:
        return ""
    return _review_reasons(chat_id, rounds)


def _review_reasons(chat_id: str, rounds: list) -> str:
    # 优先直发存档的审核详情；详情缺失（模型省略详情段/存档超时被清）时现生成一份简洁理由，别让老板吃闭门羹
    details = context_store.load_review(chat_id, "席小核")
    if details:
        return f"【席小核】审核理由：\n{details}"
    speaker, prev = _last_any_output(rounds)
    if speaker != "席小核" or not prev:
        return ""
    ctx = f"你刚给出的审核结论：\n{_cap(prev)}"
    script = _last_script(rounds)
    if script:
        ctx += f"\n\n【被审脚本】\n{_cap(script, 800)}"
    try:
        reason = _controller_factories()["席小核"]().explain("老板要看你刚才那条审核的理由，用大白话给结论依据", ctx)
    except Exception as e:
        _log.error("重新生成审核理由失败: %s", e)
        return ""
    return f"【席小核】审核理由：\n{reason}"


def _learn_rules(msg: InboundMessage, content: str, rounds: list, replies: list) -> None:
    speaker, prev = _last_expert_output(rounds)
    if not prev:
        replies.append(OutboundMessage(msg.chat_id, "没有找到上一条产出可学习：先出选题或写脚本，再把修改稿发我并说「学一下」", "小席"))
        return
    _log.info("学规则 基于 %s 的上一条产出", speaker)
    out = _dispatch_expert(msg.chat_id, replies, "席小习", f"{LEARN_MARK}{content}",
                           f"【上一条产出 · {speaker}】\n{prev}")
    if out is not None:
        replies.append(OutboundMessage(msg.chat_id, out, "席小习"))

def _confirm_rules(msg: InboundMessage, content: str, replies: list) -> None:
    if not rules.has_pending():
        replies.append(OutboundMessage(msg.chat_id, "当前没有待确认的规则", "小席"))
        return
    reject = any(w in content for w in ("不用", "算了", "别学", "撤销", "否"))
    n = rules.confirm_pending(not reject)
    replies.append(OutboundMessage(msg.chat_id,
        f"已丢弃 {n} 条待确认规则" if reject else f"已确认 {n} 条规则，写入风格档案，下次产出生效", "小席"))

_ABORT_WORDS = ("算了", "不用了", "放弃", "先不管", "驳回")
# 老板在 wait_boss 下回「放行」→ 按当前脚本通过定稿，不落规则、不再派改稿（防把放行话术误当答案，导致越改越复审的死循环）
_PASS_HINTS = ("确定了", "可以下一步", "下一步", "就这么样", "就按这个来", "就这么定",
               "确认通过", "通过吧", "可以了", "行了", "好了", "继续", "不用改",
               "别改", "不用再改", "不纠结", "就这样", "可以吧", "就这个吧")
_PASS_EXACT = ("没问题", "OK", "好的", "可以")

def _is_pass(content: str) -> bool:
    c = content.strip()
    return any(p in c for p in _PASS_HINTS) or c in _PASS_EXACT

def _resolve_doubt(msg: InboundMessage, content: str, rounds: list, replies: list) -> None:
    # 席小核给疑问点暂停等老板拍板。老板三种选择：
    # ① 放行（可以了/继续/确定了…）→ 当前脚本按通过定稿，不落规则；② 取消（不用了/算了）→ 保持原样；
    # ③ 实质答案（有具体修改指令）→ 落已确认规则（席小核/席小文都学）→ 席小文按答案改 → 复审
    pipe = _pipelines.pop(msg.chat_id, None)
    if not pipe:
        return
    if any(w in content for w in _ABORT_WORDS):
        _post(msg.chat_id, replies, "小席", "已取消本次修改，脚本保持原样，要改随时说。")
        _remember(msg, replies)
        return
    if _is_pass(content):
        _record_passed(msg.chat_id)
        _post(msg.chat_id, replies, "小席",
              "收到，疑问不再纠结——当前脚本按通过定稿。要改随时说。")
        _remember(msg, replies)
        return
    answer = content.strip()
    _log.info("老板答复审核疑问，落规则 + 让席小文修改")
    try:
        rules.add_confirmed_rule(f"老板确认审核疑问（{_cap(pipe['reviewer'], 40)}）", answer)
    except Exception as e:
        _log.error("记录疑问答案规则失败: %s", e)
    first = pipe["reviewer"].strip().split(chr(10), 1)[0]
    fix_task = (f"审核未通过，按老板的确定答案修改这条脚本。\n老板答案：{answer}\n"
                f"审核意见：{first}\n直接输出改好的完整新版脚本，不用解释过程。")
    _dispatch_expert(msg.chat_id, replies, "席小文", fix_task, pipe["script"])
    _pipelines[msg.chat_id] = {"step": "wait_fix", "script": pipe["script"],
                               "reviewer": pipe["reviewer"], "fix_rounds": pipe.get("fix_rounds", 0), "ts": time.time()}
    _post(msg.chat_id, replies, "小席",
          f"收到老板答案：{_cap(answer, 60)}\n已让席小核记住、席小文按此修改，改完复审。")
    _remember(msg, replies)


def _admin_at_mentions() -> str:
    # 老板/产品 open_id → 群内 @ 拍板（boss 未配时用产品，产品与老板同级最高权限）
    ids = []
    try:
        roles = identity.load_roles()
        ids = list(roles.get("boss_open_ids", [])) + list(roles.get("product_open_ids", []))
    except Exception as e:
        _log.error("读取 admin open_id 失败: %s", e)
    ids = list(dict.fromkeys(ids))
    return " ".join(f'<at user_id="{oid}"></at>' for oid in ids)

def _reflow(msg: InboundMessage, content: str, replies: list) -> None:
    parsed = _parse_reflow_data(content)
    if parsed:
        date, topic, raw = parsed
        data = raw.replace("万", "w")
        if not record_data(date, topic, data):
            replies.append(OutboundMessage(msg.chat_id, f"没找到「{date} {topic}」的已发条目，确认下日期/选题", "小席"))
            return
        conclusion = _dispatch_expert(msg.chat_id, replies, "席小盘", f"日期 {date} 选题 {topic} 数据：{data}", "")
        if conclusion is None:
            _pipelines[msg.chat_id] = {"step": "wait_fupan", "date": date, "topic": topic, "data": data, "ts": time.time()}
            _post(msg.chat_id, replies, "小席", f"数据已回流：{date}「{topic}」 {data}，复盘分析中…")
            return
        _post(msg.chat_id, replies, "席小盘", f"数据已回流：{date}「{topic}」 {data}\n\n【席小盘】\n{conclusion}")
    else:
        rows = [r for r in load_schedule() if r["status"] == "已发"]
        if not rows:
            replies.append(OutboundMessage(msg.chat_id, "当前没有待回流数据（排期表里没有「已发」条目）", "小席"))
        else:
            lines = "\n".join(f"- {r['date']}「{r['topic']}」数据回流：{r['data']}" for r in rows)
            replies.append(OutboundMessage(msg.chat_id, f"以下条目已发待回流，报数据格式：日期 选题 播放x 留资y\n{lines}", "小席"))

def _refresh_market(msg: InboundMessage, replies: list) -> None:
    _log.info("更新市场情报")
    res = market.refresh()
    if res["hot"] or res["leads"]:
        detail = "、".join(f"{k} {v} 条" for k, v in (("热点", res["hot"]), ("竞对线索", res["leads"])) if v)
        _post(msg.chat_id, replies, "小席",
              f"市场情报已更新：{detail}。席小题下次出选题会自动带上。\n"
              "（实时搜索尽力而为；更全的每日热点靠雷达，跑完执行 scripts/sync_radar.py）")
    else:
        _post(msg.chat_id, replies, "小席",
              "实时搜索被风控/无结果，市场情报库保持上次数据。\n"
              "要更全的每日热点，跑雷达后执行 scripts/sync_radar.py 同步。")

def _parse_reflow_data(content: str):
    m = re.search(r"(\d{1,2}/\d{1,2})\s*([^播放留资点赞转发观看]+)\s*(.+)$", content)
    if not m:
        return None
    date, topic, data = m.group(1), m.group(2).strip(), m.group(3).strip()
    if not topic or not data:
        return None
    return date, topic, data

def _topic_task(params: dict) -> str:
    count = params.get("count") or "3"
    topic = (params.get("topic") or params.get("content_type") or "").strip()
    return f"出{count}个没拍过的选题" + (f"，角度：{topic}" if topic else "")

def _schedule_entries(params: dict, content: str = "") -> list[dict]:
    count = int((params.get("count") or "3").strip("个")) or 3
    ctype = params.get("content_type") or "曝光"
    date = _resolve_date(params.get("date") or _extract_date(content) or "下周")
    publish_time = _resolve_time((params.get("publish_time") or _extract_time(content) or "").strip())
    return [
        {"date": date, "publish_time": publish_time, "content_type": ctype, "topic": f"{ctype}选题{i+1}",
         "goal": "拉新", "status": "待产", "owner": "席小文", "data": "—"}
        for i in range(count)
    ]


def _resolve_date(date_str: str) -> str:
    d = (date_str or "").strip()
    m = re.match(r"(\d{1,2})/(\d{1,2})", d)
    if m:
        return f"{int(m.group(1))}/{int(m.group(2))}"
    today = datetime.date.today()
    if d == "今天":
        return f"{today.month}/{today.day}"
    if d == "明天":
        t = today + datetime.timedelta(days=1)
        return f"{t.month}/{t.day}"
    for kw, wd in (("下周一", 0), ("下周二", 1), ("下周三", 2), ("下周四", 3),
                   ("下周五", 4), ("下周六", 5), ("下周日", 6)):
        if kw in d:
            return _fmt(_next_weekday(today, wd))
    if "下周" in d or "下星期" in d:
        return _fmt(_next_weekday(today, 0))
    for kw, wd in (("周三", 2), ("周一", 0), ("周二", 1), ("周四", 3), ("周五", 4)):
        if kw in d:
            return _fmt(_next_weekday(today, wd))
    return f"{today.month}/{today.day}"


def _next_weekday(day: datetime.date, weekday: int) -> datetime.date:
    days_ahead = weekday - day.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return day + datetime.timedelta(days=days_ahead)


def _fmt(d: datetime.date) -> str:
    return f"{d.month}/{d.day}"


def _extract_date(text: str) -> str:
    m = re.search(r"(\d{1,2})/(\d{1,2})", text or "")
    return m.group(0) if m else ""


def _resolve_time(text: str) -> str:
    m = re.search(r"(\d{1,2})[:：](\d{2})", text or "")
    if m:
        return f"{int(m.group(1))}:{m.group(2)}"
    return "12:00"


def _extract_time(text: str) -> str:
    m = re.search(r"(\d{1,2})[:：](\d{2})", text or "")
    return m.group(0) if m else ""


_PICK_HINTS = ("排上", "排一下", "排期", "定了", "选这个", "第")
_PASSED_HINTS = ("通过了", "排期发布", "发布", "上传", "这条", "这个")

def _try_pick_topic(content: str, params: dict, rounds: list, chat_id: str = "") -> dict | None:
    # ① 老板确认刚定稿的脚本选题（「这条选题通过了/排期发布/上传」）→ 用刚定稿的选题+类型
    if chat_id and chat_id in _last_passed and any(w in content for w in _PASSED_HINTS):
        lp = _last_passed[chat_id]
        if lp.get("topic"):
            return {"date": _resolve_date(params.get("date") or _extract_date(content) or "下周"),
                    "publish_time": _resolve_time((params.get("publish_time") or _extract_time(content) or "").strip()),
                    "content_type": lp["content_type"], "topic": lp["topic"], "goal": "拉新",
                    "status": "待产", "owner": "席小文", "data": "—"}
    if not any(w in content for w in _PICK_HINTS):
        return None
    speaker, prev = _last_expert_output(rounds)
    if not prev or speaker != "席小题":
        return None
    m = re.search(r"第([\d一二两三四五六七八九十]+)[条个]", content)
    item = _pick_item(prev, _to_int(m.group(1))) if m else ""
    if not item:
        item = prev.split("\n", 1)[0].strip()
    topic = re.sub(r"^\d+[.、）)]\s*", "", item)
    topic = re.sub(r"^【.*?】", "", topic).strip()
    topic = topic.split("（")[0].strip()
    if not topic:
        return None
    return {"date": _resolve_date(params.get("date") or _extract_date(content) or "下周"),
            "publish_time": _resolve_time((params.get("publish_time") or _extract_time(content) or "").strip()),
            "content_type": _type_from_line(item), "topic": topic, "goal": "拉新",
            "status": "待产", "owner": "席小文", "data": "—"}


_base_url = ""

def set_base_url(url: str) -> None:
    global _base_url
    _base_url = url

def _base_link() -> str:
    return f"\n多维表格：{_base_url}" if _base_url else ""

def _is_admin(role: str) -> bool:
    return role == "老板" or role == "产品"

def _try_mark_published(content: str) -> str:
    parts = content.replace("已发布", "").strip()
    m = re.match(r"(\d{1,2}/\d{1,2})\s*(.*)", parts)
    if m and m.group(2).strip():
        date, topic = m.group(1), m.group(2).strip()
        return "确认" if mark_published(date, topic) else "未找到对应待发条目"
    return "格式不对，示例：8/3 普娃逆袭 已发布"
