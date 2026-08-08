import os, tempfile, unittest
from unittest import mock
from brain import knowledge

CATS = ["路径与升学", "学术与能力", "学历与认证", "费用与价值",
        "安全与监管", "学生管理与适应性", "就业与发展", "信任与案例"]

def _setup_tmp():
    tmp = tempfile.TemporaryDirectory()
    d = os.path.join(tmp.name, "家长常问与成单解答")
    os.makedirs(d)
    for i, c in enumerate(CATS):
        with open(os.path.join(d, f"{c}.md"), "w", encoding="utf-8") as f:
            f.write(f"# {c}\n唯一标识{i}")
    return tmp

class TestLoadQna(unittest.TestCase):
    def test_match_fee_category(self):
        tmp = _setup_tmp()
        try:
            with mock.patch.object(knowledge, "_KNOWLEDGE_DIR", tmp.name):
                out = knowledge.load_qna("关于预算对比")
            self.assertIn("# 费用与价值", out)
            self.assertNotIn("# 路径与升学", out)
        finally:
            tmp.cleanup()

    def test_match_academic_category(self):
        tmp = _setup_tmp()
        try:
            with mock.patch.object(knowledge, "_KNOWLEDGE_DIR", tmp.name):
                out = knowledge.load_qna("孩子英语不好听不懂课")
            self.assertIn("# 学术与能力", out)
        finally:
            tmp.cleanup()

    def test_empty_topic_falls_back_to_all(self):
        tmp = _setup_tmp()
        try:
            with mock.patch.object(knowledge, "_KNOWLEDGE_DIR", tmp.name):
                out = knowledge.load_qna("")
            for i, c in enumerate(CATS):
                self.assertIn(f"唯一标识{i}", out)
        finally:
            tmp.cleanup()

    def test_unknown_topic_falls_back_to_all(self):
        tmp = _setup_tmp()
        try:
            with mock.patch.object(knowledge, "_KNOWLEDGE_DIR", tmp.name):
                out = knowledge.load_qna("今天天气不错")
            for i, c in enumerate(CATS):
                self.assertIn(f"唯一标识{i}", out)
        finally:
            tmp.cleanup()

    def test_missing_files_does_not_crash(self):
        tmp = _setup_tmp()
        try:
            os.unlink(os.path.join(tmp.name, "家长常问与成单解答", "路径与升学.md"))
            with mock.patch.object(knowledge, "_KNOWLEDGE_DIR", tmp.name):
                out = knowledge.load_qna("")
            self.assertIn("唯一标识1", out)
        finally:
            tmp.cleanup()

class TestStyle(unittest.TestCase):
    def test_style_users_has_default(self):
        self.assertIn("老席", knowledge.style_users())

    def test_load_style_default_returns_content(self):
        text = knowledge.load_style("老席")
        self.assertTrue(len(text) > 100)

    def test_load_style_fallback_unknown_user(self):
        # 未知风格用户 → 回落老席，绝不空
        self.assertEqual(knowledge.load_style("不存在的用户"), knowledge.load_style("老席"))

    def test_load_style_empty_falls_back_default(self):
        self.assertEqual(knowledge.load_style(""), knowledge.load_style("老席"))

if __name__ == "__main__":
    unittest.main()
