import unittest, tempfile, os
from pipe import InboundMessage
from brain.controller import handle_message
from brain import scheduler

def msg(content, role="老板", mid="m1"):
    return InboundMessage(message_id=mid, chat_id="oc_t", content=content,
                          sender_open_id="ou_x", sender_role=role, mentions=[])

class TestController(unittest.TestCase):
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

    def test_out_topic(self):
        out = handle_message(msg("帮我出3个选题"))
        self.assertTrue(any("席小题" in m.agent_tag for m in out))

    def test_write_script_chain(self):
        out = handle_message(msg("写条脚本，关于预算"))
        tags = [m.agent_tag for m in out]
        self.assertIn("席小文", tags)
        self.assertIn("席小核", tags)

    def test_view_schedule(self):
        out = handle_message(msg("看下排期表"))
        self.assertTrue(any("排期" in m.text for m in out))

    def test_publisher_can_confirm(self):
        out = handle_message(msg("8/3普娃逆袭已发布", role="发片同事"))
        self.assertTrue(any("已发" in m.text or "确认" in m.text for m in out))

    def test_publisher_cannot_override(self):
        out = handle_message(msg("下周三要3条曝光", role="发片同事"))
        self.assertTrue(any("无权限" in m.text for m in out))

    def test_boss_override_schedule(self):
        out = handle_message(msg("下周三要3条曝光"))
        self.assertTrue(any("3条" in m.text for m in out))

    def test_fallback(self):
        out = handle_message(msg("今天天气不错"))
        self.assertTrue(any("需要我做什么" in m.text for m in out))

if __name__ == "__main__":
    unittest.main()
