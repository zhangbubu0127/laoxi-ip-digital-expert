import tempfile, unittest
from brain import context_store
from skin.multi_main import (
    _split_topic_output, _basis_request, _split_task_context, _match_basis_question,
)


class TestMultiMain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        context_store._DIR = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_split_topic_output(self):
        out = _split_topic_output("1.【信任】野鸡大学回应\n2.【信任】19岁硕士毕业\n\n【依据】\n1. 家长原话五星\n2. 爆款规律")
        self.assertEqual(out[0], "1.【信任】野鸡大学回应\n2.【信任】19岁硕士毕业")
        self.assertEqual(out[1], "1. 家长原话五星\n2. 爆款规律")

    def test_split_topic_output_no_basis(self):
        self.assertEqual(_split_topic_output("闲聊而已"), ("闲聊而已", ""))

    def test_basis_request_only_when_explaining(self):
        self.assertTrue(_basis_request("席小题", "【追问】依据是什么"))
        self.assertTrue(_basis_request("席小题", "【追问】看看"))
        self.assertTrue(_basis_request("席小题", "老板在群里直接找你聊：看依据"))
        self.assertFalse(_basis_request("席小题", "出3个有依据的选题"))
        self.assertFalse(_basis_request("席小文", "【追问】依据是什么"))

    def test_split_task_context_reads_file(self):
        context_store.put("oc_demo", "席小题", "出3个选题", "【最近群讨论】\n...")
        task, ctx = _split_task_context("oc_demo", "席小题", "@席小题 出3个选题")
        self.assertEqual(task, "出3个选题")
        self.assertEqual(ctx, "【最近群讨论】\n...")

    def test_split_task_context_no_file(self):
        task, ctx = _split_task_context("oc_demo", "席小文", "@席小文 写条脚本")
        self.assertEqual(task, "写条脚本")
        self.assertEqual(ctx, "")

    def test_match_basis_specific_number(self):
        topics = "1.【信任】野鸡大学回应\n2.【信任】19岁硕士毕业"
        basis = "1. 家长原话五星\n2. 爆款规律跑过"
        out = _match_basis_question("第2个的依据", topics, basis)
        self.assertIn("选题2", out)
        self.assertIn("19岁硕士毕业", out)
        self.assertIn("爆款规律跑过", out)
        self.assertNotIn("野鸡大学", out)

    def test_match_basis_all(self):
        topics = "1.【信任】野鸡大学回应\n2.【信任】19岁硕士毕业"
        basis = "1. 家长原话五星\n2. 爆款规律跑过"
        out = _match_basis_question("看依据", topics, basis)
        self.assertIn("选题1", out)
        self.assertIn("选题2", out)

    def test_match_basis_layout_blank_line_between_blocks(self):
        topics = "1.【信任】野鸡大学回应\n2.【信任】19岁硕士毕业"
        basis = "1. 家长原话五星\n2. 爆款规律跑过"
        out = _match_basis_question("看依据", topics, basis)
        self.assertIn("\n\n", out)  # 块间空行分隔
        self.assertIn("\n依据：家长原话五星", out)  # 选题与依据分行
        self.assertNotIn("选题1「野鸡大学回应」依据", out)  # 不糊成一长句

    def test_match_basis_drift_pairs_by_number(self):
        topics = "1.选题甲\n2.选题乙"
        basis = "1. 依据乙\n2. 依据甲"
        out = _match_basis_question("第2个的依据", topics, basis)
        self.assertIn("选题乙", out)
        self.assertIn("依据甲", out)
        self.assertNotIn("选题甲", out)

    def test_match_basis_missing_number(self):
        out = _match_basis_question("第5个的依据", "1.甲\n2.乙", "1.依据甲\n2.依据乙")
        self.assertIn("没找到", out)

    def test_match_basis_new_block_format(self):
        # LLM 飘出的块状格式：每条依据以（选题N依据，来源：…）开头、正文成段，解析器需配对
        topics = "1.【信任】野鸡大学回应\n2.【曝光】19岁硕士毕业"
        basis = ("（选题1依据，来源：市场热点+知识库）\n家长原话里最高频的是安全焦虑，这条打信任。\n\n"
                 "（选题2依据，来源：市场热点+知识库）\n这条对准反内卷家长，反常识劝退有争议能拉曝光。")
        out = _match_basis_question("看依据", topics, basis)
        self.assertIn("选题1", out)
        self.assertIn("家长原话里最高频的是安全焦虑", out)
        self.assertIn("选题2", out)
        self.assertIn("反常识劝退有争议能拉曝光", out)
        self.assertNotIn("未给出依据", out)

    def test_match_basis_new_block_format_specific_number(self):
        topics = "1.【信任】野鸡大学回应\n2.【曝光】19岁硕士毕业"
        basis = ("（选题1依据，来源：市场热点+知识库）\n家长原话安全焦虑。\n\n"
                 "（选题2依据，来源：市场热点+知识库）\n反内卷家长，反常识劝退。")
        out = _match_basis_question("第2个的依据", topics, basis)
        self.assertIn("选题2", out)
        self.assertIn("19岁硕士毕业", out)
        self.assertIn("反内卷家长，反常识劝退", out)
        self.assertNotIn("野鸡大学", out)
        self.assertNotIn("未给出依据", out)


if __name__ == "__main__":
    unittest.main()
