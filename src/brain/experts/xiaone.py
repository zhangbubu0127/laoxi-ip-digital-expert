import json, re

from brain.llm import generate as _default_generate
from brain.knowledge import load_file
from brain.rules import render_rules
from brain.experts.base import Expert
from brain import search as _search

REDLINES = [
    "保证录取", "包录取", "100%录取", "包拿毕业证", "保毕业", "保录取",
    "国立大学直录", "南洋理工直录", "新加坡国立大学直录", "南洋理工大学直录",
    "北大", "新加坡绿卡", "移民", "零门槛",
]

class XiaoneExpert(Expert):
    name = "席小核"

    def __init__(self, generate=_default_generate, verify=None):
        self._generate = generate
        self._verify = verify or _search.verify

    def handle(self, task: str, context: str = "") -> str:
        hits = [w for w in REDLINES if w in task]
        if hits:
            return f"❌ 红线：命中 {hits}，需改。"
        redlines = load_file("合规红线/合规红线.md")
        facts = load_file("业务事实/费用数据.md")
        system = (
            "你是【席小核】，老席留学IP团队的审核+核实专家，老席发稿前的最后一道闸。\n"
            "真人参照：老席团队里最较真的审稿人。他盯的不是文笔，是三样：这话会不会让老席惹麻烦（合规红线）、\n"
            "这数字是不是真的（事实）、这语气是不是老席本人（风格）。他下结论干脆：红线就毙，黄线就改，\n"
            "没把握就挂「需老板确认」。\n"
            "\n"
            "【默认立场】\n"
            "- 先想风险，再谈好坏。审核以「能不能发」为准，不是以「好不好看」为准。\n"
            "- 不确定的事实必须标注「需老板确认」，绝不编造。\n"
            "\n"
            "【沟通风格与格式】\n"
            "- 直接输出结果，不要思考过程。\n"
            "- 输出不要带【席小核】等自我标注前缀，直接以审核人身份说话。\n"
            "- 收到闲聊/非任务消息：先自然共情接话，再绕回审核工作主动建议下一步，别死板套 JSON。\n"
            "- 先输出一行【审核结论】：用大白话给判定（红线/黄线/通过）+ 关键理由，80字以内，像审稿人当面说人话。\n"
            "- 紧接着输出一行 JSON：{\"judgement\":\"红线/黄线/通过 判定+每条理由\","
            "\"claims\":[{\"fact\":\"需要联网核实的客观事实\"}]}\n"
            "- claims 只列脚本中出现的、可独立核实的客观事实（院校/排名/费用/政策/数据），没有则给空数组。\n"
            "\n"
            "【行为规则】\n"
            "1. 风格红线审核（自称老席、无书面过渡词、反常识钩子）。\n"
            "2. 库内事实核实：费用数字等必须与知识库一致。\n"
            "3. 库外事实用外核核实，查不到标「需老板确认 · 低置信度」。\n"
            "\n"
            "【硬约束】\n"
            "- 红线词本地先拦（REDLINES 命中直接判红线）。\n"
            "- 已确认规则必须对齐（老板偏好）。\n"
            "\n"
            f"【合规红线】\n{redlines}\n"
            f"【费用数据（唯一事实来源）】\n{facts}\n"
            f"【已确认规则（老板偏好，审核须对齐）】\n{render_rules()}\n"
        )
        user = f"审核以下脚本，给出判定：\n{task}"
        text = self._generate(system, user)
        claims = _parse_claims(text)
        if not claims:
            return text
        return text + "\n\n" + self._verify_block(claims)

    def _verify_block(self, claims: list) -> str:
        lines = []
        for claim in claims[:3]:
            try:
                hits = self._verify(claim)
            except Exception:
                hits = []
            if not hits:
                lines.append(f"【外核】{claim} → 未查到，标「需老板确认 · 低置信度」")
                continue
            top = hits[0]
            conf = "高" if claim[:4] in top["title"] else "中"
            lines.append(f"【外核】{claim} → {top['title']} · {top['url']} · 置信度{conf}")
        return "\n".join(lines)

def _parse_claims(text: str) -> list:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    return [str(c["fact"]).strip() for c in data.get("claims", [])
            if isinstance(c, dict) and c.get("fact")][:3]
