import unittest, tempfile, os
from brain import rules

class TestRules(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
        tmp.close()
        self.path = tmp.name
        self._orig = rules._RULES_PATH
        rules._RULES_PATH = self.path

    def tearDown(self):
        rules._RULES_PATH = self._orig
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_add_pending(self):
        rid = rules.add_rule("老板把X改成Y", "费用用软性表达")
        self.assertIn("rule_0001", rid)
        self.assertTrue(rules.has_pending())
        self.assertFalse(rules.confirmed_rules())
        self.assertIn("费用用软性表达", rules.render_rules(rules.load_rules()))

    def test_confirm_writes(self):
        rules.add_rule("A", "规则1")
        self.assertEqual(rules.confirm_pending(True), 1)
        self.assertFalse(rules.has_pending())
        self.assertEqual(len(rules.confirmed_rules()), 1)
        self.assertIn("规则1", rules.render_rules())

    def test_reject(self):
        rules.add_rule("A", "规则1")
        self.assertEqual(rules.confirm_pending(False), 1)
        self.assertFalse(rules.confirmed_rules())
        self.assertFalse(rules.has_pending())

    def test_missing_file_empty(self):
        os.unlink(self.path)
        self.assertEqual(rules.load_rules(), [])
        self.assertFalse(rules.has_pending())
        self.assertIn("暂无", rules.render_rules())

    def test_sanitize_pipe(self):
        rules.add_rule("X|Y", "规则|1")
        row = rules.load_rules()[0]
        self.assertNotIn("|", row["change"])
        self.assertNotIn("|", row["rule"])

    def test_add_confirmed(self):
        rid = rules.add_confirmed_rule("老板确认审核疑问", "费用按8万算没问题")
        self.assertIn("rule_0001", rid)
        self.assertFalse(rules.has_pending())
        self.assertEqual(len(rules.confirmed_rules()), 1)
        self.assertIn("费用按8万算没问题", rules.render_rules())

    def test_confirmed_rule_keeps_type(self):
        rules.add_confirmed_rule("老板确认审核疑问", "费用按8万算没问题", rtype="事实规则")
        row = rules.load_rules()[0]
        self.assertEqual(row["type"], "事实规则")

    def test_invalid_type_defaults_to_content_strategy(self):
        rules.add_confirmed_rule("A", "规则1", rtype="随便")
        self.assertEqual(rules.load_rules()[0]["type"], "内容策略")

    def test_render_rules_groups_by_type(self):
        rules.add_confirmed_rule("A", "别用绝对词", rtype="禁止事项")
        rules.add_confirmed_rule("B", "费用用软性表达", rtype="表达偏好")
        out = rules.render_rules()
        self.assertLess(out.index("【禁止事项】"), out.index("【表达偏好】"))
        self.assertIn("别用绝对词", out)
        self.assertIn("费用用软性表达", out)

    def test_legacy_six_column_file_backfills_type(self):
        # 旧 6 列（无 type）规则文件：load 时回填默认类型，不丢旧规则
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("| id | 来源改动 | 提炼规则 | level | 状态 | 确认时间 |\n"
                    "|------|---------|------|------|------|--------|\n"
                    "| rule_0001 | 老板改开头 | 开头反常识直接给结论 | L1 | 已确认 | 2026-08-03 |\n")
        rows = rules.load_rules()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["type"], "内容策略")
        self.assertIn("开头反常识直接给结论", rules.render_rules())

    def test_style_user_rules_isolated(self):
        # 不同风格用户的规则各落各的库，互不污染
        other = os.path.join(os.path.dirname(self.path), "学习规则-张艺宝.md")
        try:
            rules.add_rule("老板把A改成B", "老席规则")
            rules.add_rule("老板把C改成D", "张艺宝规则", style_user="张艺宝")
            self.assertEqual([r["rule"] for r in rules.load_rules()], ["老席规则"])
            self.assertEqual([r["rule"] for r in rules.load_rules("张艺宝")], ["张艺宝规则"])
            rules.confirm_pending(True, style_user="张艺宝")
            self.assertIn("张艺宝规则", rules.render_rules(style_user="张艺宝"))
            self.assertNotIn("张艺宝规则", rules.render_rules())
            self.assertTrue(rules.has_pending())  # 老席库待确认不受张艺宝确认影响
            self.assertTrue(os.path.exists(other))
        finally:
            if os.path.exists(other):
                os.unlink(other)

if __name__ == "__main__":
    unittest.main()
