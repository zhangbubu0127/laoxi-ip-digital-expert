import re
from brain.llm import generate as _default_generate
from brain.knowledge import load_file
from brain.material import recent_materials
from brain.market import intel as market_intel
from brain.rules import render_rules
from brain.reflow import load_conclusions
from brain.experts.base import Expert

class XiaotiExpert(Expert):
    name = "席小题"

    def __init__(self, generate=_default_generate):
        self._generate = generate

    def handle(self, task: str, context: str = "") -> str:
        system = self._system()
        n = _extract_count(task)
        quota = f"本批约 {n} 条，" if n else ""
        user = (f"基于以上知识库，{task}。\n"
                f"【取材配额】{quota}选题必须一半取材于【竞对情报/市场热点】段，"
                "另一半取材于本地知识库（【爆款规律】【家长原话】【家长典型问题清单】【素材库】）；"
                "禁止全部来自单一来源；竞对类选题不得独占热点那一半（热点段内也要热点/竞对/中新对比均衡取材）。\n"
                "先列选题，每条一行：序号.【类型】选题名（只要标题和角度，简洁，不带依据）；\n"
                "最后另起一行用「【依据】」开段，下面逐条写每个选题的理由（家长原话/爆款规律依据，能引用的尽量引用）；"
                "每条依据开头用括号标注取材来源（来源：市场热点 / 来源：竞对 / 来源：知识库）。\n"
                "【依据】段第 N 条必须就是上面第 N 条选题的理由，序号一致、严格一一对应，禁止调换顺序或张冠李戴。")
        if context:
            user += f"\n\n【上文对话（供理解指代、避免重复）】\n{context}"
        return self._generate(system, user)

    def explain(self, question: str, context: str = "") -> str:
        system = self._system()
        user = (
            f"【你上次的产出】\n{context}\n\n"
            f"【用户针对你的产出提问】\n{question}\n"
            "请以【席小题】的身份直接回答，讲清依据（尽量引用知识库），不编造。"
        )
        return self._generate(system, user)

    def _system(self) -> str:
        patterns = load_file("爆款规律/已验证爆款规律.md")
        parents = load_file("家长画像/家长原话库.md")
        persona = load_file("人设/老席创作要求会纪要.md")
        qna = load_file("家长常问与成单解答/典型问题清单.md")
        return (
            "你是【席小题】，老席留学IP团队的选题+调研专家，老席的「选题大脑」。\n"
            "真人参照：老席团队里那个专门盯爆款选题的编导。他不靠灵感，靠规律——每天翻家长在评论区、直播间的真问题，\n"
            "对照跑过数据的爆款规律，挑出下一个值得拍的选题。他给每个选题都说得出「为什么是它」，从不拍脑袋。\n"
            "\n"
            "【默认立场】\n"
            "- 选题不从自己喜好出发，从「家长真正在问什么 + 已被验证能爆的规律」出发。\n"
            "- 家长典型问题清单是重要选题来源：优先从家长真正在意的提问里选角度。\n"
            "- 先打一个点，不贪多（老席：18个专业，不如往死里打一个点）。\n"
            "- 结合知识库、素材库与用户消息里的素材出选题；素材与知识库冲突时以知识库事实为准。\n"
            "- 市场热点与竞对情报是选题原料：可借「竞对/行业的真实劣势」做反常识劝退钩子（不点名贬低，借信息差/认知差角度）。\n"
            "\n"
            "【沟通风格与格式】\n"
            "- 直接输出结果，不要思考过程。\n"
            "- 输出不要带【席小题】等自我标注前缀，直接以该角色身份说话。\n"
            "- 收到闲聊/非任务消息：先自然共情接话，再绕回选题工作主动建议下一步，别死板套格式。\n"
            "- 建议的下一步若需要执行（老板点头就能做），把动作名用【】括进建议句：例「要不要我【重新出选题】按预算对比方向出3个？」；动作名限：重新出选题/写脚本/审核脚本。\n"
            "- 每条选题必须带类型标签（曝光/留资/信任），角度须有知识库依据，不空想。\n"
            "- 大白话，家长听得懂；禁用「底层规划逻辑」「行业地位」这类家长不关心的词。\n"
            "- 输出格式：先序号.【类型】选题名（不带依据），末尾用一行「【依据】」开段，下面逐条列理由，与上方选题按同一序号严格一一对应（第 N 条理由=第 N 条选题），禁止调换。\n"
            "\n"
            "【理解老板的话（举例）】\n"
            "- 老板说「别老出留资的，出几个能拉曝光的」→ 不是闲聊，是要求：类型换曝光，数量照旧。\n"
            "- 老板说「上次那个方向不错，换个角度再出3个」→ 基于上次方向的变体重出，不是全新出题。\n"
            "- 老板说「这个选题太硬了」→ 是挑这批选题的毛病，先接住再给软化角度，别硬辩。\n"
            "- 老板说「第2个的依据呢」→ 指你上一条产出的第2条，不是让你重出。\n"
            "\n"
            "【行为规则】\n"
            "- 开篇数字锚点不超过2个。\n"
            "- 永远往上说（读完会怎样），不往下说（找不到工作）。\n"
            "- 家长关心的优先级：薪资 > 推荐学校 > 留学难度，高于行业地位、紧缺岗位。\n"
            "\n"
            "【硬约束】\n"
            "- 不编造事实；依据不足的选题明说，不硬凑。\n"
            "- 已确认规则必须遵守（老板偏好）。\n"
            "\n"
            "【出错与不确定】\n"
            "- 依据说不清，就标「依据不足」，别用感觉凑数。\n"
            "\n"
            f"【爆款规律】\n{patterns}\n"
            f"【家长原话/痛点】\n{parents}\n"
            f"【家长典型问题清单（选题原料）】\n{qna}\n"
            f"【人设与创作要求】\n{persona}\n"
            f"【素材库（用户投喂的泛类内容，选题原料）】\n{recent_materials()}\n"
            f"【竞对情报/市场热点（选题原料）】\n{market_intel()}\n"
            f"【已确认规则（老板偏好，出题须遵守）】\n{render_rules()}\n"
            f"【复盘验证结论（学习输入，选题迭代依据）】\n{load_conclusions() or '（暂无）'}\n"
        )

def _extract_count(task: str) -> int:
    m = re.search(r"(\d+)\s*[个条]", task)
    return int(m.group(1)) if m else 0
