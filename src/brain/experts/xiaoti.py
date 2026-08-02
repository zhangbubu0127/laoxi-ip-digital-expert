from brain.llm import generate as _default_generate
from brain.knowledge import load_file
from brain.experts.base import Expert

class XiaotiExpert(Expert):
    name = "席小题"

    def __init__(self, generate=_default_generate):
        self._generate = generate

    def handle(self, task: str) -> str:
        patterns = load_file("爆款规律/已验证爆款规律.md")
        parents = load_file("家长画像/家长原话库.md")
        persona = load_file("人设/老席创作要求会纪要.md")
        system = (
            "你是【席小题】，老席留学IP团队的选题+调研专家。\n"
            "产出规则：每条选题必须带类型标签（曝光/留资/信任）+ 调研依据，角度须有知识库依据，不空想。\n"
            f"【爆款规律】\n{patterns}\n"
            f"【家长原话/痛点】\n{parents}\n"
            f"【人设与创作要求】\n{persona}\n"
        )
        user = f"基于以上知识库，出3个没拍过的选题，{task}。格式：1.【类型】选题名（依据）。"
        return self._generate(system, user)
