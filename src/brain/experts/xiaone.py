from base import Expert

REDLINES = ["保证录取", "包录取", "国立大学直录", "南洋理工直录", "北大预科"]

class XiaoneExpert(Expert):
    name = "席小核"

    def handle(self, task: str) -> str:
        hits = [w for w in REDLINES if w in task]
        if hits:
            return f"❌ 红线：命中 {hits}，需改。"
        return "✅ 通过：无红线，可进入发布流程。"
