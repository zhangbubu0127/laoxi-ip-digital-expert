import unittest, tempfile
from brain.experts.base import Expert
from brain.experts.xiaoti import XiaotiExpert
from brain.experts.xiaowen import XiaowenExpert
from brain.experts.xiaone import XiaoneExpert
from brain.experts.xiaoxi import XiaoxiExpert
from brain import material

class FakeGen:
    def __init__(self):
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        return "【测试】生成结果"

class FakeGenReturn:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def __call__(self, system, user):
        self.calls.append((system, user))
        return self.text

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

    def test_xiaowen_prompt_has_confirmed_rules(self):
        fg = FakeGen()
        XiaowenExpert(generate=fg).handle("普娃预算不够")
        self.assertIn("已确认规则", fg.calls[0][0])

    def test_xiaowen_script_format_spec(self):
        fg = FakeGen()
        XiaowenExpert(generate=fg).handle("写条脚本，关于预算对比")
        user = fg.calls[0][1]
        self.assertIn("【席小文】## 脚本 v1", user)
        self.assertIn("【口播正文】", user)
        self.assertIn("## 结构拆解", user)
        self.assertIn("| 时间段 | 内容 | 目的 |", user)
        self.assertIn("## 封面文字", user)
        self.assertIn("**主封面（3选1）**", user)
        self.assertIn("## 钩子词", user)
        self.assertIn("**评论区关键词**", user)
        self.assertIn("## 投放建议", user)

    def test_xiaowen_revision_bumps_version(self):
        fg = FakeGen()
        XiaowenExpert(generate=fg).handle("修改：把开头改成先讲结果")
        self.assertIn("脚本 v2", fg.calls[0][1])

    def test_xiaoxi_prompts_for_rule_json(self):
        fg = FakeGen()
        out = XiaoxiExpert(generate=fg).handle("把开头改成先讲结果", context="【AI原版】旧文")
        self.assertIn("测试", out)
        system = fg.calls[0][0]
        self.assertIn("rules", system)
        self.assertIn("change", system)
        self.assertIn("rule", system)

    def test_xiaone_verifies_external_claims(self):
        fg = FakeGenReturn('{"judgement":"通过","claims":[{"fact":"新加坡国立大学"}]}')
        def fake_verify(query):
            return [{"title": "新加坡国立大学", "url": "https://zh.wikipedia.org/wiki/新加坡国立大学"}]
        verdict = XiaoneExpert(generate=fg, verify=fake_verify).handle("新加坡留学，老席帮你算笔账")
        self.assertIn("【外核】新加坡国立大学", verdict)
        self.assertIn("置信度高", verdict)

    def test_xiaone_unverifiable_marks_need_boss(self):
        fg = FakeGenReturn('{"judgement":"通过","claims":[{"fact":"某校QS第5"}]}')
        verdict = XiaoneExpert(generate=fg, verify=lambda q: []).handle("脚本")
        self.assertIn("需老板确认", verdict)
        self.assertIn("低置信度", verdict)

    def test_xiaone_no_claims_skips_external(self):
        fg = FakeGenReturn("通过，无红线。")
        verdict = XiaoneExpert(generate=fg, verify=lambda q: [{"title": "x", "url": "u"}]).handle("脚本")
        self.assertNotIn("【外核】", verdict)

    def test_xiaoti_prompt_includes_material_library(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            material._MATERIAL_DIR = tmp.name
            material.save_material("普娃预算对比的热点文章要点")
            fg = FakeGen()
            XiaotiExpert(generate=fg).handle("帮我出3个选题")
            system = fg.calls[0][0]
            self.assertIn("素材库", system)
            self.assertIn("普娃预算对比的热点文章要点", system)
        finally:
            tmp.cleanup()

    def test_xiaoti_prompt_includes_parent_question_list(self):
        fg = FakeGen()
        XiaotiExpert(generate=fg).handle("帮我出3个选题")
        system = fg.calls[0][0]
        self.assertIn("家长典型问题清单", system)
        self.assertIn("初中毕业真的能直接读大学吗？", system)

    def test_xiaowen_prompt_loads_topic_category_qna(self):
        fg = FakeGen()
        XiaowenExpert(generate=fg).handle("写条脚本，关于预算对比")
        system = fg.calls[0][0]
        self.assertIn("家长常问与成单解答", system)
        self.assertIn("北京预科这一年学费是多少？", system)
        self.assertNotIn("初中毕业真的能直接读大学吗？", system)

    def test_xiaoti_prompt_has_rules_and_conclusions(self):
        fg = FakeGen()
        XiaotiExpert(generate=fg).handle("帮我出3个选题")
        system = fg.calls[0][0]
        self.assertIn("已确认规则", system)
        self.assertIn("复盘验证结论", system)

    def test_xiaone_prompt_has_confirmed_rules(self):
        fg = FakeGen()
        XiaoneExpert(generate=fg).handle("新加坡留学，老席帮你算笔账")
        system = fg.calls[0][0]
        self.assertIn("已确认规则", system)

    def test_xiaowen_prompt_shows_no_conclusions_when_file_absent(self):
        from brain import reflow as reflow_mod
        old = reflow_mod._CONCLUSION_PATH
        reflow_mod._CONCLUSION_PATH = "/nonexistent/验证结论.md"
        try:
            fg = FakeGen()
            XiaowenExpert(generate=fg).handle("普娃预算不够")
            system = fg.calls[0][0]
            self.assertIn("复盘验证结论", system)
            self.assertIn("（暂无）", system)
        finally:
            reflow_mod._CONCLUSION_PATH = old

    def test_xiaoti_prompt_shows_no_conclusions_when_file_absent(self):
        from brain import reflow as reflow_mod
        old = reflow_mod._CONCLUSION_PATH
        reflow_mod._CONCLUSION_PATH = "/nonexistent/验证结论.md"
        try:
            fg = FakeGen()
            XiaotiExpert(generate=fg).handle("帮我出3个选题")
            system = fg.calls[0][0]
            self.assertIn("（暂无）", system)
        finally:
            reflow_mod._CONCLUSION_PATH = old

if __name__ == "__main__":
    unittest.main()
