import unittest
from brain.intent import recognize, by_keyword, IntentResult, INTENTS

class FakeGen:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def __call__(self, system, user, **kw):
        self.calls.append((system, user, kw))
        return self.text

class TestRecognize(unittest.TestCase):
    def test_maps_intent_with_params(self):
        fg = FakeGen('{"intent":"出选题","params":{"count":"3","topic":"反常识"}}')
        r = recognize("帮我出3个选题，反常识角度", generate=fg)
        self.assertEqual(r.intent, "出选题")
        self.assertEqual(r.params, {"count": "3", "topic": "反常识"})

    def test_parses_normalized_task(self):
        fg = FakeGen('{"intent":"出选题","params":{"count":"3"},"task":"出3个关于预算对比的选题，反常识角度"}')
        r = recognize("出3个选题，关于预算对比，要反常识", generate=fg)
        self.assertEqual(r.intent, "出选题")
        self.assertEqual(r.task, "出3个关于预算对比的选题，反常识角度")

    def test_task_optional_defaults_empty(self):
        fg = FakeGen('{"intent":"写脚本"}')
        r = recognize("写条脚本", generate=fg)
        self.assertEqual(r.task, "")

    def test_prompt_asks_for_normalized_task(self):
        fg = FakeGen('{"intent":"出选题"}')
        recognize("帮我出个选题", generate=fg)
        system = fg.calls[0][0]
        self.assertIn("task", system)
        self.assertIn("扩写", system)

    def test_strips_json_fence(self):
        fg = FakeGen('```json\n{"intent":"写脚本"}\n```')
        r = recognize("写条台本", generate=fg)
        self.assertEqual(r.intent, "写脚本")

    def test_unknown_intent_falls_to_other(self):
        fg = FakeGen('{"intent":"订外卖"}')
        r = recognize("帮我订外卖", generate=fg)
        self.assertEqual(r.intent, "其他")

    def test_malformed_output_falls_to_other(self):
        fg = FakeGen("抱歉，我不太懂")
        r = recognize("你好", generate=fg)
        self.assertEqual(r.intent, "其他")

    def test_prompt_contains_full_taxonomy(self):
        fg = FakeGen('{"intent":"其他"}')
        recognize("hi", generate=fg)
        system = fg.calls[0][0]
        for i in INTENTS:
            self.assertIn(i, system)
        self.assertIn("不要思考过程", system)

    def test_intent_max_tokens(self):
        fg = FakeGen('{"intent":"其他"}')
        recognize("hi", generate=fg)
        self.assertEqual(fg.calls[0][2]["max_tokens"], 800)

    def test_history_injected_into_user_prompt(self):
        fg = FakeGen('{"intent":"写脚本"}')
        recognize("第1个写条脚本", history_text="【老板】帮我出3个选题\n【席小题】1.选题A 2.选题B", generate=fg)
        user = fg.calls[0][1]
        self.assertIn("历史对话：", user)
        self.assertIn("帮我出3个选题", user)
        self.assertIn("当前消息：第1个写条脚本", user)

    def test_revise_intent(self):
        fg = FakeGen('{"intent":"反馈修改"}')
        r = recognize("这个脚本不行，把开头改掉", generate=fg)
        self.assertEqual(r.intent, "反馈修改")

    def test_ask_intent(self):
        fg = FakeGen('{"intent":"追问"}')
        r = recognize("为什么选这个角度", generate=fg)
        self.assertEqual(r.intent, "追问")

    def test_record_material_intent(self):
        fg = FakeGen('{"intent":"记素材"}')
        r = recognize("记素材：这篇讲预算对比的文章", generate=fg)
        self.assertEqual(r.intent, "记素材")

    def test_full_discussion_intent(self):
        fg = FakeGen('{"intent":"看完整讨论"}')
        r = recognize("看完整讨论", generate=fg)
        self.assertEqual(r.intent, "看完整讨论")

    def test_lookup_record_intent(self):
        fg = FakeGen('{"intent":"查记录"}')
        r = recognize("刚才的选题你还能看见么", generate=fg)
        self.assertEqual(r.intent, "查记录")

    def test_roundtable_intent(self):
        fg = FakeGen('{"intent":"圆桌讨论"}')
        r = recognize("开圆桌会议", generate=fg)
        self.assertEqual(r.intent, "圆桌讨论")

    def test_review_script_intent(self):
        fg = FakeGen('{"intent":"审核脚本"}')
        r = recognize("审核一下", generate=fg)
        self.assertEqual(r.intent, "审核脚本")

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

    def test_chitchat(self):
        self.assertEqual(by_keyword("今天天气不错").intent, "其他")

if __name__ == "__main__":
    unittest.main()
