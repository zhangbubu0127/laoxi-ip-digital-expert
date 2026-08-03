import unittest
from brain.session import SessionStore, render_history

class TestSessionStore(unittest.TestCase):
    def setUp(self):
        self.s = SessionStore(max_rounds=7)

    def test_keeps_only_last_7_rounds(self):
        for i in range(10):
            self.s.add_round("c1", [{"speaker": "老板", "text": f"第{i}轮"}])
        rounds = self.s.history("c1")
        self.assertEqual(len(rounds), 7)
        self.assertEqual(rounds[0][0]["text"], "第3轮")
        self.assertEqual(rounds[-1][0]["text"], "第9轮")

    def test_chats_isolated(self):
        self.s.add_round("c1", [{"speaker": "老板", "text": "a"}])
        self.s.add_round("c2", [{"speaker": "老板", "text": "b"}])
        self.assertEqual(len(self.s.history("c1")), 1)
        self.assertEqual(len(self.s.history("c2")), 1)

    def test_clear(self):
        self.s.add_round("c1", [{"speaker": "老板", "text": "a"}])
        self.s.clear()
        self.assertEqual(self.s.history("c1"), [])

class TestRenderHistory(unittest.TestCase):
    def test_flattens_rounds_to_lines(self):
        rounds = [
            [{"speaker": "老板", "text": "帮我出3个选题"},
             {"speaker": "席小题", "text": "1.选题A 2.选题B"}],
            [{"speaker": "老板", "text": "第1个写条脚本"}],
        ]
        out = render_history(rounds)
        self.assertIn("【老板】帮我出3个选题", out)
        self.assertIn("【席小题】1.选题A 2.选题B", out)
        self.assertIn("【老板】第1个写条脚本", out)

    def test_truncates_long_lines(self):
        rounds = [[{"speaker": "老板", "text": "x" * 500}]]
        out = render_history(rounds, per_line=200)
        line = out.split("\n")[0]
        self.assertTrue(line.endswith("…"))
        self.assertLess(len(line), 210)

    def test_newlines_flattened(self):
        rounds = [[{"speaker": "席小文", "text": "line1\nline2"}]]
        self.assertNotIn("\nline2", render_history(rounds))

if __name__ == "__main__":
    unittest.main()
