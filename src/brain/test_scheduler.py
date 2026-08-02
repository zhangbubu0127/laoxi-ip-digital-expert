import unittest, tempfile, os
from brain import scheduler

class TestScheduler(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
        tmp.write("| 日期 | 内容类型 | 选题 | 目标 | 状态 | 负责人 | 数据回流 |\n")
        tmp.write("|------|---------|------|------|------|--------|---------|\n")
        tmp.write("| 8/3  | 曝光 | 普娃逆袭 | 拉新 | 待发 | 席小文 | — |\n")
        tmp.close()
        self.path = tmp.name
        scheduler._SCHEDULE_PATH = self.path

    def tearDown(self):
        os.unlink(self.path)

    def test_load(self):
        rows = scheduler.load_schedule()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "待发")

    def test_mark_published(self):
        ok = scheduler.mark_published("8/3", "普娃逆袭")
        self.assertTrue(ok)
        rows = scheduler.load_schedule()
        self.assertEqual(rows[0]["status"], "已发")

    def test_mark_published_not_found(self):
        ok = scheduler.mark_published("9/9", "不存在")
        self.assertFalse(ok)

    def test_render(self):
        text = scheduler.render_schedule()
        self.assertIn("| 8/3", text)

if __name__ == "__main__":
    unittest.main()
