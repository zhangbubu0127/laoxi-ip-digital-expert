import unittest, tempfile, os
from brain import reflow

class TestReflow(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.reflow_tmp = os.path.join(self.dir.name, "数据回流.md")
        self.conclusion_tmp = os.path.join(self.dir.name, "验证结论.md")
        self._ro = reflow._REFLOW_PATH
        self._co = reflow._CONCLUSION_PATH
        reflow._REFLOW_PATH = self.reflow_tmp
        reflow._CONCLUSION_PATH = self.conclusion_tmp

    def tearDown(self):
        reflow._REFLOW_PATH = self._ro
        reflow._CONCLUSION_PATH = self._co
        self.dir.cleanup()

    def test_append_reflow(self):
        reflow.append_reflow("8/5", "普娃逆袭", "播放2.1w 留资23")
        with open(self.reflow_tmp, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("8/5", text)
        self.assertIn("普娃逆袭", text)
        self.assertIn("播放2.1w 留资23", text)

    def test_append_conclusion(self):
        reflow.append_conclusion("费用对比类爆了，多做")
        with open(self.conclusion_tmp, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("费用对比类爆了", text)

    def test_missing_append_creates_file(self):
        reflow.append_reflow("8/6", "x", "y")
        self.assertTrue(os.path.exists(self.reflow_tmp))

if __name__ == "__main__":
    unittest.main()
