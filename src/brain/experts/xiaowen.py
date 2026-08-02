from brain.llm import generate as _default_generate
from brain.knowledge import load_file
from brain.experts.base import Expert

class XiaowenExpert(Expert):
    name = "席小文"

    def __init__(self, generate=_default_generate):
        self._generate = generate

    def handle(self, task: str) -> str:
        persona = load_file("人设/老席创作要求会纪要.md")
        facts = load_file("业务事实/费用数据.md")
        patterns = load_file("爆款规律/已验证爆款规律.md")
        redlines = load_file("合规红线/合规红线.md")
        system = (
            "你是【席小文】，老席留学IP团队的资深写手，写作铁律：\n"
            "1. 自称「老席」，用老席第一人称（北方口语、反常识劝退钩子）。\n"
            "2. 费用数字必须与知识库一致，禁止编造。\n"
            "3. 无书面过渡词。\n"
            "4. 合规红线零容忍，命中立即规避。\n"
            f"【人设与创作要求】\n{persona}\n"
            f"【费用数据（唯一事实来源）】\n{facts}\n"
            f"【爆款规律】\n{patterns}\n"
            f"【合规红线】\n{redlines}\n"
        )
        user = f"基于以上，写一条短视频脚本：{task}。输出：脚本 v1 + 结构拆解 + 封面文字 + 钩子词 + 投放建议。"
        return self._generate(system, user)
