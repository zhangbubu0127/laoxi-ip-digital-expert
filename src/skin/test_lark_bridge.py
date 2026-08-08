import subprocess, time, unittest
from unittest import mock

from pipe import InboundMessage
from skin import lark_bridge
from skin import bots


def _msg(mentions=(), names=()):
    return InboundMessage(message_id="m1", chat_id="oc_t", content="hi",
                          sender_open_id="ou_h", sender_role="产品",
                          mentions=list(mentions), mention_names=list(names))


class TestMentionedXiaoxi(unittest.TestCase):
    def test_mentions_xiaoxi_open_id(self):
        with mock.patch.object(bots, "by_role", return_value={"profile": "小席", "open_id": "ou_xx"}):
            self.assertTrue(lark_bridge._mentioned_xiaoxi(_msg(mentions=["ou_xx"])))

    def test_mention_name_xiaoxi(self):
        with mock.patch.object(bots, "by_role", return_value={"profile": "小席", "open_id": "ou_xx"}):
            self.assertTrue(lark_bridge._mentioned_xiaoxi(_msg(names=["小席"])))

    def test_not_mentioned(self):
        with mock.patch.object(bots, "by_role", return_value={"profile": "小席", "open_id": "ou_xx"}):
            self.assertFalse(lark_bridge._mentioned_xiaoxi(_msg(mentions=["ou_other"], names=["张三"])))

    def test_by_role_missing_falls_open(self):
        # 拿不到小席 open_id（单bot兜底/配置缺失）→ 放行不拦截
        with mock.patch.object(bots, "by_role", side_effect=KeyError("小席")):
            self.assertTrue(lark_bridge._mentioned_xiaoxi(_msg()))


class TestPublishReminderMentionsOwner(unittest.TestCase):
    def test_reminder_ats_owner(self):
        rows = [{"date": "8/6", "publish_time": "12:05", "topic": "排行榜",
                 "owner": "张艺宝", "source_chat": "oc_x", "status": "待产", "mins_left": 30}]
        with mock.patch.object(lark_bridge._scheduler, "check_upcoming_publish", return_value=rows), \
             mock.patch.object(lark_bridge, "send_reply") as send, \
             mock.patch("skin.identity.resolve_owner_open_id", return_value="ou_zyb"):
            lark_bridge._maybe_send_publish_reminders(0.0, profile="小席")
        text = send.call_args[0][1]
        self.assertIn('<at user_id="ou_zyb"></at>', text)
        self.assertIn("排行榜", text)
        self.assertIn("30 分钟", text)

    def test_reminder_label_by_remaining(self):
        # 文案按真实剩余分钟数；无 mins_left（历史数据）回落「约 1 小时」
        rows = [{"date": "8/6", "publish_time": "12:05", "topic": "排行榜",
                 "owner": "张艺宝", "source_chat": "oc_x", "status": "待产", "mins_left": 3}]
        with mock.patch.object(lark_bridge._scheduler, "check_upcoming_publish", return_value=rows), \
             mock.patch.object(lark_bridge, "send_reply") as send, \
             mock.patch("skin.identity.resolve_owner_open_id", return_value="ou_zyb"):
            lark_bridge._maybe_send_publish_reminders(0.0, profile="小席")
        self.assertIn("3 分钟", send.call_args[0][1])
        rows = [{"date": "8/6", "publish_time": "12:05", "topic": "排行榜",
                 "owner": "张艺宝", "source_chat": "oc_x", "status": "待产"}]
        with mock.patch.object(lark_bridge._scheduler, "check_upcoming_publish", return_value=rows), \
             mock.patch.object(lark_bridge, "send_reply") as send, \
             mock.patch("skin.identity.resolve_owner_open_id", return_value="ou_zyb"):
            lark_bridge._maybe_send_publish_reminders(0.0, profile="小席")
        self.assertIn("约 1 小时", send.call_args[0][1])

    def test_reminder_no_owner_plain(self):
        rows = [{"date": "8/6", "publish_time": "12:05", "topic": "排行榜",
                 "owner": "陌生人", "source_chat": "oc_x", "status": "待产"}]
        with mock.patch.object(lark_bridge._scheduler, "check_upcoming_publish", return_value=rows), \
             mock.patch.object(lark_bridge, "send_reply") as send, \
             mock.patch("skin.identity.resolve_owner_open_id", return_value=""):
            lark_bridge._maybe_send_publish_reminders(0.0, profile="小席")
        text = send.call_args[0][1]
        self.assertNotIn("at user", text)


class TestPublishReminderThrottle(unittest.TestCase):
    def test_throttled_call_returns_false_and_skips_check(self):
        # 回归：run_loop 旧代码每轮无脑 last_remind=time.time()，节流永远命中、检查只跑启动那一次。
        # 契约：节流窗口内（距上次检查 <30s）不检查、返回 False，run_loop 据此不得重置计时。
        with mock.patch.object(lark_bridge._scheduler, "check_upcoming_publish") as check:
            result = lark_bridge._maybe_send_publish_reminders(time.time(), profile="小席")
        self.assertIs(result, False)
        check.assert_not_called()

    def test_check_after_throttle_returns_true_and_sends(self):
        # 距上次检查 ≥30s → 真检查、返回 True；进入窗口的排期会发提醒（不再只启动发一次）
        rows = [{"date": "8/7", "publish_time": "19:17", "topic": "私校收紧",
                 "owner": "张艺宝", "source_chat": "oc_x", "status": "待产", "mins_left": 30}]
        with mock.patch.object(lark_bridge._scheduler, "check_upcoming_publish", return_value=rows), \
             mock.patch.object(lark_bridge, "send_reply") as send, \
             mock.patch("skin.identity.resolve_owner_open_id", return_value="ou_zyb"):
            result = lark_bridge._maybe_send_publish_reminders(0.0, profile="小席")
        self.assertTrue(result)
        send.assert_called_once()
        self.assertIn('<at user_id="ou_zyb"></at>', send.call_args[0][1])


class TestAddReaction(unittest.TestCase):
    @mock.patch("skin.lark_bridge.subprocess.run")
    def test_builds_cmd(self, run):
        lark_bridge.add_reaction("oc_demo", "om_msg", profile="席小题")
        cmd = run.call_args[0][0]
        self.assertIn("im", cmd)
        self.assertIn("reactions", cmd)
        self.assertIn("--message-id", cmd)
        self.assertEqual(cmd[cmd.index("--message-id") + 1], "om_msg")
        data = cmd[cmd.index("--data") + 1]
        self.assertIn('"emoji_type": "STRIVE"', data)
        self.assertEqual(cmd[cmd.index("--profile") + 1], "席小题")

    @mock.patch("skin.lark_bridge.subprocess.run")
    def test_empty_message_id_noop(self, run):
        lark_bridge.add_reaction("oc_demo", "")
        run.assert_not_called()

    @mock.patch("skin.lark_bridge.subprocess.run")
    def test_failure_logs_not_raises(self, run):
        run.side_effect = subprocess.CalledProcessError(1, ["lark-cli"])
        lark_bridge.add_reaction("oc_demo", "om_msg")


if __name__ == "__main__":
    unittest.main()
