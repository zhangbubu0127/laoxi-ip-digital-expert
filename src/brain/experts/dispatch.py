import json, re

from brain.experts.xiaoti import XiaotiExpert
from brain.experts.xiaowen import XiaowenExpert
from brain.experts.xiaone import XiaoneExpert
from brain.experts.xiaoxi import XiaoxiExpert
from brain.experts.fupan import FupanExpert
from brain import reflow, rules

EXPLAIN_MARK = "【追问】"
LEARN_MARK = "【学规则】"
RESEARCH_MARK = "【调研】"
LEARN_MAX = 3

# 派单任务开头可带风格指令：小席决定这次按谁的风格写/学，主控→专家bot 唯一通道就是任务文本，随行传递
_STYLE_RE = re.compile(r"^【风格[:：]([^】]+)】\s*")


def split_style(task: str) -> tuple[str, str]:
    """从派单任务开头剥离风格指令，返回 (风格用户, 剩余任务)。无指令→老席。"""
    m = _STYLE_RE.match(task or "")
    if m:
        return m.group(1).strip(), (task[m.end():] or "").strip()
    return "老席", (task or "").strip()

_REAL_FACTORIES = {
    "席小题": XiaotiExpert,
    "席小文": XiaowenExpert,
    "席小核": XiaoneExpert,
    "席小习": XiaoxiExpert,
    "席小盘": FupanExpert,
}


def run_expert(role: str, task: str, context: str = "", factories=None) -> str:
    fac = factories or _REAL_FACTORIES
    if role == "席小题":
        if task.startswith(RESEARCH_MARK):
            return fac["席小题"]().research(task[len(RESEARCH_MARK):].strip())
        if task.startswith(EXPLAIN_MARK):
            return fac["席小题"]().explain(task[len(EXPLAIN_MARK):].strip(), context=context)
        return fac["席小题"]().handle(task, context=context)
    if role == "席小文":
        style_user, clean = split_style(task)
        wen = fac["席小文"](style_user=style_user) if style_user != "老席" else fac["席小文"]()
        if clean.startswith(EXPLAIN_MARK):
            return wen.explain(clean[len(EXPLAIN_MARK):].strip(), context=context)
        return wen.handle(clean, context=context)
    if role == "席小核":
        if task.startswith(EXPLAIN_MARK):
            return fac["席小核"]().explain(task[len(EXPLAIN_MARK):].strip(), context=context)
        return fac["席小核"]().handle(task, context=context)
    if role == "席小习":
        style_user, clean = split_style(task)
        return learn_rules_flow(_strip_mark(clean), context, fac, style_user=style_user)
    if role == "席小盘":
        return run_reflow_flow(task, fac)
    raise KeyError(f"未知专家角色「{role}」")


def learn_rules_flow(content: str, prev: str, factories=None, style_user: str = "") -> str:
    fac = factories or _REAL_FACTORIES
    try:
        text = fac["席小习"]().handle(content, context=prev)
    except Exception:
        return "席小习提炼失败，稍后再试或重新贴修改稿"
    pairs = parse_learned_rules(text)
    if not pairs:
        return "席小习没提炼出规则，把修改前后都贴全一点再试"
    ids = [rules.add_rule(change, rule, style_user=style_user, rtype=rtype)
           for change, rule, rtype in pairs[:LEARN_MAX]]
    pending = rules.render_rules([r for r in rules.load_rules(style_user) if r["id"] in ids], style_user=style_user)
    return f"【席小习】学到的规则（{len(ids)} 条，待你确认）：\n{pending}\n回「确认」写入风格档案；回「不用了」丢弃。"


def run_reflow_flow(task: str, factories=None) -> str:
    fac = factories or _REAL_FACTORIES
    parsed = _parse_reflow_task(task)
    if not parsed:
        return "数据格式不对，示例：日期8/3 选题普娃逆袭 数据：播放2.1w 留资23"
    date, topic, data = parsed
    reflow.append_reflow(date, topic, data)
    try:
        conclusion = fac["席小盘"]().handle(f"日期 {date} 选题 {topic} 数据：{data}")
    except Exception:
        conclusion = "（复盘分析暂不可用，数据已入库）"
    reflow.append_conclusion(conclusion)
    return conclusion


def parse_learned_rules(text: str) -> list:
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            items = data.get("rules", []) if isinstance(data, dict) else []
            pairs = [(str(i.get("change", "")).strip(), str(i.get("rule", "")).strip(),
                      str(i.get("type", "")).strip())
                     for i in items if isinstance(i, dict)]
            if any(c and r for c, r, _ in pairs):
                return pairs
        except ValueError:
            pass
    pairs = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("提炼规则"):
            rule = line.split("：", 1)[-1].strip()
            pairs.append(("", rule, ""))
    return pairs


def _parse_reflow_task(task: str):
    m = re.search(r"日期\s*(.*?)\s*选题\s*(.*?)\s*数据[:：]\s*(.+)", task)
    if not m:
        return None
    date, topic, data = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    if not (date and topic and data):
        return None
    return date, topic, data


def _strip_mark(task: str) -> str:
    if task.startswith(LEARN_MARK):
        return task[len(LEARN_MARK):].strip()
    return task.strip()
