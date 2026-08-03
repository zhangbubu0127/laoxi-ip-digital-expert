import unittest, tempfile, os, json
from skin.identity import resolve_role, _ROLES_PATH
import skin.identity as identity
import skin.bots as bots

class TestIdentity(unittest.TestCase):
    def test_boss(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"boss_open_ids": ["ou_boss"], "publisher_open_ids": ["ou_pub1"]}, tmp)
        tmp.close()
        old = _ROLES_PATH
        identity._ROLES_PATH = tmp.name
        self.assertEqual(resolve_role("ou_boss"), "老板")
        self.assertEqual(resolve_role("ou_pub1"), "发片同事")
        self.assertEqual(resolve_role("ou_stranger"), "未知")
        identity._ROLES_PATH = old
        os.unlink(tmp.name)

    def test_bot_role(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"boss_open_ids": ["ou_boss"]}, tmp)
        tmp.close()
        btmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"席小题": {"profile": "席小题", "open_id": "ou_ti"}}, btmp)
        btmp.close()
        old_roles, old_bots = _ROLES_PATH, bots._BOTS_PATH
        identity._ROLES_PATH = tmp.name
        bots._BOTS_PATH = btmp.name
        try:
            self.assertEqual(resolve_role("ou_ti"), "席小题")
            self.assertEqual(resolve_role("ou_boss"), "老板")
            self.assertEqual(resolve_role("ou_unknown"), "未知")
        finally:
            identity._ROLES_PATH = old_roles
            bots._BOTS_PATH = old_bots
            os.unlink(tmp.name)
            os.unlink(btmp.name)

if __name__ == "__main__":
    unittest.main()
