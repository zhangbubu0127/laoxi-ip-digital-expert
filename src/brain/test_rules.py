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

if __name__ == "__main__":
    unittest.main()
