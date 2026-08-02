import unittest
from brain.experts.base import Expert
from brain.experts.xiaoti import XiaotiExpert
from brain.experts.xiaowen import XiaowenExpert
from brain.experts.xiaone import XiaoneExpert

class FakeGen:
    def __init__(self):
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        return "【测试】生成结果"

class TestExperts(unittest.TestCase):
    def test_xiaoti_uses_knowledge_and_llm(self):
        fg = FakeGen()
        out = XiaotiExpert(generate=fg).handle("信息差角度")
        self.assertIn("测试", out)
        self.assertTrue(any("类型标签" in s for s, _ in fg.calls))
        self.assertTrue(any("家长原话" in s for s, _ in fg.calls))

    def test_xiaowen_prompt_has_fee_and_style_constraints(self):
        fg = FakeGen()
        XiaowenExpert(generate=fg).handle("普娃预算不够")
        system = fg.calls[0][0]
        self.assertIn("费用数字必须与知识库一致", system)
        self.assertIn("自称「老席」", system)
        self.assertIn("无书面过渡词", system)

    def test_xiaone_rejects_redline_word_locally(self):
        bad = "老席保证录取，费用只要8万一年"
        verdict = XiaoneExpert(generate=FakeGen()).handle(bad)
        self.assertIn("红线", verdict)

    def test_xiaone_passes_clean_via_llm(self):
        fg = FakeGen()
        good = "新加坡留学，老席帮你算笔账"
        verdict = XiaoneExpert(generate=fg).handle(good)
        self.assertIn("测试", verdict)
        self.assertEqual(len(fg.calls), 1)

if __name__ == "__main__":
    unittest.main()
