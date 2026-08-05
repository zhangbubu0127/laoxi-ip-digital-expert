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

# 公开结论与详细理由的分隔线：公开段发群，详情段存后台（老板要理由才展示）
REVIEW_SPLIT = "===审核详情==="

class XiaoneExpert(Expert):
    name = "席小核"

    def __init__(self, generate=_default_generate, verify=None):
        self._generate = generate
        self._verify = verify or _search.verify

    def handle(self, task: str, context: str = "") -> str:
        # 脚本在 context（后台），红线本地检查要连 task 一起扫
        script = f"{task}\n{context}".strip()
        hits = [w for w in REDLINES if w in script]
        if hits:
            return self._fail_reply(hits)
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
            "【输出格式（必须严格遵守，别漏别改）】\n"
            "- 第一行【审核结论】：只写三选一 —— 通过 / 不通过 / 需老板确认。\n"
            "- 如有疑问：下一段用大白话列出需要老板确认的疑问点（1-3 条）。\n"
            "- 再下一行固定写「需要给您展示理由吗？」。\n"
            "- 然后空行，写一行 {REVIEW_SPLIT}，再之后是【审核详情】段：这是必须交付的存档内容（老板要看理由就展示它），\n"
            "  不是可省的推理过程。无论结论是「通过」还是「不通过」，都要用中文大白话逐条写 2-4 条判定依据\n"
            "  （合规/事实/风格，每条说人话），以及外核结果；禁止出现 judge/claims/fact 等英文术语键，全部翻译成大白话。\n"
            "- 把需要联网核实的客观事实（院校/排名/费用/政策/数据）单独放详情段末尾的 JSON 里："
            "{{\"claims\":[{{\"fact\":\"客观事实\"}}]}}（这一行只作后台外核提取，不进对话）。\n"
            "\n"
            "【行为规则】\n"
            "1. 风格红线审核（自称老席、无书面过渡词、反常识钩子）。\n"
            "2. 库内事实核实：费用数字等必须与知识库一致。\n"
            "3. 库外事实用外核核实，查不到标「需老板确认 · 低置信度」。\n"
            "4. 审核是输出任务：不要展示内心推理草稿，直接给结论；但【审核详情】段是交付内容的一部分，必须写完。\n"
            "\n"
            "【理解老板的话（举例）】\n"
            "- 老板说「你审这么严干嘛」→ 别改标准，先解释为什么拦，再问要不要放宽（放宽要老板明说）。\n"
            "- 老板说「这条是不是能发」→ 不是闲聊，是要结论：直接给 通过/不通过/需老板确认。\n"
            "- 老板说「把那条的审核理由给我」→ 指你刚审的那条，给大白话理由，不给英文术语。\n"
            "- 老板说「费用那个数字你查下」→ 是要外核事实，去核，别凭记忆答。\n"
            "- 被老板直接找/闲聊：先共情接话，再绕回审核工作建议下一步；建议可执行的下一步时把动作名用【】括进建议句"
            "（如「要不要我【审核脚本】把这条再过一遍？」），动作名限：重新出选题/写脚本/审核脚本。\n"
            "\n"
            "【硬约束】\n"
            "- 红线词本地先拦（REDLINES 命中直接判不通过，无需再走模型）。\n"
            "- 已确认规则必须对齐（老板偏好）。\n"
            "\n"
            f"【合规红线】\n{redlines}\n"
            f"【费用数据（唯一事实来源）】\n{facts}\n"
            f"【已确认规则（老板偏好，审核须对齐）】\n{render_rules()}\n"
        )
        user = f"审核以下脚本：\n{task}"
        if context:
            user += f"\n\n【脚本原文】\n{context}"
        # 审核 prompt 同样注入知识库（1万+ token），推理模型 thinking 可能吃满默认预算空输出，预算给 12000 防空
        text = self._generate(system, user, max_tokens=12000)
        claims = _parse_claims(text)
        if not claims:
            return text
        return text + "\n\n" + self._verify_block(claims)

    def explain(self, question: str, context: str = "") -> str:
        system = (
            "你是【席小核】，老席留学IP团队的审核专家。老板针对你刚给的审核结论追问理由。\n"
            "直接回答，用中文大白话把审核理由逐条讲清（合规/事实/风格），禁英文术语，不编造，简洁。\n"
        )
        user = f"【本次审核详情】\n{context}\n\n【老板问】\n{question}"
        return self._generate(system, user)

    def _fail_reply(self, hits: list) -> str:
        words = "、".join(hits)
        return (
            f"【审核结论】不通过\n"
            f"脚本里出现红线词：{words}，这类承诺性说法绝不能发。\n"
            f"需要给您展示理由吗？\n"
            f"\n{REVIEW_SPLIT}\n"
            f"命中「{words}」属于合规红线（明令禁止的保录取/保毕业/直录等承诺），直接判不通过，不能放行。"
        )

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

def split_review(text: str):
    """拆「公开结论」与「审核详情」；详情段剥掉 JSON 供后台存档。"""
    if REVIEW_SPLIT in text:
        public, _, details = text.partition(REVIEW_SPLIT)
        public = _strip_json(public).strip()
        details = _strip_json(details).strip()
        if public:
            return public, details
        return details, ""  # LLM 没给公开段，全当公开
    return _strip_json(text).strip(), ""

def _strip_json(text: str) -> str:
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        text = text[:m.start()] + text[m.end():]
    return text.strip()

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
