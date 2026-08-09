import unittest
from brain.planner import parse_plan, plan, ACTIONS

class FakeGen:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def __call__(self, system, user, **kw):
        self.calls.append((system, user, kw))
        return self.text

class TestParsePlan(unittest.TestCase):
    def test_no_action_line_plain_chat(self):
        r = parse_plan("好啊，那就按这个方向来。")
        self.assertEqual(r.action, "")
        self.assertEqual(r.reply, "好啊，那就按这个方向来。")

    def test_action_line_extracted_and_stripped(self):
        r = parse_plan("懂了。\n【行动】{\"action\":\"出选题\",\"task\":\"出3个预算对比选题\"}")
        self.assertEqual(r.action, "出选题")
        self.assertEqual(r.task, "出3个预算对比选题")
        self.assertEqual(r.reply, "懂了。")

    def test_malformed_json_ignored(self):
        r = parse_plan("【行动】{不是json}")
        self.assertEqual(r.action, "")
        self.assertNotIn("【行动】", r.reply)

    def test_non_dict_json_ignored(self):
        r = parse_plan("【行动】[\"出选题\"]")
        self.assertEqual(r.action, "")

    def test_unknown_action_not_whitelisted(self):
        r = parse_plan("【行动】{\"action\":\"订外卖\",\"task\":\"帮我订\"}")
        self.assertEqual(r.action, "")

    def test_action_line_must_be_at_line_start(self):
        # 正文里提到「【行动】」但不是独立行首 → 不当动作解析，避免误触发
        r = parse_plan("回复格式是【行动】{...}，需要你注意。")
        self.assertEqual(r.action, "")

    def test_params_only_dict_kept(self):
        r = parse_plan("【行动】{\"action\":\"改排期\",\"params\":{\"count\":\"3\",\"date\":\"下周\"}}")
        self.assertEqual(r.params, {"count": "3", "date": "下周"})

    def test_params_non_dict_dropped(self):
        r = parse_plan("【行动】{\"action\":\"出选题\",\"params\":\"3个\"}")
        self.assertEqual(r.params, {})

class TestPlanCall(unittest.TestCase):
    def test_generate_invoked_with_system_and_user(self):
        fg = FakeGen("收到。")
        plan("出3个选题", generate=fg)
        system, user, kw = fg.calls[0]
        self.assertIn("小席", system)
        self.assertIn("老板说：出3个选题", user)
        self.assertEqual(kw["max_tokens"], 1200)
        self.assertEqual(kw["temperature"], 0.3)

    def test_no_context_parts_only_content(self):
        fg = FakeGen("收到。")
        plan("随便聊聊", generate=fg)
        user = fg.calls[0][1]
        self.assertNotIn("最近对话", user)
        self.assertNotIn("本条 @了", user)

    def test_full_context_parts(self):
        fg = FakeGen("收到。")
        plan("这句可以说", history_text="老板:测试", mention_names=["席小核"],
             step="wait_he", review_head="不通过：命中红线", generate=fg)
        user = fg.calls[0][1]
        self.assertIn("最近对话", user)
        self.assertIn("本条 @了：席小核", user)
        self.assertIn("当前流水线：wait_he", user)
        self.assertIn("最近一次席小核审核结论：不通过：命中红线", user)

    def test_garbage_output_parses_to_empty(self):
        fg = FakeGen("抱歉，我不太懂你在说什么。")
        r = plan("你好", generate=fg)
        self.assertEqual(r.action, "")
        self.assertEqual(r.reply, "抱歉，我不太懂你在说什么。")

    def test_planner_prompt_clarifies_confirm_action_semantics(self):
        # 回归：22 个动作只列名字不写语义，模型把「怎么没@我提醒」联想成「确认排期」误执行。
        # 锁：确认已发布/确认排期有明确语义，且「追问提醒」明确不是确认排期。
        fg = FakeGen("收到。")
        plan("随便聊聊", generate=fg)
        system = fg.calls[0][0]
        self.assertIn("确认已发布", system)
        self.assertIn("确认排期（停止该条发布提醒）", system)
        self.assertIn("已准备", system)
        self.assertIn("怎么没提醒", system)
        self.assertIn("不是确认排期", system)

    def test_whitelist_has_all_expected_actions(self):
        for a in ("出选题", "写脚本", "审核脚本", "看排期表", "改排期", "改表格结构",
                  "确认已发布", "确认排期", "反馈修改", "追问", "要审核理由", "记素材",
                  "看完整讨论", "查记录", "查已用选题", "圆桌讨论", "学规则", "确认规则",
                  "数据回流", "更新市场情报", "改审核看法", "专家闲聊", "调研"):
            self.assertIn(a, ACTIONS)

    def test_planner_prompt_clarifies_research_semantics(self):
        # 调研是白名单动作，提示词须给出触发语义，避免老板「帮我查查X」被当闲聊
        fg = FakeGen("收到。")
        plan("随便聊聊", generate=fg)
        system = fg.calls[0][0]
        self.assertIn("调研", system)
        self.assertIn("派席小题用实时搜索调研", system)

if __name__ == "__main__":
    unittest.main()
