import unittest, tempfile, os, json
from skin import bots


class TestBots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"席小题": {"profile": "席小题", "open_id": "ou_ti"}}, self.tmp)
        self.tmp.close()
        self._orig = bots._BOTS_PATH
        bots._BOTS_PATH = self.tmp.name

    def tearDown(self):
        bots._BOTS_PATH = self._orig
        os.unlink(self.tmp.name)

    def test_by_role_ok(self):
        entry = bots.by_role("席小题")
        self.assertEqual(entry["open_id"], "ou_ti")
        self.assertEqual(entry["profile"], "席小题")

    def test_by_role_missing_role(self):
        with self.assertRaises(KeyError):
            bots.by_role("不存在的角色")

    def test_by_role_empty_open_id_raises(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"席小文": {"profile": "席小文", "open_id": ""}}, f)
            path = f.name
        old = bots._BOTS_PATH
        bots._BOTS_PATH = path
        try:
            with self.assertRaises(KeyError):
                bots.by_role("席小文")
        finally:
            bots._BOTS_PATH = old
            os.unlink(path)

    def test_by_role_missing_file(self):
        old = bots._BOTS_PATH
        bots._BOTS_PATH = "/nonexistent/bots.json"
        try:
            with self.assertRaises(KeyError):
                bots.by_role("席小题")
        finally:
            bots._BOTS_PATH = old

    def test_by_open_id(self):
        self.assertEqual(bots.by_open_id("ou_ti"), "席小题")
        self.assertIsNone(bots.by_open_id("ou_unknown"))
        self.assertIsNone(bots.by_open_id(""))


if __name__ == "__main__":
    unittest.main()
