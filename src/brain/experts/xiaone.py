from brain.llm import generate as _default_generate
from brain.knowledge import load_file
from brain.experts.base import Expert

REDLINES = [
    "保证录取", "包录取", "100%录取", "包拿毕业证", "保毕业", "保录取",
    "国立大学直录", "南洋理工直录", "新加坡国立大学直录", "南洋理工大学直录",
    "北大", "新加坡绿卡", "移民", "零门槛",
]

class XiaoneExpert(Expert):
    name = "席小核"

    def __init__(self, generate=_default_generate):
        self._generate = generate

    def handle(self, task: str) -> str:
        hits = [w for w in REDLINES if w in task]
        if hits:
            return f"❌ 红线：命中 {hits}，需改。"
        redlines = load_file("合规红线/合规红线.md")
        facts = load_file("业务事实/费用数据.md")
        system = (
            "你是【席小核】，老席留学IP团队的审核+核实专家。\n"
            "审核规则：\n"
            "1. 风格红线审核（自称老席、无书面过渡词、反常识钩子）。\n"
            "2. 库内事实核实：费用数字等必须与知识库一致。\n"
            "3. 不确定的事实必须标注「需老板确认」，绝不编造。\n"
            "输出：红线/黄线/通过 判定 + 每条理由。\n"
            f"【合规红线】\n{redlines}\n"
            f"【费用数据（唯一事实来源）】\n{facts}\n"
        )
        user = f"审核以下脚本，给出判定：\n{task}"
        return self._generate(system, user)
