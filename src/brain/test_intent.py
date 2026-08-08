import unittest
from brain.intent import by_keyword
from brain.planner import parse_plan, plan, ACTIONS

class FakeGen:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def __call__(self, system, user, **kw):
        self.calls.append((system, user, kw))
        return self.text

class TestParsePlan(unittest.TestCase):
    def test_maps_action_with_params(self):
        r = parse_plan('【行动】{"action":"出选题","task":"按预算方向出3个","params":{"count":"3","topic":"预算对比"}}')
        self.assertEqual(r.action, "出选题")
        self.assertEqual(r.params, {"count": "3", "topic": "预算对比"})

    def test_parses_normalized_task(self):
        r = parse_plan('【行动】{"action":"出选题","task":"出3个关于预算对比的选题，反常识角度"}')
        self.assertEqual(r.action, "出选题")
        self.assertEqual(r.task, "出3个关于预算对比的选题，反常识角度")

    def test_task_optional_defaults_empty(self):
        r = parse_plan('【行动】{"action":"写脚本"}')
        self.assertEqual(r.task, "")

    def test_strips_action_line_from_reply(self):
        r = parse_plan('懂了，我按预算方向来。\n【行动】{"action":"出选题","task":"出3个"}')
        self.assertIn("懂了，我按预算方向来。", r.reply)
        self.assertNotIn("【行动】", r.reply)
        self.assertEqual(r.action, "出选题")

    def test_prompt_asks_for_normalized_task(self):
        fg = FakeGen('【行动】{"action":"出选题"}')
        plan("帮我出个选题", generate=fg)
        system = fg.calls[0][0]
        self.assertIn("task", system)
        self.assertIn("白名单", system)

    def test_unknown_action_falls_to_empty(self):
        r = parse_plan('【行动】{"action":"订外卖","task":"帮我订"}')
        self.assertEqual(r.action, "")
        self.assertEqual(r.task, "")

    def test_malformed_output_falls_to_empty(self):
        r = parse_plan("抱歉，我不太懂")
        self.assertEqual(r.action, "")
        self.assertEqual(r.reply, "抱歉，我不太懂")

    def test_prompt_contains_full_whitelist(self):
        fg = FakeGen('【行动】{"action":"专家闲聊"}')
        plan("hi", generate=fg)
        system = fg.calls[0][0]
        for a in ACTIONS:
            self.assertIn(a, system)
        self.assertIn("120字以内", system)

    def test_plan_max_tokens_and_temp(self):
        fg = FakeGen('【行动】{"action":"专家闲聊"}')
        plan("hi", generate=fg)
        kw = fg.calls[0][2]
        self.assertEqual(kw["max_tokens"], 1200)
        self.assertEqual(kw["temperature"], 0.3)

    def test_history_injected_into_user_prompt(self):
        fg = FakeGen('【行动】{"action":"写脚本","task":"按预算方向写一条"}')
        plan("第1个写条脚本", history_text="【老板】帮我出3个选题\n【席小题】1.选题A 2.选题B", generate=fg)
        user = fg.calls[0][1]
        self.assertIn("最近对话", user)
        self.assertIn("帮我出3个选题", user)
        self.assertIn("老板说：第1个写条脚本", user)

    def test_mention_names_injected(self):
        fg = FakeGen('【行动】{"action":"专家闲聊"}')
        plan("帮我看看", mention_names=["席小核"], generate=fg)
        user = fg.calls[0][1]
        self.assertIn("本条 @了：席小核", user)

    def test_review_head_injected(self):
        fg = FakeGen('【行动】{"action":"专家闲聊"}')
        plan("为什么不能这么说", review_head="不通过：命中红线", generate=fg)
        user = fg.calls[0][1]
        self.assertIn("最近一次席小核审核结论：不通过：命中红线", user)

    def test_step_suppression_hint(self):
        fg = FakeGen('【行动】{"action":"专家闲聊"}')
        plan("再来一条", step="wait_fix", generate=fg)
        user = fg.calls[0][1]
        self.assertIn("当前流水线：wait_fix", user)
        self.assertIn("不要派新活", user)

    def test_revise_action(self):
        r = parse_plan('【行动】{"action":"反馈修改","task":"把开头改掉"}')
        self.assertEqual(r.action, "反馈修改")

    def test_ask_action(self):
        r = parse_plan('【行动】{"action":"追问","task":"为什么选这个角度"}')
        self.assertEqual(r.action, "追问")

    def test_record_material_action(self):
        r = parse_plan('【行动】{"action":"记素材","task":"存这篇预算对比文章"}')
        self.assertEqual(r.action, "记素材")

    def test_full_discussion_action(self):
        r = parse_plan('【行动】{"action":"看完整讨论"}')
        self.assertEqual(r.action, "看完整讨论")

    def test_lookup_record_action(self):
        r = parse_plan('【行动】{"action":"查记录","task":"查刚才的选题还在不在"}')
        self.assertEqual(r.action, "查记录")

    def test_roundtable_action(self):
        r = parse_plan('【行动】{"action":"圆桌讨论"}')
        self.assertEqual(r.action, "圆桌讨论")

    def test_review_script_action(self):
        r = parse_plan('【行动】{"action":"审核脚本","task":"审刚刚生成的文案"}')
        self.assertEqual(r.action, "审核脚本")

    def test_table_column_action(self):
        r = parse_plan('【行动】{"action":"改表格结构","task":"给排期表加一列脚本内容"}')
        self.assertEqual(r.action, "改表格结构")

    def test_revise_review_rules_action(self):
        r = parse_plan('【行动】{"action":"改审核看法","task":"「两三万一个月工资」可以说"}')
        self.assertEqual(r.action, "改审核看法")

    def test_expert_chat_action(self):
        r = parse_plan('【行动】{"action":"专家闲聊","task":"和席小核聊两句"}')
        self.assertEqual(r.action, "专家闲聊")

    def test_confirm_schedule_action(self):
        r = parse_plan('【行动】{"action":"确认排期"}')
        self.assertEqual(r.action, "确认排期")

class TestByKeyword(unittest.TestCase):
    def test_topic_synonym(self):
        r = by_keyword("选几个没拍过的角度")
        self.assertEqual(r.intent, "出选题")

    def test_schedule_synonym(self):
        r = by_keyword("下周三要3条曝光")
        self.assertEqual(r.intent, "改排期")
        self.assertEqual(r.params["count"], "3")

    def test_confirm_passed_topic_synonym(self):
        # 老板确认刚定稿选题后要求上传/排期 → 改排期（不被「出选题」/「写脚本」吸走）
        self.assertEqual(by_keyword("这条选题通过了，把类型和选题都上传").intent, "改排期")
        self.assertEqual(by_keyword("那可以排期发布了").intent, "改排期")
        self.assertEqual(by_keyword("这条选题通过了，把类型上传").intent, "改排期")

    def test_revise_synonym(self):
        self.assertEqual(by_keyword("这个脚本不行，把开头改掉").intent, "反馈修改")
        self.assertEqual(by_keyword("再犀利一点").intent, "反馈修改")

    def test_ask_synonym(self):
        self.assertEqual(by_keyword("为什么选这个角度").intent, "追问")
        self.assertEqual(by_keyword("这个数据哪来的").intent, "追问")

    def test_record_material_synonym(self):
        self.assertEqual(by_keyword("记素材：预算对比文章").intent, "记素材")
        self.assertEqual(by_keyword("收下这段热点内容").intent, "记素材")

    def test_full_discussion_synonym(self):
        self.assertEqual(by_keyword("看完整讨论").intent, "看完整讨论")
        self.assertEqual(by_keyword("看看最近群里聊了啥").intent, "看完整讨论")

    def test_roundtable_synonym(self):
        self.assertEqual(by_keyword("开圆桌会议").intent, "圆桌讨论")
        self.assertEqual(by_keyword("大家讨论下第二条选题").intent, "圆桌讨论")
        self.assertEqual(by_keyword("关于预算对比开个圆桌").intent, "圆桌讨论")

    def test_write_script_synonym(self):
        self.assertEqual(by_keyword("根据这个出内容吧").intent, "写脚本")
        self.assertEqual(by_keyword("按这个出").intent, "写脚本")
        self.assertEqual(by_keyword("出个60秒文案").intent, "写脚本")
        self.assertEqual(by_keyword("写条脚本").intent, "写脚本")

    def test_published(self):
        self.assertEqual(by_keyword("8/3普娃逆袭已发布").intent, "确认已发布")

    def test_confirm_schedule(self):
        self.assertEqual(by_keyword("已确认排期").intent, "确认排期")
        self.assertEqual(by_keyword("确认排期").intent, "确认排期")
        self.assertEqual(by_keyword("8/6排行榜已确认排期").intent, "确认排期")
        self.assertEqual(by_keyword("排期确认了").intent, "确认排期")

    def test_confirm_schedule_not_shadow_schedule_edit(self):
        # 「已确认排期」不误伤成改排期/出选题/写脚本
        self.assertEqual(by_keyword("这条已确认排期").intent, "确认排期")
        self.assertEqual(by_keyword("确认排期").intent, "确认排期")

    def test_review_script_synonym(self):
        self.assertEqual(by_keyword("审核一下").intent, "审核脚本")
        self.assertEqual(by_keyword("刚刚生成的文案，审核一下").intent, "审核脚本")
        self.assertEqual(by_keyword("这条脚本审审").intent, "审核脚本")
        self.assertEqual(by_keyword("复审一下").intent, "审核脚本")

    def test_market_intel_synonym(self):
        self.assertEqual(by_keyword("更新情报").intent, "更新市场情报")
        self.assertEqual(by_keyword("刷新市场情报").intent, "更新市场情报")
        self.assertEqual(by_keyword("搜竞对").intent, "更新市场情报")
        self.assertEqual(by_keyword("现在什么话题热").intent, "更新市场情报")

    def test_used_topic_synonym(self):
        # 「查已用选题」优先于「出选题」：含「选题」也不被出选题误吞
        self.assertEqual(by_keyword("看已用选题").intent, "查已用选题")
        self.assertEqual(by_keyword("过往用过的选题有哪些").intent, "查已用选题")
        self.assertEqual(by_keyword("历史选题").intent, "查已用选题")
        self.assertEqual(by_keyword("用过哪些选题").intent, "查已用选题")

    def test_lookup_record_synonym(self):
        # 「查记录」兜底：老板问历史产出还在不在/在哪（含「能看见」口语变体）
        self.assertEqual(by_keyword("刚才的选题你还能看见么").intent, "查记录")
        self.assertEqual(by_keyword("刚才的选题还能看到么").intent, "查记录")
        self.assertEqual(by_keyword("上次出的选题在哪").intent, "查记录")
        self.assertEqual(by_keyword("刚才那个脚本还在吗").intent, "查记录")
        self.assertEqual(by_keyword("选题找不回来了").intent, "查记录")

    def test_lookup_record_not_shadow_other_intents(self):
        # 别误伤：带「选题」但不带回顾性动词的，仍是原意图
        self.assertNotEqual(by_keyword("把刚才那个选题写条脚本").intent, "查记录")
        self.assertEqual(by_keyword("把第一条选题排上").intent, "改排期")
        self.assertEqual(by_keyword("这条选题通过了，把类型和选题都上传").intent, "改排期")

    def test_table_column_intent(self):
        self.assertEqual(by_keyword("单开一列放脚本内容").intent, "改表格结构")
        self.assertEqual(by_keyword("把详细脚本内容也放到里面吧，单开一列").intent, "改表格结构")
        self.assertEqual(by_keyword("加个字段").intent, "改表格结构")
        self.assertEqual(by_keyword("再多加一列").intent, "改表格结构")

    def test_table_column_not_shadowed_by_write_script(self):
        # 含「脚本」但没加列词的仍是写脚本；带加列词的优先改表格结构（否则会误执行成写脚本）
        self.assertEqual(by_keyword("写条脚本").intent, "写脚本")
        self.assertEqual(by_keyword("单开一列放脚本内容").intent, "改表格结构")

    def test_chitchat(self):
        self.assertEqual(by_keyword("今天天气不错").intent, "其他")

if __name__ == "__main__":
    unittest.main()
