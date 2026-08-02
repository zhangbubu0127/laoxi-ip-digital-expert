import unittest
from base import Expert
from xiaoti import XiaotiExpert
from xiaowen import XiaowenExpert
from xiaone import XiaoneExpert

class TestExperts(unittest.TestCase):
    def test_xiaoti_returns_typed_topics(self):
        out = XiaotiExpert().handle("信息差角度")
        self.assertIn("曝光", out)
        self.assertIn("留资", out)

    def test_xiaowen_produces_script(self):
        out = XiaowenExpert().handle("普娃预算不够")
        self.assertIn("脚本 v1", out)

    def test_xiaone_rejects_redline_word(self):
        bad = "老席保证录取，费用只要8万一年"
        verdict = XiaoneExpert().handle(bad)
        self.assertIn("红线", verdict)

    def test_xiaone_passes_clean(self):
        good = "新加坡留学，老席帮你算笔账"
        verdict = XiaoneExpert().handle(good)
        self.assertIn("通过", verdict)

if __name__ == "__main__":
    unittest.main()
