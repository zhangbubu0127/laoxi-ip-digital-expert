import subprocess, unittest
from unittest import mock

from skin import lark_bridge


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
