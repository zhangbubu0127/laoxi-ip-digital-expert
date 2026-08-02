from base import Expert

class XiaotiExpert(Expert):
    name = "席小题"

    def handle(self, task: str) -> str:
        return (
            "推荐3个选题（基于调研+知识库）：\n"
            "1.【留资】预算不够？这条路线最省\n"
            "2.【曝光】别去新加坡？那去哪\n"
            "3.【信任】老席13年为什么不涨价"
        )
