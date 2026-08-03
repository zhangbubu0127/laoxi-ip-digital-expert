import unittest, tempfile, os
from brain import scheduler

class TestScheduler(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
        tmp.write("| 日期 | 发布时间 | 内容类型 | 选题 | 目标 | 状态 | 负责人 | 数据回流 |\n")
        tmp.write("|------|---------|---------|------|------|------|--------|---------|\n")
        tmp.write("| 8/3  | 12:00 | 曝光 | 普娃逆袭 | 拉新 | 待发 | 席小文 | — |\n")
        tmp.close()
        self.path = tmp.name
        scheduler._SCHEDULE_PATH = self.path
        self._sync_orig = scheduler._base_sync
        scheduler._base_sync = None

    def tearDown(self):
        os.unlink(self.path)
        scheduler._base_sync = self._sync_orig

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

    def test_record_data_after_publish(self):
        scheduler.mark_published("8/3", "普娃逆袭")
        ok = scheduler.record_data("8/3", "普娃逆袭", "播放2.1w 留资23")
        self.assertTrue(ok)
        row = scheduler.load_schedule()[0]
        self.assertEqual(row["status"], "回流完成")
        self.assertEqual(row["data"], "播放2.1w 留资23")

    def test_record_data_not_published(self):
        ok = scheduler.record_data("8/3", "普娃逆袭", "播放2.1w")
        self.assertFalse(ok)

    def test_add_entry_defaults_publish_time(self):
        scheduler.add_entry({"date": "8/5", "content_type": "曝光", "topic": "预算对比",
                             "goal": "拉新", "status": "待产", "owner": "席小文", "data": "—"})
        row = scheduler.load_schedule()[1]
        self.assertEqual(row["publish_time"], "12:00")

    def test_base_sync_called_on_writes(self):
        synced = []
        scheduler.set_base_sync(lambda rows: synced.append(list(rows)))
        scheduler.add_entry({"date": "8/5", "content_type": "曝光", "topic": "预算对比",
                             "goal": "拉新", "status": "待产", "owner": "席小文", "data": "—"})
        self.assertTrue(synced)
        self.assertEqual(synced[-1][1]["topic"], "预算对比")
        scheduler.mark_published("8/3", "普娃逆袭")
        self.assertEqual(synced[-1][0]["status"], "已发")

if __name__ == "__main__":
    unittest.main()
