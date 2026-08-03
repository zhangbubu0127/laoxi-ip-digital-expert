import unittest
from pipe import parse_inbound, format_outbound, InboundMessage, OutboundMessage

EVENT = {
    "id": "om_001",
    "chat_id": "oc_demo",
    "chat_type": "group",
    "content": "帮我出3个选题",
    "sender": {"id": "ou_boss", "user_type": "user"},
    "mentions": [],
    "create_time": "1722480000000",
}

class TestPipe(unittest.TestCase):
    def test_parse_inbound_fields(self):
        m = parse_inbound(EVENT)
        self.assertEqual(m.message_id, "om_001")
        self.assertEqual(m.chat_id, "oc_demo")
        self.assertEqual(m.content, "帮我出3个选题")
        self.assertEqual(m.sender_open_id, "ou_boss")
        self.assertEqual(m.sender_role, "未知")

    def test_parse_inbound_no_sender_safe(self):
        e = dict(EVENT); e.pop("sender")
        m = parse_inbound(e)
        self.assertEqual(m.sender_open_id, "")

    def test_format_outbound_no_tag(self):
        o = OutboundMessage(chat_id="oc_demo", text="推荐3个选题", agent_tag="席小题")
        self.assertEqual(format_outbound(o), "推荐3个选题")

    def test_format_outbound_boss_flag(self):
        o = OutboundMessage(chat_id="oc_demo", text="这个事实对吗", agent_tag="席小核", need_boss=True)
        self.assertIn("请老板确认", format_outbound(o))

    def test_parse_inbound_real_schema(self):
        real = {
            "id": "om_real_001",
            "chat_id": "oc_real",
            "content": "看下排期表",
            "sender_id": "ou_sender",
            "mentions": [{"id": "ou_mentioned", "key": "@_user_1", "name": "席小题"}],
        }
        m = parse_inbound(real)
        self.assertEqual(m.message_id, "om_real_001")
        self.assertEqual(m.sender_open_id, "ou_sender")
        self.assertEqual(m.mentions, ["ou_mentioned"])

if __name__ == "__main__":
    unittest.main()
