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

    def test_sweep_preserves_basis_and_review(self):
        context_store.put("oc_demo", "席小题", "t", "c")
        context_store.save_basis("oc_demo", "席小题", "1.选题甲\n2.选题乙", "1.依据甲\n2.依据乙")
        context_store.save_review("oc_demo", "席小核", "费用数字无红线。")
        context_store.sweep(older_than=0)
        self.assertEqual(context_store.load_basis("oc_demo", "席小题"),
                         ("1.选题甲\n2.选题乙", "1.依据甲\n2.依据乙"))
        self.assertEqual(context_store.load_review("oc_demo", "席小核"), "费用数字无红线。")
        self.assertIsNone(context_store.take("oc_demo", "席小题"))

    def test_take_ignores_basis_file(self):
        context_store.put("oc_demo", "席小题", "t", "c")
        time.sleep(0.01)
        context_store.save_basis("oc_demo", "席小题", "1.选题甲\n2.选题乙", "1.依据甲\n2.依据乙")
        got = context_store.take("oc_demo", "席小题")
        self.assertEqual(got, ("t", "c"))
        self.assertEqual(context_store.load_basis("oc_demo", "席小题"),
                         ("1.选题甲\n2.选题乙", "1.依据甲\n2.依据乙"))

    def test_basis_save_load(self):
        context_store.save_basis("oc_demo", "席小题", "1.选题A\n2.选题B", "1.依据A\n2.依据B")
        self.assertEqual(context_store.load_basis("oc_demo", "席小题"),
                         ("1.选题A\n2.选题B", "1.依据A\n2.依据B"))
        self.assertEqual(context_store.load_basis("oc_demo", "席小文"), ("", ""))

    def test_review_save_load(self):
        context_store.save_review("oc_demo", "席小核", "费用数字与知识库一致，无红线。")
        self.assertEqual(context_store.load_review("oc_demo", "席小核"),
                         "费用数字与知识库一致，无红线。")
        self.assertEqual(context_store.load_review("oc_demo", "席小文"), "")

    def test_take_ignores_review_file(self):
        context_store.put("oc_demo", "席小核", "审核这条脚本", "【脚本原文】\n...")
        time.sleep(0.01)
        context_store.save_review("oc_demo", "席小核", "无红线。")
        got = context_store.take("oc_demo", "席小核")
        self.assertEqual(got, ("审核这条脚本", "【脚本原文】\n..."))
        self.assertEqual(context_store.load_review("oc_demo", "席小核"), "无红线。")


if __name__ == "__main__":
    unittest.main()
