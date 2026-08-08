import re
from brain import rules

# 意图分类已迁到小席规划器（planner.py）；本模块只留 by_keyword 作 LLM 崩溃兜底。

class IntentResult:
    def __init__(self, intent="其他", params=None, raw="", task=""):
        self.intent = intent
        self.params = params or {}
        self.raw = raw
        self.task = task

_REVIEW_HINTS = ["审核", "审一下", "审审", "复审", "过一遍", "把把关"]
# 群内加列/改表结构指令：须在改排期、写脚本判定之前命中，避免「单开一列放脚本内容」被误判成改排期/写脚本
_TABLE_HINTS = ("加一列", "再加一列", "多加一列", "单开一列", "新开一列", "多开一列", "开一列",
                "加个列", "加列", "加个字段", "加字段", "加一栏")
_REVISE_HINTS = ["不行", "重写", "改掉", "换个", "别", "改一下", "再犀利", "太生硬", "太软", "改改"]
_ASK_HINTS = ["为什么", "依据", "哪来的", "怎么来的", "解释一下", "说明一下", "理由"]
_LEARN_HINTS = ["学一下", "记住", "以后都这样", "提炼规则", "学个规则", "学这条", "这个改法", "记下来这条"]
_CONFIRM_WORDS = ("确认", "没问题", "按这个来", "可以记下来", "不用了", "算了", "别学了", "撤销", "这条对吗")
_DATA_METRICS = ("播放", "点赞", "互动", "转发", "观看", "浏览")
_MARKET_HINTS = ("更新情报", "更新市场", "搜竞对", "看竞对", "竞对是谁", "竞对有哪些",
                 "搜热点", "今天什么话题热", "现在什么话题热", "什么热点", "热点是什么",
                 "刷新情报", "刷新市场情报")
WRITE_SCRIPT_HINTS = ("写条", "写一条", "写个", "出条", "出个", "出内容", "出脚本", "做内容",
                      "做成内容", "按这个出", "根据这个出", "就按这个出", "来一条",
                      "出个60秒", "出条60秒", "出个内容", "文案", "台本", "脚本")
# 「查记录」兜底：老板问历史产出还在不在/在哪。须「回顾对象+回顾性动词」同时出现，避免误伤「把那条选题写条脚本」
_LOOKUP_HINTS = (("选题", "还能看到"), ("选题", "能看见"), ("选题", "还能看见"), ("选题", "看见"),
                 ("选题", "还在"), ("选题", "在哪"), ("选题", "哪去了"), ("选题", "找不回来"),
                 ("选题", "丢了"), ("选题", "丢"), ("选题", "回顾"), ("选题", "还记得"),
                 ("选题", "想起来"), ("选题", "原始记录"), ("选题", "找不回"),
                 ("脚本", "还在"), ("脚本", "还能看到"), ("脚本", "哪去了"),
                 ("上次", "还能看到"), ("上次", "还能看见"), ("刚才", "还能看到"), ("刚才", "还能看见"),
                 ("刚才", "找不到了"), ("上次", "找不到了"))
_USED_HINTS = ("已用选题", "用过的选题", "已采用的", "历史选题", "用过哪些", "用过的")

def by_keyword(content: str) -> IntentResult:
    if "已发布" in content:
        return IntentResult("确认已发布", {}, content)
    if "确认" in content and "排期" in content:
        return IntentResult("确认排期", {}, content)
    if rules.has_pending() and len(content) <= 30 and any(w in content for w in _CONFIRM_WORDS):
        return IntentResult("确认规则", {}, content)
    if any(w in content for w in ("回流", "复盘", "回填")):
        return IntentResult("数据回流", {}, content)
    if any(k in content for k in _DATA_METRICS) and re.search(r"\d", content):
        return IntentResult("数据回流", {}, content)
    if any(h in content for h in _REVIEW_HINTS):
        return IntentResult("审核脚本", {}, content)
    if any(h in content for h in _TABLE_HINTS):
        return IntentResult("改表格结构", {}, content)
    if (("通过" in content and "选题" in content)
            or ("排期" in content and "发布" in content)
            or ("上传" in content and ("选题" in content or "类型" in content))):
        return IntentResult("改排期", {}, content)
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
    if any(h in content for h in _MARKET_HINTS):
        return IntentResult("更新市场情报", {}, content)
    if "圆桌" in content or "讨论下" in content or "讨论一下" in content or ("大家" in content and "讨论" in content):
        return IntentResult("圆桌讨论", {}, content)
    if ("完整讨论" in content
            or ("讨论" in content and ("看" in content or "最近" in content))
            or ("聊" in content and "看" in content)):
        return IntentResult("看完整讨论", {}, content)
    if any(a in content and b in content for a, b in _LOOKUP_HINTS):
        return IntentResult("查记录", {}, content)
    # 「查已用选题」须在「出选题」判定之前，否则「看已用选题」会被「选题」误吞
    if any(h in content for h in _USED_HINTS):
        return IntentResult("查已用选题", {}, content)
    if "选题" in content or "角度" in content:
        return IntentResult("出选题", {"count": _extract_count(content)}, content)
    if any(h in content for h in WRITE_SCRIPT_HINTS):
        return IntentResult("写脚本", {}, content)
    if "排期表" in content:
        return IntentResult("看排期表", {}, content)
    if "曝光" in content or "留资" in content or "信任" in content:
        return IntentResult("改排期", {"count": _extract_count(content), "content_type": "曝光", "date": "下周"}, content)
    return IntentResult("其他", {}, content)

def _extract_count(content: str) -> str:
    m = re.search(r"(\d+)\s*个", content)
    return m.group(1) if m else "3"
