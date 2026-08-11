import datetime, json, re, time
from log import get_logger
from pipe import InboundMessage, OutboundMessage
from brain.circuit import CircuitBreaker
from brain.intent import by_keyword, WRITE_SCRIPT_HINTS
from brain.planner import PlanResult, plan as _planner_call
from brain.session import store, render_history
from brain import material, rules
from brain.knowledge import style_users
from brain.scheduler import mark_published, render_schedule, add_entry, load_schedule, record_data, confirm_publish
from brain.used_topics import load_used, add_used
from brain.experts.xiaoti import XiaotiExpert
from brain.experts.xiaowen import XiaowenExpert
from brain.experts.xiaone import XiaoneExpert, split_review
from brain.experts.xiaoxi import XiaoxiExpert
from brain.experts.fupan import FupanExpert
from brain.experts.dispatch import run_expert, EXPLAIN_MARK, LEARN_MARK, parse_learned_rules
from brain import basis as basis_util
from brain import context_store, market
from skin import bots, identity, base_bridge

_b = CircuitBreaker()
_log = get_logger("controller")
_EXPERT_ROLES = ("席小题", "席小文", "席小核", "席小习", "席小盘")
_USED_HINT = "出题时会优先避开/降权已用选题；想看历史用过的选题，跟我说「看已用选题」。"

# 动作级权限（替代旧的意图级 _ADMIN_ONLY）：写操作仅老板/产品，用户只读
_PERMISSION_ADMIN = frozenset((
    "出选题", "写脚本", "审核脚本", "改排期", "改表格结构", "记素材", "圆桌讨论",
    "学规则", "确认规则", "数据回流", "更新市场情报", "改审核看法",
))
_PERMISSION_USER_READONLY = frozenset((
    "看排期表", "看完整讨论", "查记录", "查已用选题", "反馈修改", "追问", "要审核理由",
))
# 第三角色桶：确认已发布（发片同事可做）、确认排期/专家闲聊（任何人）
_THIRD_BUCKET = frozenset(("确认已发布", "确认排期", "专家闲聊"))

# LLM 规划器崩溃时关键词兜底：by_keyword 意图名 → planner 动作名（大多同名）
_INTENT_TO_ACTION = {
    "出选题": "出选题", "写脚本": "写脚本", "审核脚本": "审核脚本", "看排期表": "看排期表",
    "改排期": "改排期", "改表格结构": "改表格结构", "确认已发布": "确认已发布", "确认排期": "确认排期",
    "反馈修改": "反馈修改", "追问": "追问", "记素材": "记素材", "看完整讨论": "看完整讨论",
    "查记录": "查记录", "查已用选题": "查已用选题", "圆桌讨论": "圆桌讨论", "学规则": "学规则",
    "确认规则": "确认规则", "数据回流": "数据回流", "更新市场情报": "更新市场情报",
    "调研": "调研",
}

def _fallback_planner(msg: InboundMessage, content: str, history_text: str, rounds: list) -> PlanResult | None:
    # planner LLM 崩溃时降级：关键词兜底（保高信号指令不静默）；再兜不住返回 None（走老对话模板）
    try:
        return _planner_call(content, history_text=history_text,
                             mention_names=list(msg.mention_names),
                             step=_pipelines.get(msg.chat_id, {}).get("step", ""),
                             review_head=_review_head(msg.chat_id))
    except Exception as e:
        _log.error("规划器调用失败，降级关键词路由: %s", e)
    result = by_keyword(content)
    action = _INTENT_TO_ACTION.get(result.intent)
    if action:
        return PlanResult(reply="", action=action, task=content, params=result.params)
    return None

_planner = _fallback_planner
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
        "- 看排期表（立即）：直接把多维表格链接给出来，不渲染整张表。\n"
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
    action = _ACTION_ALIASES.get(action, action)
    if action not in _ACTION_EXECUTORS:
        _log.info("行动未登记：未知动作 %s", action)
        return
    if action == "出选题":
        task = task or "重新出一版选题"
    _pending_action[chat_id] = {"action": action, "task": task, "ts": time.time()}
    _log.info("登记待定动作 chat=%s action=%s task=%r", chat_id, action, task)

def _run_immediate(chat_id: str, action: str, task: str, rounds: list, replies: list) -> None:
    """小席承诺当场兑现的动作：真去读记录/排期，把回执补进回复，不空头承诺。"""
    if action in ("核对选题记录", "核对记录"):
        reply = _lookup_topic_record(chat_id, "核对选题记录", rounds, replies, force=True)
        if reply:
            replies.append(OutboundMessage(chat_id, reply, "小席"))
    elif action in ("看排期表",):
        replies.append(OutboundMessage(chat_id, _schedule_reply(), "小席"))

def _register_expert_proposal(chat_id: str, text: str) -> None:
    """专家回复里带【动作名】的建议下一步 → 登记待确认动作，老板点头即执行。"""
    m = _EXPERT_ACTION_RE.search(text or "")
    if not m:
        return
    action = _ACTION_ALIASES.get(m.group(1), m.group(1))
    if action not in _ACTION_EXECUTORS:
        return
    line = next((l for l in text.split("\n") if m.group(0) in l), "")
    task = line.split(m.group(0), 1)[-1].strip(" ？?！!。，,；;")
    if action == "出选题":
        task = task or "重新出一版选题"
    _pending_action[chat_id] = {"action": action, "task": task, "ts": time.time()}
    _log.info("专家提议登记 chat=%s action=%s task=%r", chat_id, action, task)

def _strip_action_lines(text: str) -> str:
    return _ACTION_RE.sub("", text or "").strip()

_PROPOSAL_RE = re.compile(r"【提议】\s*([^：:]+?)\s*[：:]\s*(.*)")
_PROPOSAL_ACTIONS = (("重新出选题", "出选题"), ("重新整理", "出选题"), ("重新出一版", "出选题"),
                     ("出个选题", "出选题"), ("出条选题", "出选题"), ("出选题", "出选题"),
                     ("写条脚本", "写脚本"), ("写条", "写脚本"), ("写脚本", "写脚本"), ("出文案", "写脚本"), ("文案", "写脚本"),
                     ("审核", "审核脚本"), ("审一遍", "审核脚本"), ("复审", "审核脚本"),
                     ("看排期表", "看排期表"), ("排期表", "看排期表"))

# 「说话即行动」契约：小席聊天回复末尾的机器可执行行动行（行首锚定，避免正文误配）
_ACTION_RE = re.compile(r"^【行动】\s*(\{.*\})\s*$", re.M)
_ACTION_ALIASES = {
    "重新出选题": "出选题", "重新整理选题": "出选题", "重新出一版": "出选题",
    "写条脚本": "写脚本", "出条脚本": "写脚本", "出文案": "写脚本",
    "审核": "审核脚本", "审一遍": "审核脚本", "复审": "审核脚本",
}
# 专家回复里建议下一步用的可读标记：把动作名用【】括进建议句，主控识别后登记待确认动作，老板点头即执行
_EXPERT_ACTION_RE = re.compile(r"【(重新出选题|重新整理选题|重新出一版|出选题|写脚本|写条脚本|出条脚本|审核脚本|审核|看排期表)】")

def _register_proposal(chat_id: str, text: str) -> None:
    m = _PROPOSAL_RE.search(text or "")
    if not m:
        return
    action, detail = m.group(1).strip(), m.group(2).strip()
    for kw, action_name in _PROPOSAL_ACTIONS:
        if kw in action:
            task = detail
            if action_name == "出选题":
                task = task or "重新出一版选题"
            _pending_action[chat_id] = {"action": action_name, "task": task, "ts": time.time()}
            _log.info("登记待定动作 chat=%s action=%s task=%r", chat_id, action_name, task)
            return

def _strip_proposal_marker(text: str) -> str:
    return re.sub(r"【提议】.*", "", text or "").strip()

_STYLE_REQ_RE = re.compile(r"(?:按|用)\s*([^的，。:：、\s]{1,10})\s*的风格")

def _requested_style(text: str) -> str:
    # 老板在写脚本指令里直接点名风格（「按张艺宝的风格写」）→ 命中即用，省一次反问
    m = _STYLE_REQ_RE.search(text or "")
    if not m:
        return ""
    name = m.group(1).strip()
    return name if name in style_users() else ""

def _write_script(msg: InboundMessage, content: str, task: str, rounds: list, replies: list, params: dict | None = None) -> None:
    params = params or {}
    if msg.sender_open_id:
        _requester_by_chat[msg.chat_id] = msg.sender_open_id
    users = style_users()
    explicit = _requested_style(content) or _requested_style(task)
    if explicit in users:
        _last_style[msg.chat_id] = explicit
    elif len(users) > 1 and _last_style.get(msg.chat_id) not in users:
        # 多个风格库且本次没指定 → 问老板用谁的，等答复后再落笔（绝不默认瞎猜）
        _pipelines[msg.chat_id] = {"step": "wait_style", "content": content, "task": task,
                                   "params": params, "ts": time.time()}
        _post(msg.chat_id, replies, "小席",
              f"这次写脚本按谁的风格来？当前可用：{'/'.join(users)}。回一个人名即可（默认老席）。")
        return
    topic, ctype = _pipe_ctx(msg.chat_id, content, rounds, task)
    if topic:
        _draft_ctx[msg.chat_id] = (topic, ctype)
    task = _resolve_script_task(task, content, rounds, msg.chat_id)
    task = _apply_script_count(task, content, params)
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

def _resolve_style(msg: InboundMessage, content: str, rounds: list, replies: list) -> None:
    # 老板回答「按谁风格写」：登记该风格 → 用原请求重新派单
    pipe = _pipelines.get(msg.chat_id)
    if not pipe or pipe.get("step") != "wait_style":
        return
    matched = next((u for u in style_users() if u in content), "")
    if not matched:
        _post(msg.chat_id, replies, "小席",
              f"没认出这个风格，当前可用：{'/'.join(style_users())}。回一个人名（如「老席」）。")
        return
    _pipelines.pop(msg.chat_id, None)
    _last_style[msg.chat_id] = matched
    _post(msg.chat_id, replies, "小席", f"好，这次按「{matched}」的风格写。")
    _write_script(msg, pipe.get("content", ""), pipe.get("task", ""), rounds, replies, pipe.get("params"))

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


def _apply_script_count(task: str, content: str, params: dict) -> str:
    # 意图层提取出的 count（如「出两个脚本」的 2）要写进派单任务，否则席小文只按「写这条」出一稿
    count = params.get("count")
    if not count:
        return task
    n = _to_int(str(count))
    if n <= 1 or re.search(rf"{n}\s*条", task or ""):
        return task
    base = (task or content).strip()
    return (f"{base}。本批共 {n} 条脚本，围绕该选题写出 {n} 条不同角度/版本的脚本，"
            f"逐条完整输出，每条以## 脚本 标题 开头")


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
_pending_action = {}  # chat_id -> {"action", "task", "ts"}：小席提议待老板点头后真执行的动作
_last_style = {}  # chat_id -> 风格用户：最近一次写脚本用的说话风格（多个风格库时小席先问，之后复用）
_requester_by_chat = {}  # chat_id -> 真人 open_id：发起写脚本流水线的人，定稿/审核疑问时针对性 @ 他，不泛问
_used_cursor = {}  # chat_id -> 已用选题分页已展示到的下标：查已用选题时按 10 条一页续发
_pending_reflow = {}  # chat_id -> {"date","topic","data","ts"}：报数据时无「已发」条目先暂存，等确认已发布自动回流
_last_marked = None  # (date, topic)：最近一次人工确认「已发布」成功的排期行，供回流/连动定位
_PENDING_REFLOW_TTL = 7200  # 秒；暂存数据只在本窗口内等「已发布」确认，防数据串台到别的视频

def _controller_factories() -> dict:
    return {
        "席小题": XiaotiExpert, "席小文": XiaowenExpert, "席小核": XiaoneExpert,
        "席小习": XiaoxiExpert, "席小盘": FupanExpert,
    }

_SCRIPT_HEADER_RE = re.compile(r"^(?:【席小文】)?\s*#+\s*脚本(?:\s*v\d+)?\s*[：:]\s*(.*)$", re.M)

def _is_script_output(text: str) -> bool:
    # 脚本以「## 脚本 vX：标题」开头（历史数据可带【席小文】前缀，均识别为正式出稿）
    return bool(_SCRIPT_HEADER_RE.search(text or ""))

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
    # 写作/学习按该聊天的风格用户随行派单：非老席时在任务开头带风格指令，专家bot据此换风格/分库学习
    if role in ("席小文", "席小习"):
        su = _last_style.get(chat_id, "老席")
        if su != "老席":
            task = f"【风格:{su}】{task}"
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
    # 任意席小核产出都覆盖审核详情存档（不只 wait_he 态），保证「要理由」读到的始终是最近一次审核，而非旧存档
    if msg.sender_role == "席小核":
        _pub, _det = split_review(msg.content)
        if _det:
            context_store.save_review(msg.chat_id, "席小核", _det)
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
                  f"{_requester_mention(msg.chat_id) or _admin_at_mentions()}席小核审核有疑问，需要您定夺：\n{_cap(msg.content, 240)}\n"
                  "给个确定答案（例：费用按8万没问题 / 这句删掉），我会让席小核记住、席小文按答案修改再复审；\n"
                  "回「可以了/继续」＝按当前脚本通过定稿；回「不用了」＝取消本次修改。")
        else:
            _record_passed(msg.chat_id)
            _ask_schedule(msg.chat_id, pipe["script"], replies)
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
    elif step == "wait_topic" and role == "席小题" and not _is_probe(msg.content):
        # 席小题产出回来：pop 掉等待态、补发已用选题提醒（探询句不算产出，等真正选题正文）
        _pipelines.pop(msg.chat_id, None)
        _register_expert_proposal(msg.chat_id, msg.content)
        _post(msg.chat_id, replies, "小席", _USED_HINT)
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
        if p.get("step") == "wait_schedule":
            # 排期询问超时：脚本已交付，不催，安静撤掉等待态，随时可再排
            _post(cid, replies, "小席", "上排期的事先放着，要排随时说一声（给我日期时间就行）。")
            continue
        if p.get("step") == "wait_topic":
            # 出题产出超时：席小题已自发群展示选题，不补提醒，安静撤掉等待态
            continue
        if p.get("step") == "wait_style":
            # 风格询问超时：不催，安静撤掉，下次写脚本默认老席（要指定直接说「按XX的风格写」）
            _post(cid, replies, "小席", "选风格的事先放着，下次写脚本我按默认来，要指定直接说「按XX的风格写」。")
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

_USED_PAGE_SIZE = 10
_USED_MORE = "继续发就说「继续」，够了就说「不用了」。"
_USED_CONTINUE_WORDS = ("继续", "接着", "再来", "还有", "下一页")
_USED_STOP_WORDS = ("不用", "够了", "停", "不看了", "好了", "可以了", "结束")

def _send_used_page(chat_id: str, replies: list, start: int) -> None:
    used = load_used()
    if not used:
        replies.append(OutboundMessage(chat_id, "已用选题库还是空的——还没有选题被排上排期表。", "小席"))
        _used_cursor.pop(chat_id, None)
        return
    page = used[start:start + _USED_PAGE_SIZE]
    lines = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(page, start=start))
    total = len(used)
    n = start + len(page)
    body = f"已用选题（共{total}条，第{start // _USED_PAGE_SIZE + 1}页）：\n{lines}"
    if n < total:
        body += f"\n{_USED_MORE}"
        _used_cursor[chat_id] = n
    else:
        body += "\n已全部展示已选用选题。"
        _used_cursor.pop(chat_id, None)
    replies.append(OutboundMessage(chat_id, body, "小席"))

def _review_head(chat_id: str) -> str:
    # 传给规划器的最近一次席小核审核首行，供判断「老板是不是在纠正审核看法」用；无存档则空
    details = context_store.load_review(chat_id, "席小核")
    if not details:
        return ""
    return details.strip().split("\n", 1)[0][:120]

def _exec_topics(msg: InboundMessage, content: str, task: str, rounds: list, replies: list, params: dict | None = None) -> None:
    params = params or {}
    if not (task or params.get("count") or params.get("topic")):
        replies.append(OutboundMessage(msg.chat_id, "出选题可以。想让我出几个、从哪个角度出？（比如：3个，预算对比）", "小席"))
        return
    _log.info("派单 席小题")
    t = (task or "").strip() or _topic_task(params)
    out = _dispatch_expert(msg.chat_id, replies, "席小题", t, _xiaoti_context(content, rounds, msg.chat_id))
    if out is not None:
        replies.append(OutboundMessage(msg.chat_id, out, "席小题"))
        _post(msg.chat_id, replies, "小席", _USED_HINT)
    else:
        _pipelines[msg.chat_id] = {"step": "wait_topic", "ts": time.time()}

def _exec_research(msg: InboundMessage, content: str, task: str, rounds: list, replies: list, params: dict | None = None) -> None:
    # 调研：派席小题用实时搜索调研，任务带【调研】前缀走 research 分支（不走选题 prompt）
    params = params or {}
    q = (task or "").strip() or (params.get("topic") or "").strip()
    if not q:
        replies.append(OutboundMessage(msg.chat_id, "调研可以。要调研什么？说具体点（比如「调研一下2026新加坡低龄留学政策」）。", "小席"))
        return
    _log.info("派单 席小题 调研")
    out = _dispatch_expert(msg.chat_id, replies, "席小题", f"【调研】{q}")
    if out is not None:
        replies.append(OutboundMessage(msg.chat_id, out, "席小题"))

def _exec_view_schedule(msg: InboundMessage, content: str, task: str, rounds: list, replies: list, params: dict | None = None) -> None:
    replies.append(OutboundMessage(msg.chat_id, _schedule_reply(), "小席"))

def _exec_used_topics(msg: InboundMessage, content: str, task: str, rounds: list, replies: list, params: dict | None = None) -> None:
    _send_used_page(msg.chat_id, replies, 0)

def _exec_change_schedule(msg: InboundMessage, content: str, task: str, rounds: list, replies: list, params: dict | None = None) -> None:
    params = params or {}
    picked = _try_pick_topic(content, params, rounds, msg.chat_id)
    if picked is not None:
        add_entry(picked)
        add_used(picked["topic"])
        replies.append(OutboundMessage(msg.chat_id,
            f"已排上：{picked['date']} {picked['publish_time']} 发「{picked['topic']}」"
            f"（类型：{picked['content_type']}，负责人：{picked['owner']}）" + _base_link(), "小席"))
    elif any(w in content for w in _PASSED_HINTS):
        # 老板确认「这条选题/排期发布」但没追溯到刚定稿的选题（如进程重启）→ 明说失败，绝不生成占位
        replies.append(OutboundMessage(msg.chat_id,
            "没找到刚定稿的选题（可能刚重启或脚本还没走完审核）。请直接说「排上第1个」指定具体选题，或重新写一条脚本。", "小席"))
    else:
        # 空参数 + 内容里没有明确排期信息 → 绝不编占位行，问老板要具体排期
        explicit = any(params.get(k) for k in ("count", "date", "publish_time", "topic"))
        if not explicit and not re.search(
                r"(今天|明天|下周|下星期|星期|周[一二三四五六日]|\d{1,2}/\d{1,2}|个\s*(曝光|信任|留资)|排期)", content):
            replies.append(OutboundMessage(msg.chat_id,
                "你要排哪条选题、排哪天几点？说清楚（如「把第1个排到下周三 12:00」），我马上排。", "小席"))
        else:
            entries = _schedule_entries(params, content)
            if not entries:
                # 有排期意图但没数量/选题（如只给「排8/8 18:15 负责人张艺宝」而无刚定稿上下文）→ 明说失败，绝不编占位
                replies.append(OutboundMessage(msg.chat_id,
                    "没指定排哪条选题或排几条。说清楚（如「把第1个排到下周三 12:00」或「排3条曝光」），我马上排。", "小席"))
            else:
                for e in entries:
                    add_entry(e)
                replies.append(OutboundMessage(msg.chat_id, f"已排{len(entries)}条：" + _base_link(), "小席"))

def _exec_table_column(msg: InboundMessage, content: str, task: str, rounds: list, replies: list, params: dict | None = None) -> None:
    _log.info("改表格结构 → 加列")
    replies.append(OutboundMessage(msg.chat_id, _add_table_column(content), "小席"))

def _exec_record_material(msg: InboundMessage, content: str, task: str, rounds: list, replies: list, params: dict | None = None) -> None:
    _log.info("记素材入库")
    replies.append(OutboundMessage(msg.chat_id, _record_material(content), "小席"))

def _exec_history(msg: InboundMessage, content: str, task: str, rounds: list, replies: list, params: dict | None = None) -> None:
    hist = _history_context(msg.chat_id)
    replies.append(OutboundMessage(msg.chat_id,
                                   "最近群讨论：\n" + hist if hist else "完整讨论拉取未接入或暂无",
                                   "小席"))

def _exec_lookup_record(msg: InboundMessage, content: str, task: str, rounds: list, replies: list, params: dict | None = None) -> None:
    _log.info("查记录 → 真核对回执")
    record_reply = _lookup_topic_record(msg.chat_id, content, rounds, replies, force=True)
    if record_reply:
        replies.append(OutboundMessage(msg.chat_id, record_reply, "小席"))

def _exec_review_reasons(msg: InboundMessage, content: str, task: str, rounds: list, replies: list, params: dict | None = None) -> None:
    _log.info("要审核理由 → 直发存档详情")
    reason = _show_review_reasons(msg.chat_id, content, rounds)
    if reason:
        replies.append(OutboundMessage(msg.chat_id, reason, "席小核"))

def _exec_mark_published(msg: InboundMessage, content: str, task: str, rounds: list, replies: list, params: dict | None = None) -> None:
    params = params or {}
    ok = _try_mark_published(content, params, msg.chat_id)
    replies.append(OutboundMessage(msg.chat_id, f"已发状态：{ok}", "小席"))
    if ok != "确认" or _last_marked is None:
        return
    row = next((r for r in load_schedule()
                if r["date"] == _last_marked[0] and r["topic"] == _last_marked[1]), None)
    if row is None:
        return
    # 连动：同一条消息/参数带数据 → 标完已发直接回流
    data_str = params.get("data") or _extract_metrics(content)
    if data_str:
        data = data_str.replace("万", "w")
        if record_data(row["date"], row["topic"], data):
            replies.append(OutboundMessage(msg.chat_id,
                f"数据已回流：{row['date']}「{row['topic']}」 {data}", "小席"))
        return
    # 之前报过数据但没对上已发行 → 暂存数据现在补回流
    pending = _take_pending(msg.chat_id, row)
    if pending:
        data = pending["data"].replace("万", "w")
        if record_data(row["date"], row["topic"], data):
            replies.append(OutboundMessage(msg.chat_id,
                f"数据已回流：{row['date']}「{row['topic']}」 {data}（用之前报的数据）", "小席"))

def _exec_confirm_publish(msg: InboundMessage, content: str, task: str, rounds: list, replies: list, params: dict | None = None) -> None:
    ok = confirm_publish(msg.chat_id)
    replies.append(OutboundMessage(msg.chat_id,
                                   "已确认，这条排期不再提醒。" if ok else "没有需要确认的发布提醒。", "小席"))

def _exec_expert_chat(msg: InboundMessage, content: str, task: str, rounds: list, replies: list, params: dict | None = None) -> None:
    target = task or next((n for n in msg.mention_names if n in _EXPERT_ROLES), "")
    if not target:
        return
    _log.info("老板直接 @%s 闲聊，派单该角色自然接话", target)
    out = _dispatch_expert(msg.chat_id, replies, target,
                           f"老板在群里直接找你聊：{content}\n"
                           "先自然共情接住老板的话，别摆架子；然后绕回你的本职工作，主动建议一个具体下一步。",
                           _recent_context(rounds))
    if out is not None:
        _register_expert_proposal(msg.chat_id, out)
        replies.append(OutboundMessage(msg.chat_id, out, target))

def _exec_revise_review_rules(msg: InboundMessage, content: str, task: str, rounds: list, replies: list, params: dict | None = None) -> None:
    # 老板纠正席小核审核判断/给审核标准 → 席小习提炼 → 直接落已确认规则（老板已拍板），下次审核自动对齐
    su = _last_style.get(msg.chat_id, "老席")
    speaker, prev = _last_any_output(rounds)
    review = context_store.load_review(msg.chat_id, "席小核")
    ctx = "\n\n".join(part for part in (
        f"【最近一次席小核审核】\n{_cap(prev, 300)}" if speaker == "席小核" and prev else "",
        f"【审核详情存档】\n{review}" if review else "",
        _recent_context(rounds) or "",
    ) if part)
    _log.info("改审核看法 → 席小习提炼审核标准并直接落已确认规则")
    task_text = f"{content}\n请把老板这段话里席小核要遵守的审核标准提炼成规则，直接给 JSON。"
    style_prefix = f"【风格:{su}】" if su != "老席" else ""
    try:
        bot = bots.by_role("席小习")
    except KeyError:
        bot = None
    if bot is not None and _emit is not None:
        _context_put(msg.chat_id, "席小习", style_prefix + task_text, ctx)
        _emit(msg.chat_id, f"<at user_id=\"{bot['open_id']}\"></at> {style_prefix}{task_text}", "席小习")
        replies.append(OutboundMessage(msg.chat_id,
            "收到老板的审核标准，已让席小习沉淀成规则，席小核下次审核自动对齐。", "小席"))
        return
    try:
        text = _controller_factories()["席小习"]().handle(style_prefix + task_text, context=ctx)
    except Exception as e:
        _log.error("席小习提炼审核标准失败: %s", e)
        replies.append(OutboundMessage(msg.chat_id,
            f"收到老板的审核标准：\n{content}\n（席小习提炼失败，规则稍后再补）", "小席"))
        return
    pairs = parse_learned_rules(text)
    if pairs:
        for change, rule, rtype in pairs[:3]:
            rules.add_confirmed_rule(change, rule, style_user=su, rtype=rtype)
        body = "\n".join(f"- {rule}" for _, rule, _ in pairs[:3])
        replies.append(OutboundMessage(msg.chat_id, f"已让席小核记住：\n{body}\n下次审核自动对齐。", "小席"))
    else:
        replies.append(OutboundMessage(msg.chat_id,
            f"收到老板的审核标准：\n{content}\n已让席小核记住，下次审核按此对齐。", "小席"))

# 动作名 → 执行器。签名统一 (msg, content, task, rounds, replies, params)。
# 写脚本/审核/圆桌/追问/学规则等既有函数签名不同，用 lambda 适配。
_ACTION_EXECUTORS = {
    "出选题": _exec_topics,
    "写脚本": lambda msg, content, task, rounds, replies, params: _write_script(msg, content, task, rounds, replies, params),
    "审核脚本": lambda msg, content, task, rounds, replies, params: _review_script(msg, content, rounds, replies),
    "看排期表": _exec_view_schedule,
    "改排期": _exec_change_schedule,
    "改表格结构": _exec_table_column,
    "确认已发布": _exec_mark_published,
    "确认排期": _exec_confirm_publish,
    "反馈修改": lambda msg, content, task, rounds, replies, params: _revise(msg, content, rounds, replies),
    "追问": lambda msg, content, task, rounds, replies, params: _ask(msg, content, rounds, replies),
    "要审核理由": _exec_review_reasons,
    "记素材": _exec_record_material,
    "看完整讨论": _exec_history,
    "查记录": _exec_lookup_record,
    "查已用选题": _exec_used_topics,
    "圆桌讨论": lambda msg, content, task, rounds, replies, params: _roundtable(msg, content, params, rounds, replies),
    "学规则": lambda msg, content, task, rounds, replies, params: _learn_rules(msg, content, rounds, replies),
    "确认规则": lambda msg, content, task, rounds, replies, params: _confirm_rules(msg, content, replies),
    "数据回流": lambda msg, content, task, rounds, replies, params: _reflow(msg, content, replies, params),
    "更新市场情报": lambda msg, content, task, rounds, replies, params: _refresh_market(msg, replies),
    "改审核看法": _exec_revise_review_rules,
    "专家闲聊": _exec_expert_chat,
    "调研": _exec_research,
}

def _execute_action(msg: InboundMessage, action: str, task: str, rounds: list, replies: list, params: dict) -> None:
    fn = _ACTION_EXECUTORS.get(action)
    if not fn:
        _log.info("未知动作 %s，跳过执行", action)
        return
    _log.info("执行动作 %s", action)
    try:
        fn(msg, msg.content, task, rounds, replies, params or {})
    except Exception as e:
        _log.error("动作 %s 执行失败: %s", action, e)
        if not replies:
            replies.append(OutboundMessage(msg.chat_id, f"{action}执行失败，稍后再试。", "小席"))

def _action_allowed(role: str, action: str) -> bool:
    if action in _PERMISSION_USER_READONLY:
        return _is_user(role) or _is_admin(role)
    if action == "确认已发布":
        return _is_admin(role) or role == "发片同事"
    if action in ("确认排期", "专家闲聊"):
        return True
    return _is_admin(role)

def _deny_message(role: str, action: str) -> str:
    if role == "未知":
        return "无权限：当前身份未识别"
    return "无权限：只有老板或产品能做这个，用户只读。"

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

    if _pipelines.get(msg.chat_id, {}).get("step") == "wait_boss" and (_is_admin(role) or _is_pipeline_owner(msg)):
        _resolve_doubt(msg, content, rounds, replies)
        return replies

    if _pipelines.get(msg.chat_id, {}).get("step") == "wait_schedule" and (_is_admin(role) or _is_pipeline_owner(msg)):
        _resolve_schedule(msg, content, replies)
        return replies

    if _pipelines.get(msg.chat_id, {}).get("step") == "wait_style" and (_is_admin(role) or _is_pipeline_owner(msg)):
        _resolve_style(msg, content, rounds, replies)
        return replies

    if msg.chat_id in _used_cursor:
        if any(w in content for w in _USED_CONTINUE_WORDS):
            _send_used_page(msg.chat_id, replies, _used_cursor[msg.chat_id])
            _remember(msg, replies)
            return replies
        if any(w in content for w in _USED_STOP_WORDS):
            _used_cursor.pop(msg.chat_id, None)
            replies.append(OutboundMessage(msg.chat_id, "好，已用选题看完了，要继续出题随时说。", "小席"))
            _remember(msg, replies)
            return replies

    if _is_admin(role) and not rules.has_pending() and _is_affirmative(content):
        pending = _pop_pending(msg.chat_id)
        if pending is not None:
            action = pending.get("action")
            _log.info("老板确认提议 → 执行待定动作 action=%s", action)
            if action:
                _execute_action(msg, action, pending.get("task") or "", rounds, replies, pending.get("params") or {})
            _remember(msg, replies)
            return replies

    if _view_schedule_requested(content):
        if _action_allowed(role, "看排期表"):
            _log.info("硬锚命中 看排期表")
            _exec_view_schedule(msg, content, "", rounds, replies)
            _remember(msg, replies)
            return replies

    step = _pipelines.get(msg.chat_id, {}).get("step", "")
    plan = _planner(msg, content, history_text, rounds)
    if plan is None:
        _chat_reply(msg, content, rounds, replies)
        _remember(msg, replies)
        return replies
    if step:
        plan.action = ""  # 任何流水线态活跃：规划器只陪聊不派活，防自动改稿循环中途重复派单

    if plan.action:
        if _action_allowed(role, plan.action):
            if plan.action == "确认已发布" and not _has_publish_confirm(content):
                _log.info("确认已发布防呆拦截（原文无确认词）")
                replies.append(OutboundMessage(msg.chat_id, _publish_guard_reply(), "小席"))
            else:
                _log.info("动作命中，丢弃规划器自然回复 action=%s", plan.action)
                _execute_action(msg, plan.action, plan.task, rounds, replies, plan.params)
                if not replies and plan.reply:
                    replies.append(OutboundMessage(msg.chat_id, plan.reply, "小席"))
        else:
            replies.append(OutboundMessage(msg.chat_id, _deny_message(role, plan.action), "小席"))
        _remember(msg, replies)
        return replies

    # 规划器未写动作 → 兜底链，保高信号指令不静默
    record_reply = _lookup_topic_record(msg.chat_id, content, rounds, replies)
    if record_reply:
        _log.info("查选题记录 → 真核对回执")
        replies.append(OutboundMessage(msg.chat_id, record_reply, "小席"))
        _remember(msg, replies)
        return replies
    reason_reply = _show_review_reasons(msg.chat_id, content, rounds)
    if reason_reply:
        _log.info("老板要审核理由，直发存档详情")
        replies.append(OutboundMessage(msg.chat_id, reason_reply, "席小核"))
        _remember(msg, replies)
        return replies
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
        _remember(msg, replies)
        return replies
    force = _force_script_task(content, rounds)
    if force:
        _log.info("规划器未派活但命中产出指令，强制写脚本派单")
        _write_script(msg, content, force, rounds, replies)
        _remember(msg, replies)
        return replies
    if not replies:
        reply = plan.reply or _chat_generate(content, render_history(rounds))
        replies.append(OutboundMessage(msg.chat_id, reply, "小席"))
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
    if speaker == "席小题":
        topics, basis = context_store.load_basis(msg.chat_id, "席小题")
        if basis:
            n = basis_util.match_index(content)
            if n:
                hit = next((basis_util.item_reply(num, topic, reason)
                            for num, topic, reason in basis_util.paired_topics(topics, basis)
                            if num == n), None)
                if hit:
                    _log.info("追问 席小题依据 → 直发第 %s 条依据（真实句，不重生成）", n)
                    replies.append(OutboundMessage(msg.chat_id, hit, "席小题"))
                    return
                replies.append(OutboundMessage(msg.chat_id, f"没找到第 {n} 个选题的依据。", "席小题"))
                return
            _log.info("追问 席小题依据 → 直发存档依据（真实句，不重生成）")
            replies.append(OutboundMessage(msg.chat_id,
                                           f"依据如下（席小题出题时写的，第 N 条对应第 N 个选题）：\n{basis}", "席小题"))
            return
    expert_role = speaker if speaker in ("席小题", "席小文") else "席小文"
    ctx = f"【你上次的产出 · {speaker}】\n{prev}"
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

def _asks_chosen(content: str) -> bool:
    # 「我选择出脚本的选题还能看到么」等指向刚选中/刚定稿的那一个，而非整版选题清单
    if "定稿" in content or "刚定" in content or "刚通过" in content:
        return True
    if "出脚本" in content or ("选" in content and "脚本" in content) or "选择" in content:
        return True
    return False

def _lookup_topic_record(chat_id: str, content: str, rounds: list, replies: list, force: bool = False) -> str:
    # 老板问「刚才的选题还能看到么」这类：必须真读记录回执，不能闲聊承诺「我去核对」
    # force=True 时跳过关键词对（意图已判定为查记录，或来自小席【行动】立即动作）
    if not force and not any(a in content and b in content for a, b in _TOPIC_LOOKUP_PAIRS):
        return ""
    # 老板问的是刚选中/刚定稿的那条 → 优先回那一条，不回整版选题 dump
    lp = _last_passed.get(chat_id) or {}
    draft = _draft_ctx.get(chat_id) or ("", "")
    topic = lp.get("topic") or draft[0]
    if topic and _asks_chosen(content):
        ctype = lp.get("content_type") or draft[1]
        _log.info("查选题记录 → 刚选中选题回执")
        return (f"能看到，你刚选中的选题是「{topic}」（类型：{ctype}）。\n"
                "要排期 / 写脚本，或按这条继续，直接说就行。")
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
    # 只当席小核刚审完并邀约「需要给您展示理由吗？」、老板短回应要看理由时才直发存档详情。
    # 此前单个「要」字会误触发，把与消息无关的旧审核存档甩出来（2026-08-07 事故）。
    speaker, prev = _last_any_output(rounds)
    if speaker != "席小核" or not prev:
        return ""
    if "需要给您展示理由吗" not in prev:
        return ""
    c = content.strip()
    if len(c) > 8:
        return ""
    if not any(w in c for w in _REASON_YES):
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
    su = _last_style.get(msg.chat_id, "老席")
    if not rules.has_pending(style_user=su):
        replies.append(OutboundMessage(msg.chat_id, "当前没有待确认的规则", "小席"))
        return
    reject = any(w in content for w in ("不用", "算了", "别学", "撤销", "否"))
    n = rules.confirm_pending(not reject, style_user=su)
    replies.append(OutboundMessage(msg.chat_id,
        f"已丢弃 {n} 条待确认规则" if reject else f"已确认 {n} 条规则，写入「{su}」风格档案，下次产出生效", "小席"))

_ABORT_WORDS = ("算了", "不用了", "放弃", "先不管", "驳回")
# 老板在 wait_boss 下回「放行」→ 按当前脚本通过定稿，不落规则、不再派改稿（防把放行话术误当答案，导致越改越复审的死循环）
_PASS_HINTS = ("确定了", "可以下一步", "下一步", "就这么样", "就按这个来", "就这么定",
               "确认通过", "通过吧", "可以了", "行了", "好了", "继续", "不用改",
               "别改", "不用再改", "不纠结", "就这样", "可以吧", "就这个吧")
_PASS_EXACT = ("没问题", "OK", "好的", "可以")

def _is_pass(content: str) -> bool:
    c = content.strip()
    return any(p in c for p in _PASS_HINTS) or c in _PASS_EXACT

# 老板拍板消息里同时带「实质修改 + 放行词」（如「早三年吧，然后就可以了」）时，放行词不能抢在改稿指令前——
# 否则「早三年」这类实质答案被「可以了」吞掉直接定稿。数字量词或肯定改稿动词 → 判实质修改。
_MODIFY_VERBS = ("改成", "换成", "写成", "改为", "调成", "删掉", "去掉", "加上", "加个", "挪到")
_MODIFY_QUANT = re.compile(r"\d+|[一二三四五六七八九十两](?:年|万|岁|个|条|月|天|小时|点)")
_NEGATED_CHANGE = ("不用改", "别改", "不用再改", "不改了")

def _has_concrete_modification(content: str) -> bool:
    c = content or ""
    if any(w in c for w in _NEGATED_CHANGE):
        return False
    return bool(_MODIFY_QUANT.search(c)) or any(v in c for v in _MODIFY_VERBS)

def _ask_schedule(chat_id: str, script: str, replies: list) -> None:
    """定稿出口统一问排期：审核通过或老板放行后，@提出者 等排期信息（排哪天/负责人）。"""
    _pipelines[chat_id] = {"step": "wait_schedule", "script": script, "ts": time.time()}
    _post(chat_id, replies, "小席",
          f"{_requester_mention(chat_id)}写脚本流水线完成：席小文出稿 + 席小核审核通过。\n"
          "要不要上排期表？排哪天几点？负责人是哪个发片同事？\n"
          "回「排 8/10 12:00」（负责人不写就默认发片同事），或「先不排」。")

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
    if _has_concrete_modification(content):
        _log.info("老板答复含实质修改（有数字量词/改稿动词）→ 走改稿，放行词不抢先")
    elif _is_pass(content):
        _record_passed(msg.chat_id)
        _ask_schedule(msg.chat_id, pipe["script"], replies)
        _remember(msg, replies)
        return
    answer = content.strip()
    _log.info("老板答复审核疑问，落规则 + 让席小文修改")
    try:
        rules.add_confirmed_rule(f"老板确认审核疑问（{_cap(pipe['reviewer'], 40)}）", answer,
                                 style_user=_last_style.get(msg.chat_id, "老席"), rtype="事实规则")
    except Exception as e:
        _log.error("记录疑问答案规则失败: %s", e)
    first = pipe["reviewer"].strip().split(chr(10), 1)[0]
    # 老板拍板实质答案 → 席小习同步拆解差异并直接落已确认规则（老板已拍板，不引入二次确认；失败不影响主流程）
    try:
        xctx = f"【AI原版/审核稿】\n{_cap(pipe['script'], 300)}\n\n【审核疑问】\n{first}"
        xtext = _controller_factories()["席小习"]().handle(
            f"{answer}\n把老板这段确定答案拆解成审核/写作规则，直接给 JSON。", context=xctx)
        for change, rule, rtype in parse_learned_rules(xtext)[:3]:
            rules.add_confirmed_rule(change, rule,
                                     style_user=_last_style.get(msg.chat_id, "老席"), rtype=rtype)
    except Exception as e:
        _log.error("席小习拆解拍板答案失败: %s", e)
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

def _requester_mention(chat_id: str) -> str:
    # 发起写脚本需求的真人 → 针对性 @ 他提问（不泛问群）；没记录到就空串
    oid = _requester_by_chat.get(chat_id, "")
    return f'<at user_id="{oid}"></at>' if oid else ""


_SCHEDULE_NO_WORDS = ("不排", "先不排", "不用了", "算了", "先不上", "不了", "先不用")

def _resolve_schedule(msg: InboundMessage, content: str, replies: list) -> None:
    """定稿后等老板/产品给排期信息：解析日期时间负责人 → 记录排期，或取消。"""
    pipe = _pipelines.pop(msg.chat_id, None)
    if not pipe:
        return
    if any(w in content for w in _SCHEDULE_NO_WORDS):
        _post(msg.chat_id, replies, "小席", "好，先不排。脚本已定稿，随时说一声就上排期。")
        return
    date = _extract_date(content)
    if not date and any(k in content for k in ("今天", "明天", "下周", "下星期", "星期", "周")):
        date = _resolve_date(content)
    if not date:
        _post(msg.chat_id, replies, "小席",
              f"{_requester_mention(msg.chat_id)}给我个具体日期时间，比如「排 8/10 12:00」；或回「先不排」。")
        _pipelines[msg.chat_id] = dict(pipe, ts=time.time())
        return
    entry = _build_schedule_entry(pipe.get("script", ""), content, msg.chat_id, date)
    add_entry(entry)
    add_used(entry["topic"])
    _post(msg.chat_id, replies, "小席",
          f"已排上：{entry['date']} {entry['publish_time']} 发「{entry['topic']}」负责人={entry['owner']}（脚本已入列）"
          + _base_link())


def _build_schedule_entry(script: str, content: str, chat_id: str, date: str) -> dict:
    topic, ctype = _draft_ctx.get(chat_id, ("", ""))
    if not topic:
        topic = _script_title(script) or "脚本"
    if not ctype:
        ctype = "曝光"
    owner = _parse_owner(content) or "发片同事"
    return {"date": date,
            "publish_time": _resolve_time(content),
            "content_type": ctype, "topic": topic,
            "goal": "拉新", "status": "待产", "owner": owner,
            "script": script or "", "source_chat": chat_id}


def _script_title(script: str) -> str:
    m = _SCRIPT_HEADER_RE.search(script or "")
    if m and m.group(1).strip():
        return m.group(1).strip()[:30]
    return ""

def _reflow(msg: InboundMessage, content: str, replies: list, params: dict | None = None) -> None:
    params = params or {}
    date = (params.get("date") or "").strip()
    topic = (params.get("topic") or "").strip()
    data_str = (params.get("data") or "").strip()
    if not data_str:
        parsed = _parse_reflow_data(content)
        if parsed:
            date, topic, data_str = parsed
        else:
            # 无 M/D 日期但带数据指标 → 回落到最近一条「已发」行；没有则暂存，等确认已发布自动回流
            metrics = _extract_metrics(content)
            if metrics:
                row = _latest_published_row()
                if row is None:
                    _stash_pending(msg.chat_id, "", "", metrics)
                    replies.append(OutboundMessage(msg.chat_id,
                        f"数据已记下：{metrics}。等排期表里哪条确认「已发布」，我自动给它回流。", "小席"))
                    return
                date, topic, data_str = row["date"], row["topic"], metrics
            else:
                rows = [r for r in load_schedule() if r["status"] == "已发"]
                if not rows:
                    replies.append(OutboundMessage(msg.chat_id, "当前没有待回流数据（排期表里没有「已发」条目）", "小席"))
                else:
                    lines = "\n".join(f"- {r['date']}「{r['topic']}」数据回流：{r['data']}" for r in rows)
                    replies.append(OutboundMessage(msg.chat_id, f"以下条目已发待回流，报数据格式：日期 选题 播放x 留资y\n{lines}", "小席"))
                return
    data = data_str.replace("万", "w")
    if not record_data(date, topic, data):
        # 行存在但还没标已发 → 暂存带标题，等这条确认已发布自动回流；行不存在 → 明说没找到
        if any(r["date"] == date and r["topic"] == topic for r in load_schedule()):
            _stash_pending(msg.chat_id, date, topic, data)
            replies.append(OutboundMessage(msg.chat_id,
                f"数据已记下：{date}「{topic}」 {data}。等这条确认「已发布」后我自动给它回流。", "小席"))
        else:
            replies.append(OutboundMessage(msg.chat_id, f"没找到「{date} {topic}」的已发条目，确认下日期/选题", "小席"))
        return
    conclusion = _dispatch_expert(msg.chat_id, replies, "席小盘", f"日期 {date} 选题 {topic} 数据：{data}", "")
    if conclusion is None:
        _pipelines[msg.chat_id] = {"step": "wait_fupan", "date": date, "topic": topic, "data": data, "ts": time.time()}
        _post(msg.chat_id, replies, "小席", f"数据已回流：{date}「{topic}」 {data}，复盘分析中…")
        return
    _post(msg.chat_id, replies, "席小盘", f"数据已回流：{date}「{topic}」 {data}\n\n【席小盘】\n{conclusion}")

def _stash_pending(chat_id: str, date: str, topic: str, data: str) -> None:
    _pending_reflow[chat_id] = {"date": date, "topic": topic, "data": data, "ts": time.time()}

def _take_pending(chat_id: str, row: dict):
    p = _pending_reflow.get(chat_id)
    if not p:
        return None
    if time.time() - p["ts"] > _PENDING_REFLOW_TTL:
        _pending_reflow.pop(chat_id, None)
        return None
    hint = (p.get("topic") or "").strip()
    d = (p.get("date") or "").strip()
    # 带标题/日期的暂存只在匹配刚标已发的行时应用，防止串台
    if hint and not _fuzzy_topic_match(_normalize_topic(hint), _normalize_topic(row["topic"])):
        return None
    if d and d != row["date"]:
        return None
    _pending_reflow.pop(chat_id, None)
    return p

def _extract_metrics(content: str) -> str:
    pairs = re.findall(r"(浏览|播放|点赞|评论|收藏|转发|分享|留资)\s*[:：]?\s*([0-9][0-9\.万w]*)", content)
    return " ".join(f"{k}{v}" for k, v in pairs)

def _latest_published_row():
    rows = [r for r in load_schedule() if r["status"] == "已发"]
    if not rows:
        return None
    def key(r):
        try:
            m, d = r["date"].split("/")
            return int(m) * 100 + int(d)
        except (ValueError, AttributeError):
            return 0
    return max(rows, key=key)

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

def _parse_count(text: str) -> int:
    m = re.search(r"([一二两三四五六七八九十\d]+)\s*(条|个)", text or "")
    return _to_int(m.group(1)) if m else 0

def _schedule_entries(params: dict, content: str = "") -> list[dict]:
    # 只服务批量排期：明确数量（内容或 params.count）才编行；单条排期不在此兜底，杜绝「曝光选题N」占位垃圾
    count = _parse_count(content) or int((params.get("count") or "0").strip("个"))
    if count <= 0:
        return []
    ctype = params.get("content_type") or "曝光"
    date = _resolve_date(params.get("date") or _extract_date(content) or "下周")
    publish_time = _resolve_time((params.get("publish_time") or _extract_time(content) or "").strip())
    owner = _parse_owner(content) or "发片同事"
    return [
        {"date": date, "publish_time": publish_time, "content_type": ctype, "topic": f"{ctype}选题{i+1}",
         "goal": "拉新", "status": "待产", "owner": owner, "data": "—"}
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
    if m:
        return m.group(0)
    m = re.search(r"(\d{1,2})月(\d{1,2})日", text or "")
    if m:
        return f"{int(m.group(1))}/{int(m.group(2))}"
    return ""


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

def _parse_owner(content: str) -> str:
    # 从「负责人张艺宝/给张艺宝/归张艺宝」抽出负责人，无则空串
    m = re.search(r"(?:负责人|给|归)\s*([^，。；、\s]{1,6})", content or "")
    if m and m.group(1) not in ("是", "的", "谁", "发片"):
        return m.group(1)
    return ""

def _list_reference(content: str) -> bool:
    # 「第N个/第N条」指列表下标（排上第1个），不能误用刚定稿选题
    return bool(re.search(r"第[一二两三四五六七八九十\d]+[条个]", content or ""))

def _explicit_count(content: str) -> bool:
    # 批量排期「排3条/排两个」带数量，不套刚定稿选题
    return bool(re.search(r"[一二两三四五六七八九十\d]+\s*(条|个)", content or ""))

def _has_schedule_intent(content: str, params: dict) -> bool:
    # 明确排期意图：日期/时间/负责人/排字已给出，或 params 已解析出日期 → 视为排刚定稿选题
    if any(params.get(k) for k in ("date", "publish_time")):
        return True
    if _extract_date(content) or _extract_time(content):
        return True
    if any(k in content for k in ("今天", "明天", "下周", "下星期", "星期", "周")):
        return True
    if "负责人" in content or "上排期" in content or re.search(r"排[上入表期发布]", content):
        return True
    return False

def _try_pick_topic(content: str, params: dict, rounds: list, chat_id: str = "") -> dict | None:
    # ① 老板确认/排刚定稿的脚本选题（「这条选题通过了/排期发布/排8/8 18:15 负责人张艺宝」）→ 用刚定稿的选题+类型
    if chat_id and chat_id in _last_passed:
        lp = _last_passed[chat_id]
        if lp.get("topic"):
            picked_ready = (any(w in content for w in _PASSED_HINTS)
                            or _has_schedule_intent(content, params))
            if picked_ready and not _list_reference(content) and not _explicit_count(content):
                return {"date": _resolve_date(params.get("date") or _extract_date(content) or "下周"),
                        "publish_time": _resolve_time((params.get("publish_time") or _extract_time(content) or "").strip()),
                        "content_type": lp["content_type"], "topic": lp["topic"], "goal": "拉新",
                        "status": "待产", "owner": _parse_owner(content) or "发片同事", "data": "—"}
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

def _schedule_reply() -> str:
    """看排期表回执：配了多维表格就直接给链接，不刷整张表；未配才回本地表格。"""
    if _base_url:
        return f"多维表格：{_base_url}"
    return "当前排期表：\n" + render_schedule()

# 看排期表硬锚：明确含「看/查/打开+表格词」或「表格词+链接」→ 不依赖 LLM 直接给排期表
_VIEW_SCHEDULE_RE = re.compile(
    r"(?:看|查|打开|看看|看下|看一下|发我|给我|给我发|发一下|贴|给个)\s*(?:下\s*)?"
    r"(?:排期表|多维表格|表格)"
    r"|(?:排期表|多维表格|表格)\s*(?:链接|发我|给我|看看|看下)"
    r"|(?:看|看看|看下)\s*排期"
)
# 硬锚排除词：含这些 → 是改表格结构/标已发等，不是看排期表
_VIEW_SCHEDULE_EXCLUDE = ("改", "结构", "加一列", "删", "标已发", "已发布", "标成")

def _view_schedule_requested(content: str) -> bool:
    if any(k in (content or "") for k in _VIEW_SCHEDULE_EXCLUDE):
        return False
    return bool(_VIEW_SCHEDULE_RE.search(content or ""))

# 确认已发布防呆：高后果动作，原文必须带发布确认信号词才放行（覆盖「刚发布了一条」「已确认发布」等轻确认）
_PUBLISH_CONFIRM_WORDS = ("已发布", "已发", "已确认发布", "发布了一条", "发布了", "刚发布", "刚发了",
                          "发了一条", "发过了", "发好了", "发完了", "发布完成", "发出去了", "这条发了", "发了")

def _has_publish_confirm(content: str) -> bool:
    return any(w in (content or "") for w in _PUBLISH_CONFIRM_WORDS)

def _publish_guard_reply() -> str:
    link = _base_link()
    return f"你是要标哪条已发布？说日期+标题，比如「8/8 19岁本科毕业… 已发布」。{link}".strip()

_TABLE_COLUMN_ALIASES = {"脚本": "脚本内容", "详细脚本": "脚本内容", "脚本全文": "脚本内容", "脚本正文": "脚本内容"}

def _parse_column_name(content: str) -> str:
    # 先认已知列名（口语「把详细脚本内容也放到里面」= 脚本内容），再兜底抓「放/存 XX」或「XX列」
    for kw in ("脚本内容", "脚本全文", "详细脚本", "脚本正文"):
        if kw in content:
            return "脚本内容"
    if "负责人" in content:
        return "负责人"
    m = re.search(r"(?:放|存|填|展示|记录)\s*([^，。；、\s]{1,8})", content)
    if m:
        return _TABLE_COLUMN_ALIASES.get(m.group(1), m.group(1))
    m = re.search(r"([^，。；、\s]{1,6})列(?:吧|呢)?", content)
    return _TABLE_COLUMN_ALIASES.get(m.group(1), m.group(1)) if m else ""

def _add_table_column(content: str) -> str:
    name = _parse_column_name(content)
    if not name:
        return "你要加一列放什么？说清楚列名，比如「单开一列放脚本内容」。"
    if name == "负责人":
        return "负责人列已经在了，不用重复加。"
    try:
        base_bridge.add_column(name)
    except Exception as e:
        _log.error("加列「%s」失败: %s", name, e)
        return f"加列「{name}」失败：{_cap(str(e))}"
    if name == "脚本内容":
        return "已加「脚本内容」列——本地排期表 schema 已同步，定稿的脚本会自动填进这一列。"
    return f"已加「{name}」列（文本）。只有「脚本内容」列会自动填，其他列需要手动维护。"

def _is_admin(role: str) -> bool:
    return role == "老板" or role == "产品"

def _is_user(role: str) -> bool:
    return role == "用户"

def _is_pipeline_owner(msg: InboundMessage) -> bool:
    # 发起该条写脚本流水线的真人：他也算需求方，可回排期/审核确认，不必等老板
    oid = _requester_by_chat.get(msg.chat_id, "")
    return bool(oid) and msg.sender_open_id == oid

def _publish_reply(row: dict, date_mismatch: bool, reported_date: str) -> str:
    """标已发成功回执；日期不符（用户报的日期与表里不一致但 topic 唯一命中）时提示已按表里日期确认。"""
    if date_mismatch:
        return f"确认（注：你报的日期 {reported_date} 表里是 {row['date']}，已按表里 {row['date']} 确认）"
    return "确认"

def _try_mark_published(content: str, params: dict | None = None, chat_id: str = "") -> str:
    global _last_marked
    params = params or {}
    _last_marked = None
    if params.get("date") and params.get("topic"):
        row, date_mm = _resolve_publish_row(params["date"], params["topic"])
        if row and mark_published(row["date"], row["topic"]):
            _last_marked = (row["date"], row["topic"])
            return _publish_reply(row, date_mm, params["date"])
        return "未找到对应待发条目"
    body = content.replace("已发布", "").strip()
    m = re.match(r"(\d{1,2}/\d{1,2})\s*(.*)", body)
    if m and m.group(2).strip():
        row, date_mm = _resolve_publish_row(m.group(1), m.group(2).strip())
        if row and mark_published(row["date"], row["topic"]):
            _last_marked = (row["date"], row["topic"])
            return _publish_reply(row, date_mm, m.group(1))
        return "未找到对应待发条目"
    # 无日期 → 剥《》标点后按标题模糊匹配待发行
    norm = _normalize_topic(body)
    if norm:
        for r in load_schedule():
            if r["status"] in ("待产", "待发") and _fuzzy_topic_match(norm, _normalize_topic(r["topic"])):
                if mark_published(r["date"], r["topic"]):
                    _last_marked = (r["date"], r["topic"])
                    return "确认"
                return "未找到对应待发条目"
    # 无日期+标题也模糊不到 → 回落「最近定稿的选题」（刚讨论/刚上排期表那条），唯一命中才标，撞题不猜
    if chat_id:
        lp = _last_passed.get(chat_id) or {}
        lp_topic = _normalize_topic(lp.get("topic", ""))
        if lp_topic:
            rows = [r for r in load_schedule()
                    if r["status"] in ("待产", "待发")
                    and _fuzzy_topic_match(lp_topic, _normalize_topic(r["topic"]))]
            if len(rows) == 1:
                r = rows[0]
                if mark_published(r["date"], r["topic"]):
                    _last_marked = (r["date"], r["topic"])
                    return "确认"
                return "未找到对应待发条目"
            if len(rows) > 1:
                return "待发行里有几条都能对上刚讨论的选题，说下日期或标题，我好确认是标哪条已发。"
    return "没找到匹配的待发条目，说清日期+标题（如「8/3 普娃逆袭 已发布」）"

def _resolve_publish_row(date_hint: str, topic_hint: str) -> tuple[dict | None, bool]:
    """把 LLM/正则给的 date/topic 提示落到一条 待产/待发 行，返回 (row, 日期是否不符)。
    date_hint 可能是「8/7 19:45」（剥出 M/D 去掉时间），topic_hint 剥《》标点后模糊匹配；
    日期对得上优先；对不上但标题在待发行里唯一命中时容忍日期笔误（第二元素 True）；
    找不到或撞题歧义返回 (None, False)。"""
    dm = re.search(r"(\d{1,2})/(\d{1,2})", date_hint or "")
    date = f"{int(dm.group(1))}/{int(dm.group(2))}" if dm else ""
    norm = _normalize_topic(topic_hint or "")
    if not norm:
        return None, False
    exact, fallback = [], []
    for r in load_schedule():
        if r["status"] not in ("待产", "待发"):
            continue
        if not _fuzzy_topic_match(norm, _normalize_topic(r["topic"])):
            continue
        if not date or r["date"] == date:
            exact.append(r)
        else:
            fallback.append(r)
    if len(exact) == 1:
        return exact[0], False
    if len(fallback) == 1:
        return fallback[0], True
    return None, False

def _normalize_topic(s: str) -> str:
    return re.sub(r"[《》「」『』【】\s,，。、？！?!:：;；\"'']+", "", s or "")

def _fuzzy_topic_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return a in b or b in a
