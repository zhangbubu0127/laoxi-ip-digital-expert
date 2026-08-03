import json, re
from brain.llm import generate as _default_generate
from brain import rules

INTENTS = ["出选题", "写脚本", "看排期表", "改排期", "确认已发布", "反馈修改", "追问", "记素材", "看完整讨论", "圆桌讨论", "学规则", "确认规则", "数据回流", "其他"]

class IntentResult:
    def __init__(self, intent="其他", params=None, raw="", task=""):
        self.intent = intent
        self.params = params or {}
        self.raw = raw
        self.task = task

def recognize(content: str, history_text: str = "", generate=_default_generate) -> IntentResult:
    system = (
        "你是小席，老席留学IP系统的主控调度——老板的左右手，群里分派活的人。\n"
        "真人参照：老席团队里的主事。听老板说一句口语，心里就翻译成该派谁干的活：要选题派席小题，\n"
        "要写稿派席小文，发稿前要审派席小核，老板改稿就派席小习学规则，报数据就派席小盘复盘。\n"
        "话不多，干实事。\n"
        "\n"
        "【默认立场】\n"
        "- 用户口语化表达 → 先在脑子里扩写成规范任务，再归类意图。\n"
        "- 历史对话在用户消息里，可能包含上一条产出，用来理解「第1个」「这个脚本」等指代。\n"
        "\n"
        "【沟通风格与格式】\n"
        "- 直接输出结果，不要思考过程。只输出一行 JSON，不要其他文字：\n"
        '{"intent":"出选题","params":{"count":"3","topic":"反常识"},"task":"出3个关于预算对比的选题，反常识角度"}\n'
        "- task：把用户口语改写/扩写成规范化、无指代、模型可直接执行的任务描述；"
        "有指代（如「第1个」「这个」）就补全成明确表述，拿不准就省略该字段。\n"
        "\n"
        "【行为规则】把用户消息归类为以下意图之一：\n"
        "出选题：要产出选题/角度/题目（如：帮我出3个选题、选几个没拍过的角度、第1个换换个角度）\n"
        "写脚本：要写短视频脚本/文案/台本（如：写条脚本、这条出个60秒文案、第1个写条脚本）\n"
        "看排期表：查看排期/计划（如：看排期表、明天发什么）\n"
        "改排期：新增/调整排期、把选中的选题排上（如：下周三要3条曝光、把周一那条改成信任向、把第2个排上、排上第1个 18:00 发、定了这个）\n"
        "确认已发布：报告某条已发（如：8/3普娃逆袭已发布）\n"
        "反馈修改：对上次产出提出修改/重写意见（如：这个脚本不行、把开头改掉、再犀利一点）\n"
        "追问：针对上次产出提问/要解释依据（如：为什么选这个角度、第2个的依据是什么、这个数据哪来的）\n"
        "记素材：把用户提供的泛类内容/文章/灵感存进素材库，供日后出选题（如：记素材：这篇讲预算对比的文章、收下这段热点内容）\n"
        "看完整讨论：拉取并展示最近群聊讨论记录（如：看完整讨论、最近群里聊了啥）\n"
        "圆桌讨论：老板要求对某个主题/选题多视角集体讨论（如：开圆桌会议、大家讨论下第二条选题、圆桌一下）\n"
        "学规则：让席小习从修改稿里提炼写作规则，供以后遵守（如：学一下、记住这个改法、以后都这样写、提炼个规则）\n"
        "确认规则：老板对席小习提炼的待确认规则拍板（如：确认、可以记下来、不用了、算了）\n"
        "数据回流：报告/查看某条已发内容的数据，或要复盘分析（如：8/5普娃逆袭 播放2.1w 留资23、回填这条数据、复盘一下）\n"
        "其他：闲聊或无以上意图\n"
        "\n"
        "【硬约束】\n"
        "- params 可用字段（没有就省略）：count（数量）、topic（角度/主题/信息差）、"
        "date（日期）、content_type（内容类型：曝光/留资/信任）。\n"
        "- task 可省略；给出了就一定是规范任务描述，不能是口语原句。\n"
        "- 意图必须在上面的列表内，找不到就归「其他」。\n"
        "\n"
        "【出错与不确定】\n"
        "- 拿不准归哪类，宁归「其他」，不瞎猜意图。\n"
    )
    if history_text:
        user = f"历史对话：\n{history_text}\n当前消息：{content}"
    else:
        user = f"消息：{content}"
    out = generate(system, user, max_tokens=800, temperature=0.2)
    return _parse(out, content)

def _parse(text: str, raw: str) -> IntentResult:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return IntentResult("其他", {}, raw)
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return IntentResult("其他", {}, raw)
    intent = data.get("intent") if data.get("intent") in INTENTS else "其他"
    params = data.get("params") if isinstance(data.get("params"), dict) else {}
    task = data.get("task") if isinstance(data.get("task"), str) else ""
    return IntentResult(intent, params, raw, task)

_REVISE_HINTS = ["不行", "重写", "改掉", "换个", "别", "改一下", "再犀利", "太生硬", "太软", "改改"]
_ASK_HINTS = ["为什么", "依据", "哪来的", "怎么来的", "解释一下", "说明一下"]
_LEARN_HINTS = ["学一下", "记住", "以后都这样", "提炼规则", "学个规则", "学这条", "这个改法", "记下来这条"]
_CONFIRM_WORDS = ("确认", "没问题", "按这个来", "可以记下来", "不用了", "算了", "别学了", "撤销", "这条对吗")
_DATA_METRICS = ("播放", "点赞", "互动", "转发", "观看", "浏览")

def by_keyword(content: str) -> IntentResult:
    if "已发布" in content:
        return IntentResult("确认已发布", {}, content)
    if rules.has_pending() and len(content) <= 30 and any(w in content for w in _CONFIRM_WORDS):
        return IntentResult("确认规则", {}, content)
    if any(w in content for w in ("回流", "复盘", "回填")):
        return IntentResult("数据回流", {}, content)
    if any(k in content for k in _DATA_METRICS) and re.search(r"\d", content):
        return IntentResult("数据回流", {}, content)
    if ("改排期" in content
            or ("排期" in content and ("改" in content or "要" in content or "加" in content))
            or any(k in content for k in ("排上", "排一下", "定了"))):
        return IntentResult("改排期", {"count": _extract_count(content), "content_type": "曝光", "date": "下周"}, content)
    if any(h in content for h in _LEARN_HINTS):
        return IntentResult("学规则", {}, content)
    if any(h in content for h in _REVISE_HINTS):
        return IntentResult("反馈修改", {}, content)
    if any(h in content for h in _ASK_HINTS):
        return IntentResult("追问", {}, content)
    if any(k in content for k in ("记素材", "收素材", "存素材", "素材入库", "收下")):
        return IntentResult("记素材", {}, content)
    if "圆桌" in content or "讨论下" in content or "讨论一下" in content or ("大家" in content and "讨论" in content):
        return IntentResult("圆桌讨论", {}, content)
    if ("完整讨论" in content
            or ("讨论" in content and ("看" in content or "最近" in content))
            or ("聊" in content and "看" in content)):
        return IntentResult("看完整讨论", {}, content)
    if "选题" in content or "角度" in content:
        return IntentResult("出选题", {"count": _extract_count(content)}, content)
    if "脚本" in content or "台本" in content:
        return IntentResult("写脚本", {}, content)
    if "排期表" in content:
        return IntentResult("看排期表", {}, content)
    if "曝光" in content or "留资" in content or "信任" in content:
        return IntentResult("改排期", {"count": _extract_count(content), "content_type": "曝光", "date": "下周"}, content)
    return IntentResult("其他", {}, content)

def _extract_count(content: str) -> str:
    m = re.search(r"(\d+)\s*个", content)
    return m.group(1) if m else "3"
