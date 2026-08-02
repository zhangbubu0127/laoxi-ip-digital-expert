import unittest, tempfile, os, json
from skin.identity import resolve_role, _ROLES_PATH
import skin.identity as identity

class TestIdentity(unittest.TestCase):
    def test_boss(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"boss_open_id": "ou_boss", "publisher_open_ids": ["ou_pub1"]}, tmp)
        tmp.close()
        old = _ROLES_PATH
        identity._ROLES_PATH = tmp.name
        self.assertEqual(resolve_role("ou_boss"), "老板")
        self.assertEqual(resolve_role("ou_pub1"), "发片同事")
        self.assertEqual(resolve_role("ou_stranger"), "未知")
        identity._ROLES_PATH = old
        os.unlink(tmp.name)

if __name__ == "__main__":
    unittest.main()
