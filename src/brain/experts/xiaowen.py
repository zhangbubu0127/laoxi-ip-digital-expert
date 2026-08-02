from brain.experts.base import Expert

class XiaowenExpert(Expert):
    name = "席小文"

    def handle(self, task: str) -> str:
        return (
            "脚本 v1：\n"
            f"【钩子】{task}，家里没矿也能走\n"
            "【数字】15岁入学、8万/年学费\n"
            "【收口】你想让孩子受苦，那就别去新加坡\n"
            "【CTA】后台滴滴我，发路线图"
        )
