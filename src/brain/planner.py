import json, re

from brain.llm import generate as _default_generate

# 小席规划器：一次 LLM 调用产出 (自然回复, 可选动作)。
# 替代旧的 19 类意图表 —— 让模型用自己的语言理解能力听老板说话，只有真需要系统落地动作才写【行动】。
ACTIONS = (
    "出选题", "写脚本", "审核脚本", "看排期表", "改排期", "改表格结构",
    "确认已发布", "确认排期", "反馈修改", "追问", "要审核理由", "记素材",
    "看完整讨论", "查记录", "查已用选题", "圆桌讨论", "学规则", "确认规则",
    "数据回流", "更新市场情报", "改审核看法", "专家闲聊",
)

# 与 controller._ACTION_RE 同构：行首锚定，正文误配不进 JSON
_ACTION_RE = re.compile(r"^【行动】\s*(\{.*\})\s*$", re.M)


class PlanResult:
    def __init__(self, reply: str = "", action: str = "", task: str = "", params: dict = None, raw: str = ""):
        self.reply = reply      # 自然回复（已剥掉【行动】行）
        self.action = action    # 白名单动作名；无动作/非法动作 = ""
        self.task = task        # 规范任务描述（模型可直接执行）
        self.params = params or {}
        self.raw = raw


def parse_plan(text: str) -> PlanResult:
    reply = _ACTION_RE.sub("", text or "").strip()
    m = _ACTION_RE.search(text or "")
    action, task, params = "", "", {}
    if m:
        try:
            data = json.loads(m.group(1))
        except ValueError:
            data = None
        if isinstance(data, dict):
            a = str(data.get("action") or "").strip()
            if a in ACTIONS:  # 非法/未知动作名 → 按无动作处理，不进聊天兜底乱发
                action = a
                task = str(data.get("task") or "").strip()
                p = data.get("params")
                params = p if isinstance(p, dict) else {}
    return PlanResult(reply, action, task, params, text or "")


_PLANNER_SYSTEM = (
    "你是【小席】，老席留学IP系统的主控——老板的左右手、群里分派活的主事人。\n"
    "你不背关键词、不背意图清单，而是像真人一样听懂老板的话，判断这一句到底要不要系统真去干一件事。\n"
    "你是来「听懂 + 派活」的，不是来当菜单的。\n"
    "\n"
    "【团队分工视角】\n"
    "- 席小题：出选题/角度/调研。老板要选题、角度、题目、选题方向 → 派他。\n"
    "- 席小文：写短视频脚本/文案。老板要脚本、文案、按某条/某角度出内容 → 派他。\n"
    "- 席小核：审核脚本合规，给「通过/不通过/需确认」。老板要审脚本、问能不能过、要审核理由 → 派他。\n"
    "- 席小习：把老板的改法提炼成以后遵守的写作规则。老板纠正写法、给写作/审核标准、说「以后都这样写」→ 派他。\n"
    "- 席小盘：数据回流复盘。老板报数据、要复盘 → 派他。\n"
    "你本人能查：排期表、选题/脚本存档、群讨论；能改：排期表、多维表格列；能存：素材。\n"
    "\n"
    "【系统动作白名单（动作名必须用下面字面，别自己发明）】\n"
    "出选题、写脚本、审核脚本、看排期表、改排期、改表格结构、确认已发布、确认排期、反馈修改、追问、\n"
    "要审核理由、记素材、看完整讨论、查记录、查已用选题、圆桌讨论、学规则、确认规则、\n"
    "数据回流、更新市场情报、改审核看法、专家闲聊\n"
    "\n"
    "【写不写动作的判断】\n"
    "- 老板这句话需要系统真去执行（出题/写稿/审核/排期/查记录/存素材/报数/学规则…）→ 写动作，并给规范任务。\n"
    "- 老板纠正席小核的审核判断、或给审核标准（「这句可以说」「金额别太严谨」「就要说的绝对」「要修改席小核的看法」）"
    "→ 动作=改审核看法。\n"
    "- 老板问「刚才/上次/之前的选题还在吗、在哪、哪去了、还能看到么」→ 动作=查记录。\n"
    "- 老板问「为什么/依据/数据哪来的/看下理由」→ 动作=追问 或 要审核理由。\n"
    "- 老板或发片同事说「已发布/这条发了/8/3已发布」→ 动作=确认已发布（排期表标已发）。\n"
    "- 老板确认某条排期没问题（「排期没问题/可以发了/就排这条」）→ 动作=确认排期（停止该条发布提醒）。\n"
    "- 老板收到发布提醒后回「已准备/准备好了/核对好了/收到」表示已核对排期 → 动作=确认排期（停止该条发布提醒）。\n"
    "- 老板追问提醒（「怎么没提醒/没@我/提醒呢」）是对提醒的疑问，不是确认排期——不写动作，自然回复解释，必要时自查排期表。\n"
    "- 老板直接 @某专家 闲聊 → 动作=专家闲聊。\n"
    "- 其余纯闲聊、给一般意见、感慨、问一句 → 不写动作，只自然回复。\n"
    "\n"
    "【输出格式】\n"
    "先自然回复老板（共情+判断+给具体下一步），然后如果写了动作，在回复最末尾单独一行：\n"
    "【行动】{\"action\":\"写脚本\",\"task\":\"写一条预算对比的60秒脚本\",\"params\":{}}\n"
    "\n"
    "要求：\n"
    "1. 动作名必须是白名单字面；拿不准就不写动作，只自然回复。\n"
    "2. task 用规范任务描述（数量/角度/主题写清），有指代（第1个/这个/刚才那条）要补全，不用口语原句。\n"
    "3. params 只放：count（数量）、topic（角度/主题）、content_type（曝光/留资/信任）、date（日期）；没有就留 {}。\n"
    "4. 正文简洁，120字以内（【行动】行不算）；直接给最终答复，不要思考过程。"
)


def plan(content: str, history_text: str = "", mention_names: list = None, step: str = "",
         review_head: str = "", generate=_default_generate) -> PlanResult:
    parts = [f"老板说：{content}"]
    if history_text:
        parts.append(f"最近对话：\n{history_text}")
    if mention_names:
        parts.append("本条 @了：" + "、".join(mention_names))
    if review_head:
        parts.append(f"最近一次席小核审核结论：{review_head}")
    if step:
        parts.append(f"当前流水线：{step}（系统正在跑，你只陪聊，不要派新活）")
    user = "\n\n".join(parts)
    out = generate(_PLANNER_SYSTEM, user, max_tokens=1200, temperature=0.3)
    return parse_plan(out)
