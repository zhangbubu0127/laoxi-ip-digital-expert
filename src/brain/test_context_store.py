import tempfile, unittest, time
from brain import context_store


class TestContextStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        context_store._DIR = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_put_take_roundtrip(self):
        context_store.put("oc_demo", "席小题", "出3个选题", "【最近群讨论】\n...")
        got = context_store.take("oc_demo", "席小题")
        self.assertEqual(got, ("出3个选题", "【最近群讨论】\n..."))
        self.assertIsNone(context_store.take("oc_demo", "席小题"))

    def test_take_missing(self):
        self.assertIsNone(context_store.take("oc_demo", "席小文"))

    def test_roles_isolated(self):
        context_store.put("oc_demo", "席小题", "t1", "c1")
        self.assertIsNone(context_store.take("oc_demo", "席小文"))
        got = context_store.take("oc_demo", "席小题")
        self.assertEqual(got, ("t1", "c1"))

    def test_take_latest_when_multiple(self):
        context_store.put("oc_demo", "席小题", "old", "oldc")
        time.sleep(0.01)
        context_store.put("oc_demo", "席小题", "new", "newc")
        got = context_store.take("oc_demo", "席小题")
        self.assertEqual(got, ("new", "newc"))

    def test_sweep_removes_stale(self):
        context_store.put("oc_demo", "席小题", "t", "c")
        context_store.sweep(older_than=0)
        self.assertIsNone(context_store.take("oc_demo", "席小题"))

    def test_basis_save_load(self):
        context_store.save_basis("oc_demo", "席小题", "1.选题A\n2.选题B", "1.依据A\n2.依据B")
        self.assertEqual(context_store.load_basis("oc_demo", "席小题"),
                         ("1.选题A\n2.选题B", "1.依据A\n2.依据B"))
        self.assertEqual(context_store.load_basis("oc_demo", "席小文"), ("", ""))


if __name__ == "__main__":
    unittest.main()
