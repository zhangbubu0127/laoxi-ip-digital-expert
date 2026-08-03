import json, unittest
from unittest import mock
from skin.group_history import pull_group_history

def _payload(msgs):
    return json.dumps({"data": {"messages": msgs}}).encode("utf-8")

class TestGroupHistory(unittest.TestCase):
    def test_includes_user_and_app_messages(self):
        msgs = [
            {"msg_type": "text", "sender": {"sender_type": "user", "name": "张艺宝"}, "content": "帮我出3个选题"},
            {"msg_type": "text", "sender": {"sender_type": "app", "name": "席小题"}, "content": "1.【信任】野鸡大学回应"},
            {"msg_type": "text", "sender": {"sender_type": "app", "name": "小席"}, "content": "@席小题 出3个选题"},
        ]
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout=_payload(msgs))):
            out = pull_group_history("oc_demo")
        self.assertIn("【张艺宝】帮我出3个选题", out)
        self.assertIn("【席小题】1.【信任】野鸡大学回应", out)
        self.assertIn("【小席】@席小题 出3个选题", out)

    def test_skips_non_text_and_empty(self):
        msgs = [
            {"msg_type": "image", "sender": {"sender_type": "user", "name": "张艺宝"}, "content": "{}"},
            {"msg_type": "text", "sender": {"sender_type": "app", "name": "席小题"}, "content": "   "},
        ]
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout=_payload(msgs))):
            out = pull_group_history("oc_demo")
        self.assertEqual(out, "")

    def test_bad_payload_returns_empty(self):
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout=b"not json")):
            self.assertEqual(pull_group_history("oc_demo"), "")

if __name__ == "__main__":
    unittest.main()
