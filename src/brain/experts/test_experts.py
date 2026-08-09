import unittest, tempfile
from unittest import mock
from brain.experts.base import Expert
from brain.experts.xiaoti import XiaotiExpert
from brain.experts.xiaowen import XiaowenExpert
from brain.experts.xiaone import XiaoneExpert, split_review
from brain.experts.xiaoxi import XiaoxiExpert
from brain import material

class FakeGen:
    def __init__(self):
        self.calls = []

    def __call__(self, system, user, **kwargs):
        self.calls.append((system, user))
        return "【测试】生成结果"

class FakeGenReturn:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def __call__(self, system, user, **kwargs):
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

    def test_xiaowen_threads_style_user(self):
        # 非老席风格 → 加载该用户风格库 + 该用户规则，默认老席保持老席
        fg = FakeGen()
        with mock.patch("brain.experts.xiaowen.load_style", return_value="SENTINEL_STYLE") as ls:
            XiaowenExpert(generate=fg, style_user="张艺宝").handle("普娃预算不够")
        system = fg.calls[0][0]
        self.assertIn("SENTINEL_STYLE", system)
        self.assertIn("风格库：张艺宝", system)
        self.assertIn("张艺宝 风格偏好", system)
        ls.assert_called_once_with("张艺宝")
        fg2 = FakeGen()
        XiaowenExpert(generate=fg2).handle("普娃预算不够")
        self.assertIn("风格库：老席", fg2.calls[0][0])
        self.assertIn("无书面过渡词", system)

    def test_xiaone_rejects_redline_word_locally(self):
        bad = "老席保证录取，费用只要8万一年"
        verdict = XiaoneExpert(generate=FakeGen()).handle(bad)
        self.assertIn("红线", verdict)

    def test_xiaone_redline_in_context_also_rejected(self):
        # 问题2：脚本放 context（后台），红线扫描必须覆盖 context 而非只扫 task
        verdict = XiaoneExpert(generate=FakeGen()).handle("审核这条脚本", context="包录取，直接上国立")
        self.assertIn("红线", verdict)

    def test_xiaone_split_review(self):
        text = ("【审核结论】通过\n需要给您展示理由吗？\n\n===审核详情===\n"
                "费用数字与知识库一致，无红线。")
        public, details = split_review(text)
        self.assertIn("【审核结论】通过", public)
        self.assertIn("需要给您展示理由吗？", public)
        self.assertIn("费用数字与知识库一致", details)
        self.assertNotIn("===审核详情===", details)

    def test_xiaone_split_review_strips_json(self):
        text = ("【审核结论】通过\n需要给您展示理由吗？\n\n===审核详情===\n"
                "费用数字与知识库一致。\n{\"claims\":[{\"fact\":\"新加坡国立大学\"}]}")
        public, details = split_review(text)
        self.assertNotIn("claims", details)
        self.assertNotIn("{", details)

    def test_xiaone_fail_reply_splits_clean(self):
        # 本地红线拒稿输出也要双段：公开段只给结论+问理由，详情段存后台
        text = XiaoneExpert(generate=FakeGen()).handle("包录取")
        public, details = split_review(text)
        self.assertIn("【审核结论】不通过", public)
        self.assertIn("需要给您展示理由吗？", public)
        self.assertIn("合规红线", details)

    def test_xiaone_passes_clean_via_llm(self):
        fg = FakeGen()
        good = "新加坡留学，老席帮你算笔账"
        verdict = XiaoneExpert(generate=fg).handle(good)
        self.assertIn("测试", verdict)
        self.assertEqual(len(fg.calls), 1)

    def test_xiaowen_prompt_discourages_over_reasoning(self):
        fg = FakeGen()
        XiaowenExpert(generate=fg).handle("写条脚本，关于预算对比")
        self.assertIn("不要展开长篇推理过程", fg.calls[0][0])

    def test_xiaone_prompt_discourages_over_reasoning(self):
        fg = FakeGen()
        XiaoneExpert(generate=fg).handle("新加坡留学，老席帮你算笔账")
        self.assertIn("不要展示内心推理草稿", fg.calls[0][0])

    def test_xiaone_prompt_details_mandatory(self):
        # 审核详情段是必须交付的存档内容，不是可省的推理过程（否则老板要理由时无档可读）
        fg = FakeGen()
        XiaoneExpert(generate=fg).handle("新加坡留学，老席帮你算笔账")
        system = fg.calls[0][0]
        self.assertIn("必须交付", system)
        self.assertIn("必须写完", system)

    def test_xiaowen_prompt_has_confirmed_rules(self):
        fg = FakeGen()
        XiaowenExpert(generate=fg).handle("普娃预算不够")
        self.assertIn("已确认规则", fg.calls[0][0])

    def test_xiaowen_script_format_spec(self):
        fg = FakeGen()
        XiaowenExpert(generate=fg).handle("写条脚本，关于预算对比")
        user = fg.calls[0][1]
        self.assertIn("## 脚本 v1", user)
        self.assertNotIn("【席小文】## 脚本", user)
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
        self.assertIn("type", system)
        self.assertIn("表达偏好", system)
        self.assertIn("禁止事项", system)

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

    def test_xiaoti_prompt_includes_market_intel(self):
        with mock.patch("brain.experts.xiaoti.market_intel", return_value="【竞对】新东方前途文书模板化（测试）"):
            fg = FakeGen()
            XiaotiExpert(generate=fg).handle("帮我出3个选题")
        system = fg.calls[0][0]
        self.assertIn("竞对情报/市场热点", system)
        self.assertIn("新东方前途文书模板化", system)

    def test_xiaoti_quota_guides_balanced_sourcing(self):
        fg = FakeGen()
        XiaotiExpert(generate=fg).handle("帮我出10个选题")
        user = fg.calls[0][1]
        self.assertIn("取材配额", user)
        self.assertIn("尽量兼顾", user)
        self.assertIn("别清一色只用一个来源", user)
        self.assertIn("标注取材来源", user)

    def test_xiaoti_quota_no_count_still_instructs_split(self):
        fg = FakeGen()
        XiaotiExpert(generate=fg).handle("出几个选题")
        user = fg.calls[0][1]
        self.assertIn("取材配额", user)
        self.assertNotIn("本批约", user)

    def test_xiaoti_prompt_includes_used_topics(self):
        with mock.patch("brain.experts.xiaoti.load_used", return_value=["普娃逆袭", "费用对比"]):
            fg = FakeGen()
            XiaotiExpert(generate=fg).handle("帮我出3个选题")
        user = fg.calls[0][1]
        self.assertIn("已用选题", user)
        self.assertIn("- 普娃逆袭", user)
        self.assertIn("优先给新角度", user)

    def test_xiaoti_quota_adaptive_small_count(self):
        with mock.patch("brain.experts.xiaoti.load_used", return_value=[]):
            fg = FakeGen()
            XiaotiExpert(generate=fg).handle("出1个选题")
            small = fg.calls[0][1]
            XiaotiExpert(generate=fg).handle("帮我出5个选题")
            big = fg.calls[1][1]
        self.assertIn("随机选一类或两类", small)
        self.assertNotIn("尽量兼顾", small)
        self.assertIn("尽量兼顾", big)
        self.assertNotIn("随机选一类或两类", big)

    def test_xiaone_prompt_has_confirmed_rules(self):
        fg = FakeGen()
        XiaoneExpert(generate=fg).handle("新加坡留学，老席帮你算笔账")
        system = fg.calls[0][0]
        self.assertIn("已确认规则", system)

    def test_xiaoti_prompt_has_competition_positioning(self):
        fg = FakeGen()
        XiaotiExpert(generate=fg).handle("帮我出3个选题")
        system = fg.calls[0][0]
        self.assertIn("项目竞争口径", system)
        self.assertIn("不得互相比较优劣势", system)

    def test_xiaowen_prompt_has_competition_positioning(self):
        fg = FakeGen()
        XiaowenExpert(generate=fg).handle("普娃预算不够")
        system = fg.calls[0][0]
        self.assertIn("项目竞争口径", system)
        self.assertIn("不得互相比较优劣势", system)

    def test_xiaone_prompt_has_competition_positioning(self):
        fg = FakeGen()
        XiaoneExpert(generate=fg).handle("新加坡留学，老席帮你算笔账")
        system = fg.calls[0][0]
        self.assertIn("项目竞争口径", system)
        self.assertIn("不得互相比较优劣势", system)

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
