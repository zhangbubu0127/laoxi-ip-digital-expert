import unittest, tempfile, os, datetime
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
        self._reminded_orig = scheduler._REMINDED_PATH
        rtmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        rtmp.close()
        scheduler._REMINDED_PATH = rtmp.name
        self.reminded_path = rtmp.name
        scheduler._REMINDED.clear()
        scheduler._LAST_REMINDED.clear()

    def tearDown(self):
        os.unlink(self.path)
        if os.path.exists(self.reminded_path):
            os.unlink(self.reminded_path)
        scheduler._REMINDED_PATH = self._reminded_orig
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

    def test_add_entry_with_script_writes_10_cells(self):
        scheduler.add_entry({"date": "8/5", "content_type": "曝光", "topic": "预算对比",
                             "goal": "拉新", "status": "待产", "owner": "发片同事", "data": "—",
                             "script": "【席小文】## 脚本 预算对比\n第一句是硬钩子",
                             "source_chat": "oc_x"})
        rows = scheduler.load_schedule()
        last = rows[-1]
        self.assertEqual(last["date"], "8/5")
        self.assertEqual(last["topic"], "预算对比")
        self.assertEqual(last["script"], "【席小文】## 脚本 预算对比 第一句是硬钩子")
        self.assertEqual(last["source_chat"], "oc_x")

    def test_old_8cell_row_still_loads(self):
        # setUp 写的是旧 8 列表头 + 8 格行 → load_schedule 补齐新列为空串
        rows = scheduler.load_schedule()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["script"], "")
        self.assertEqual(rows[0]["source_chat"], "")

    def test_row_with_hyphens_in_script_still_loads(self):
        # 脚本内容含「----」分隔线（结构拆解）时，整行不能误判成表头分隔线跳过
        scheduler.add_entry({"date": "8/6", "content_type": "信任", "topic": "排行榜",
                             "goal": "拉新", "status": "待产", "owner": "张艺宝", "data": "",
                             "script": "## 结构拆解\n--------\n0-3秒", "source_chat": "oc_y",
                             "publish_time": "12:05"})
        rows = scheduler.load_schedule()
        self.assertEqual(len(rows), 2)
        last = rows[-1]
        self.assertEqual(last["topic"], "排行榜")
        self.assertEqual(last["owner"], "张艺宝")
        self.assertEqual(last["script"], "## 结构拆解 -------- 0-3秒")

    def test_render_contains_script_and_source_columns(self):
        text = scheduler.render_schedule()
        self.assertIn("脚本内容", text)
        self.assertIn("来源群", text)

    def test_check_upcoming_publish_hits_within_hour(self):
        scheduler.add_entry({"date": "8/5", "content_type": "曝光", "topic": "预算对比",
                             "goal": "拉新", "status": "待产", "owner": "发片同事", "data": "—",
                             "script": "", "source_chat": "oc_x", "publish_time": "15:00"})
        now = datetime.datetime(2026, 8, 5, 14, 30)  # 发布前30分钟
        rows = scheduler.check_upcoming_publish(now)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["topic"], "预算对比")
        self.assertEqual(rows[0]["source_chat"], "oc_x")
        self.assertEqual(rows[0]["mins_left"], 30)

    def test_check_upcoming_publish_not_before_2h(self):
        scheduler.add_entry({"date": "8/5", "content_type": "曝光", "topic": "预算对比",
                             "goal": "拉新", "status": "待产", "owner": "发片同事", "data": "—",
                             "script": "", "source_chat": "oc_x", "publish_time": "15:00"})
        now = datetime.datetime(2026, 8, 5, 13, 0)  # 发布前2小时，不在1小时窗口内
        self.assertEqual(scheduler.check_upcoming_publish(now), [])

    def test_check_upcoming_publish_dedup(self):
        scheduler.add_entry({"date": "8/5", "content_type": "曝光", "topic": "预算对比",
                             "goal": "拉新", "status": "待产", "owner": "发片同事", "data": "—",
                             "script": "", "source_chat": "oc_x", "publish_time": "15:00"})
        now = datetime.datetime(2026, 8, 5, 14, 30)
        self.assertEqual(len(scheduler.check_upcoming_publish(now)), 1)
        # 同一条二次调用不再重复返回
        self.assertEqual(scheduler.check_upcoming_publish(now), [])

    def test_confirm_publish_stops_reminders(self):
        # 排期日期必须用真实今天：_load_reminded 只保留当天的 key（发布提醒本就只发当天的）
        today = datetime.date.today()
        today_str = f"{today.month}/{today.day}"
        scheduler.add_entry({"date": today_str, "content_type": "曝光", "topic": "预算对比",
                             "goal": "拉新", "status": "待产", "owner": "发片同事", "data": "—",
                             "script": "", "source_chat": "oc_x", "publish_time": "15:00"})
        now = datetime.datetime(today.year, today.month, today.day, 14, 30)
        self.assertEqual(len(scheduler.check_upcoming_publish(now)), 1)
        # 群内人工确认排期 → 该条不再提醒
        self.assertTrue(scheduler.confirm_publish("oc_x"))
        self.assertEqual(scheduler.check_upcoming_publish(now), [])
        # 确认状态落盘：清掉内存集合重新加载后，仍不重复提醒
        scheduler._REMINDED.clear()
        scheduler._REMINDED = scheduler._load_reminded()
        self.assertEqual(scheduler.check_upcoming_publish(now), [])

    def test_confirm_publish_unknown_chat(self):
        self.assertFalse(scheduler.confirm_publish("oc_none"))

if __name__ == "__main__":
    unittest.main()
