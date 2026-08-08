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

    def test_resolve_owner_open_id(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"owner_ids": {"张艺宝": "ou_zyb"}, "publisher_open_ids": ["ou_pub1"]}, tmp)
        tmp.close()
        old = _ROLES_PATH
        identity._ROLES_PATH = tmp.name
        try:
            self.assertEqual(identity.resolve_owner_open_id("张艺宝"), "ou_zyb")
            self.assertEqual(identity.resolve_owner_open_id("发片同事"), "ou_pub1")
            self.assertEqual(identity.resolve_owner_open_id("陌生人"), "")
        finally:
            identity._ROLES_PATH = old
            os.unlink(tmp.name)

    def test_resolve_owner_open_id_publisher_empty(self):
        # 发片同事泛指但 publisher 未配 → 空串不 @
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"owner_ids": {}, "publisher_open_ids": []}, tmp)
        tmp.close()
        old = _ROLES_PATH
        identity._ROLES_PATH = tmp.name
        try:
            self.assertEqual(identity.resolve_owner_open_id("发片同事"), "")
        finally:
            identity._ROLES_PATH = old
            os.unlink(tmp.name)

    def test_trusted_group_role(self):
        # open_id 未命中已知角色，但 chat_id 在 trusted_groups → 按群配置角色（外部群等同内部群）
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"product_open_ids": ["ou_prod"], "trusted_groups": {"oc_ext": "产品"}}, tmp)
        tmp.close()
        old = _ROLES_PATH
        identity._ROLES_PATH = tmp.name
        try:
            self.assertEqual(resolve_role("ou_new", "oc_ext"), "产品")
            self.assertEqual(resolve_role("ou_new", "oc_other"), "未知")
            # 已知角色优先级高于群授权
            self.assertEqual(resolve_role("ou_prod", "oc_ext"), "产品")
        finally:
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
