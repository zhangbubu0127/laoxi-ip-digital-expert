import datetime, re, time
from log import get_logger
from pipe import InboundMessage, OutboundMessage
from brain.circuit import CircuitBreaker
from brain.intent import recognize, by_keyword, IntentResult
from brain.session import store, render_history
from brain import material, rules
from brain.scheduler import mark_published, render_schedule, add_entry, load_schedule, record_data
from brain.experts.xiaoti import XiaotiExpert
from brain.experts.xiaowen import XiaowenExpert
from brain.experts.xiaone import XiaoneExpert
from brain.experts.xiaoxi import XiaoxiExpert
from brain.experts.fupan import FupanExpert
from brain.experts.dispatch import run_expert, EXPLAIN_MARK, LEARN_MARK
from brain import context_store
from skin import bots

_b = CircuitBreaker()
_log = get_logger("controller")
_ADMIN_ONLY = ("出选题", "写脚本", "改排期", "看排期表", "反馈修改", "追问", "记素材", "看完整讨论", "圆桌讨论", "学规则", "确认规则", "数据回流")
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
        "你是小席，老席留学IP系统的主控调度——老板的左右手。\n"
        "老板这句没被识别成明确派单/操作指令，别摆架子念菜单，先像真人一样接住情绪：\n"
        "第一段：共情老板的话（安慰、表示理解、说点暖心的），像真的关心他；\n"
        "第二段：绕回工作——从他这句话里抓一个能做的角度，主动提议具体下一步（如：要不要让席小题从这个角度出个选题／席小文写条脚本／席小核审一遍）。\n"
        "直接给最终答复，不要输出思考过程。\n"
        "硬约束：不编造事实，不假装执行了任何操作；简洁，120字以内。"
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
    replies.append(OutboundMessage(msg.chat_id, text.strip() or "需要我做什么？出选题/写脚本/看排期/讨论？", "小席"))

_PIPE_TIMEOUT = 180.0
_MAX_FIX_ROUNDS = 2  # 席小核审核不通过时，自动 @席小文 修改的轮次上限，超了交给老板
_pipelines = {}  # chat_id -> 异步编排状态（写脚本/圆桌/数据回流）

def _controller_factories() -> dict:
    return {
        "席小题": XiaotiExpert, "席小文": XiaowenExpert, "席小核": XiaoneExpert,
        "席小习": XiaoxiExpert, "席小盘": FupanExpert,
    }

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
    if pipe is None:
        return replies
    role = msg.sender_role
    step = pipe.get("step")
    if step == "wait_wen" and role == "席小文":
        _log.info("写脚本流水线 wait_wen→wait_he")
        he = _dispatch_expert(msg.chat_id, replies, "席小核", f"审核这条脚本：\n{msg.content}", "")
        if he is None:
            _pipelines[msg.chat_id] = {"step": "wait_he", "script": msg.content, "fix_rounds": 0, "ts": time.time()}
        else:
            _post(msg.chat_id, replies, "席小核", he)
            _pipelines.pop(msg.chat_id, None)
            _post(msg.chat_id, replies, "小席", "写脚本流水线完成：席小文出稿 + 席小核审核已展示，要改直接说。")
    elif step == "wait_he" and role == "席小核":
        rounds = pipe.get("fix_rounds", 0)
        if _verdict_needs_change(msg.content) and rounds < _MAX_FIX_ROUNDS:
            _log.info("写脚本流水线 审核未过→席小文修改（第%d轮）", rounds + 1)
            _dispatch_expert(msg.chat_id, replies, "席小文",
                             f"审核未通过，修改这条脚本：{msg.content}\n直接输出改好的完整新版脚本，不用解释过程。",
                             pipe["script"])
            _pipelines[msg.chat_id] = {"step": "wait_fix", "script": pipe["script"],
                                       "reviewer": msg.content, "fix_rounds": rounds + 1, "ts": time.time()}
            _post(msg.chat_id, replies, "小席",
                  f"席小核审核未通过：{_cap(msg.content)}\n已让席小文按意见修改，改完再复审。")
        else:
            _pipelines.pop(msg.chat_id, None)
            if _verdict_needs_change(msg.content):
                _post(msg.chat_id, replies, "小席",
                      f"席小核复审仍不过（已改 {rounds} 轮）：{_cap(msg.content)}\n请老板定夺，或手动 @席小文 继续改。")
            else:
                _post(msg.chat_id, replies, "小席", "写脚本流水线完成：席小文出稿 + 席小核审核通过，要改直接说。")
    elif step == "wait_fix" and role == "席小文":
        _log.info("写脚本流水线 wait_fix→wait_he（复审）")
        he = _dispatch_expert(msg.chat_id, replies, "席小核", f"审核这条脚本：\n{msg.content}", "")
        if he is None:
            _pipelines[msg.chat_id] = {"step": "wait_he", "script": msg.content,
                                       "fix_rounds": pipe.get("fix_rounds", 0), "ts": time.time()}
        else:
            _pipelines.pop(msg.chat_id, None)
            _post(msg.chat_id, replies, "席小核", he)
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
        _log.info("派单 席小文 + 席小核")
        _post(msg.chat_id, replies, "小席", "写脚本进行中：席小文写作 → 席小核审核，两段约30-60秒…")
        wen = _dispatch_expert(msg.chat_id, replies, "席小文", (result.task or "").strip() or content, _recent_context(rounds))
        if wen is None:
            _pipelines[msg.chat_id] = {"step": "wait_wen", "content": content, "ts": time.time()}
        else:
            _post(msg.chat_id, replies, "席小文", wen)
            he = _dispatch_expert(msg.chat_id, replies, "席小核", wen, "")
            if he is None:
                _pipelines[msg.chat_id] = {"step": "wait_he", "script": wen, "ts": time.time()}
            else:
                _post(msg.chat_id, replies, "席小核", he)
                _post(msg.chat_id, replies, "小席", "写脚本流水线完成：席小文出稿 + 席小核审核已展示，要改直接说。")
    elif intent == "看排期表":
        replies.append(OutboundMessage(msg.chat_id, "当前排期表：\n" + render_schedule() + _base_link(), "小席"))
    elif intent == "改排期":
        picked = _try_pick_topic(content, result.params, rounds)
        if picked is not None:
            add_entry(picked)
            replies.append(OutboundMessage(msg.chat_id,
                f"已排上：{picked['date']} {picked['publish_time']} 发「{picked['topic']}」\n" + render_schedule() + _base_link(), "小席"))
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
    else:
        target = next((n for n in msg.mention_names if n in _EXPERT_ROLES), "")
        if target:
            _log.info("老板直接 @%s 闲聊，派单该角色自然接话", target)
            out = _dispatch_expert(msg.chat_id, replies, target,
                                   f"老板在群里直接找你聊：{content}\n"
                                   "先自然共情接住老板的话，别摆架子；然后绕回你的本职工作，主动建议一个具体下一步。",
                                   _recent_context(rounds))
            if out is not None:
                replies.append(OutboundMessage(msg.chat_id, out, target))
        else:
            _chat_reply(msg, content, rounds, replies)
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

def _verdict_needs_change(text: str) -> bool:
    return any(mark in text for mark in ("❌", "需改", "红线", "黄线"))

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
    speaker, prev = _last_expert_output(rounds)
    if not prev:
        replies.append(OutboundMessage(msg.chat_id, "没有找到可追问的上一条产出", "小席"))
        return
    expert_role = speaker if speaker in ("席小题", "席小文") else "席小文"
    _log.info("追问 派单 %s", speaker)
    out = _dispatch_expert(msg.chat_id, replies, expert_role, f"{EXPLAIN_MARK}{content}",
                           f"【你上次的产出 · {speaker}】\n{prev}")
    if out is not None:
        replies.append(OutboundMessage(msg.chat_id, out, expert_role))

def _last_expert_output(rounds: list):
    for rnd in reversed(rounds):
        for turn in reversed(rnd):
            if turn["speaker"] in ("席小题", "席小文"):
                return turn["speaker"], turn["text"]
    return None, ""

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

def _try_pick_topic(content: str, params: dict, rounds: list) -> dict | None:
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
            "content_type": "曝光", "topic": topic, "goal": "拉新",
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
