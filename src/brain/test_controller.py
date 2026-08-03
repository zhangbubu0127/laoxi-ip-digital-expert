import unittest, tempfile, os
from unittest import mock
from pipe import InboundMessage
from brain.controller import handle_message, handle_bot_output
from brain.intent import IntentResult
from brain.session import store
from brain import scheduler, material, circuit, rules, reflow
import brain.controller as controller

class FakeExpert:
    def __init__(self, text):
        self.text = text
        self.last_context = None
        self.last_explain_context = None
        self.last_task = None

    def handle(self, task, context=""):
        self.last_context = context
        self.last_task = task
        return self.text

    def explain(self, question, context=""):
        self.last_explain_context = context
        return self.text

def msg(content, role="老板", mid="m1", mention_names=None):
    return InboundMessage(message_id=mid, chat_id="oc_t", content=content,
                          sender_open_id="ou_x", sender_role=role, mentions=[],
                          mention_names=mention_names or [])

def _fake_intent(content, history_text=""):
    if "已发布" in content:
        return IntentResult("确认已发布", {}, content)
    if "学一下" in content or "记住" in content or "学个规则" in content:
        return IntentResult("学规则", {}, content)
    if "确认" in content:
        return IntentResult("确认规则", {}, content)
    if "回流" in content or "复盘" in content or "播放" in content:
        return IntentResult("数据回流", {}, content)
    if "不行" in content or "改掉" in content:
        return IntentResult("反馈修改", {}, content)
    if "为什么" in content or "依据" in content or "哪来的" in content:
        return IntentResult("追问", {}, content)
    if "记素材" in content or "收下" in content:
        return IntentResult("记素材", {}, content)
    if "完整讨论" in content or "看讨论" in content:
        return IntentResult("看完整讨论", {}, content)
    if "圆桌" in content or "讨论下" in content or "大家" in content:
        return IntentResult("圆桌讨论", {}, content)
    if "选题" in content or "角度" in content:
        return IntentResult("出选题", {"count": "3"}, content)
    if "脚本" in content:
        return IntentResult("写脚本", {}, content)
    if "排期表" in content:
        return IntentResult("看排期表", {}, content)
    if "曝光" in content:
        return IntentResult("改排期", {"count": "3", "content_type": "曝光", "date": "下周"}, content)
    return IntentResult("其他", {}, content)

class TestController(unittest.TestCase):
    def setUp(self):
        store.clear()
        tmp = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
        tmp.write("| 日期 | 发布时间 | 内容类型 | 选题 | 目标 | 状态 | 负责人 | 数据回流 |\n")
        tmp.write("|------|---------|---------|------|------|------|--------|---------|\n")
        tmp.write("| 8/3  | 12:00 | 曝光 | 普娃逆袭 | 拉新 | 待发 | 席小文 | — |\n")
        tmp.close()
        self.path = tmp.name
        scheduler._SCHEDULE_PATH = self.path
        self.material_tmp = tempfile.TemporaryDirectory()
        material._MATERIAL_DIR = self.material_tmp.name
        self.ledger_tmp = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
        self.ledger_tmp.close()
        self._ledger_orig = circuit._LEDGER_PATH
        circuit._LEDGER_PATH = self.ledger_tmp.name
        controller._recognize = _fake_intent
        controller._pull_history = lambda chat_id, n: "【同事】群里讨论了预算对比方案"
        controller._chat_generate = lambda content, history_text="": "需要我做什么？出选题/写脚本/看排期/讨论？"
        controller._context_put = lambda chat_id, role, task, context: "testtoken"
        self.xiaoti = FakeExpert("【测试】3个选题")
        self.xiaowen = FakeExpert("【测试】脚本 v1")
        self.xiaone = FakeExpert("✅ 通过：无红线，可进入发布流程。")
        self.xiaoxi = FakeExpert('{"rules":[{"change":"老板把「保证」改成「基本都」","rule":"费用承诺用软性表达"}]}')
        controller.XiaotiExpert = lambda: self.xiaoti
        controller.XiaowenExpert = lambda: self.xiaowen
        controller.XiaoneExpert = lambda: self.xiaone
        controller.XiaoxiExpert = lambda: self.xiaoxi
        self.fupan = FakeExpert("【席小盘】费用对比类爆了，多做")
        controller.FupanExpert = lambda: self.fupan
        self.rules_tmp = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
        self.rules_tmp.close()
        self._rules_orig = rules._RULES_PATH
        rules._RULES_PATH = self.rules_tmp.name
        self.reflow_dir = tempfile.TemporaryDirectory()
        self._reflow_orig = reflow._REFLOW_PATH
        self._conclusion_orig = reflow._CONCLUSION_PATH
        reflow._REFLOW_PATH = os.path.join(self.reflow_dir.name, "数据回流.md")
        reflow._CONCLUSION_PATH = os.path.join(self.reflow_dir.name, "验证结论.md")

    def tearDown(self):
        os.unlink(self.path)
        self.material_tmp.cleanup()
        os.unlink(self.ledger_tmp.name)
        circuit._LEDGER_PATH = self._ledger_orig
        rules._RULES_PATH = self._rules_orig
        if os.path.exists(self.rules_tmp.name):
            os.unlink(self.rules_tmp.name)
        reflow._REFLOW_PATH = self._reflow_orig
        reflow._CONCLUSION_PATH = self._conclusion_orig
        self.reflow_dir.cleanup()
        controller._pull_history = None
        controller._emit = None

    def test_out_topic(self):
        out = handle_message(msg("帮我出3个选题"))
        self.assertTrue(any("席小题" in m.agent_tag for m in out))

    def test_write_script_chain(self):
        out = handle_message(msg("写条脚本，关于预算"))
        tags = [m.agent_tag for m in out]
        self.assertIn("席小文", tags)
        self.assertIn("席小核", tags)

    def test_view_schedule(self):
        out = handle_message(msg("看下排期表"))
        self.assertTrue(any("排期" in m.text for m in out))

    def test_publisher_can_confirm(self):
        out = handle_message(msg("8/3普娃逆袭已发布", role="发片同事"))
        self.assertTrue(any("已发" in m.text or "确认" in m.text for m in out))

    def test_publisher_cannot_override(self):
        out = handle_message(msg("下周三要3条曝光", role="发片同事"))
        self.assertTrue(any("无权限" in m.text for m in out))

    def test_boss_override_schedule(self):
        out = handle_message(msg("下周三要3条曝光"))
        self.assertTrue(any("3条" in m.text for m in out))

    def test_product_has_boss_permission(self):
        out = handle_message(msg("帮我出3个选题", role="产品"))
        self.assertTrue(any("席小题" in m.agent_tag for m in out))

    def test_fallback(self):
        out = handle_message(msg("今天天气不错"))
        self.assertTrue(any("需要我做什么" in m.text for m in out))

    def test_chat_uses_llm_for_other_intent(self):
        controller._chat_generate = lambda content, history_text="": "明白了，你是想聊预算对比这事吧？"
        out = handle_message(msg("随便聊聊"))
        self.assertTrue(any("明白了" in m.text for m in out))
        self.assertTrue(any(m.agent_tag == "小席" for m in out))

    def test_chat_falls_back_when_llm_fails(self):
        def boom(content, history_text=""):
            raise RuntimeError("llm down")
        controller._chat_generate = boom
        out = handle_message(msg("随便聊聊"))
        self.assertTrue(any("需要我做什么" in m.text for m in out))

    def test_synonym_routes_to_xiaoti(self):
        out = handle_message(msg("选几个没拍过的角度，反常识优先"))
        self.assertTrue(any("席小题" in m.agent_tag for m in out))

    def test_continuity_passes_context_to_xiaowen(self):
        handle_message(msg("帮我出3个选题"))
        self.xiaowen.last_context = None
        handle_message(msg("第1个写条脚本"))
        self.assertIsNotNone(self.xiaowen.last_context)
        self.assertIn("帮我出3个选题", self.xiaowen.last_context)

    def test_revise_routes_to_last_content_expert(self):
        handle_message(msg("写条脚本"))
        self.xiaowen.last_context = None
        out = handle_message(msg("这个脚本不行，把开头改掉"))
        self.assertTrue(any(m.agent_tag == "席小文" for m in out))
        self.assertIn("上一条产出", self.xiaowen.last_context)

    def test_revise_without_history(self):
        out = handle_message(msg("这个脚本不行"))
        self.assertTrue(any("没有找到可修改的上一条产出" in m.text for m in out))

    def test_ask_routes_to_last_expert(self):
        handle_message(msg("帮我出3个选题"))
        self.xiaoti.last_explain_context = None
        out = handle_message(msg("为什么选这个角度"))
        self.assertTrue(any(m.agent_tag == "席小题" for m in out))
        self.assertIn("你上次的产出", self.xiaoti.last_explain_context)

    def test_ask_without_history(self):
        out = handle_message(msg("为什么选这个角度"))
        self.assertTrue(any("没有找到可追问的上一条产出" in m.text for m in out))

    def test_record_material_saves(self):
        out = handle_message(msg("记素材：普娃预算对比，三年比国内省12万"))
        self.assertTrue(any("已存素材" in m.text for m in out))
        self.assertEqual(len(os.listdir(self.material_tmp.name)), 1)

    def test_publisher_cannot_record_material(self):
        out = handle_message(msg("记素材：预算对比", role="发片同事"))
        self.assertTrue(any("无权限" in m.text for m in out))

    def test_topic_asks_params_when_vague(self):
        controller._recognize = lambda content, history: IntentResult("出选题", {}, content)
        out = handle_message(msg("帮我出个选题"))
        self.assertTrue(any("出几个" in m.text for m in out))
        self.assertFalse(any(m.agent_tag == "席小题" for m in out))

    def test_topic_uses_normalized_task(self):
        controller._recognize = lambda content, history: IntentResult("出选题", {}, content, "出3个关于预算对比的选题，反常识角度")
        handle_message(msg("出3个选题，关于预算对比"))
        self.assertEqual(self.xiaoti.last_task, "出3个关于预算对比的选题，反常识角度")

    def test_write_script_uses_normalized_task(self):
        controller._recognize = lambda content, history: IntentResult("写脚本", {}, content, "写一条60秒脚本，主题：新加坡预算对比")
        handle_message(msg("写条脚本"))
        self.assertEqual(self.xiaowen.last_task, "写一条60秒脚本，主题：新加坡预算对比")

    def test_reflow_normalizes_wan_to_w(self):
        handle_message(msg("8/3普娃逆袭已发布"))
        out = handle_message(msg("8/3普娃逆袭 播放2.1万 留资23"))
        self.assertEqual(self.fupan.last_task, "日期 8/3 选题 普娃逆袭 数据：播放2.1w 留资23")
        row = scheduler.load_schedule()[0]
        self.assertEqual(row["data"], "播放2.1w 留资23")

    def test_topic_passes_current_content(self):
        self.xiaoti.last_context = None
        handle_message(msg("预算对比这篇文章说新加坡三年比国内省12万，基于这个出3个选题"))
        self.assertIsNotNone(self.xiaoti.last_context)
        self.assertIn("新加坡三年比国内省12万", self.xiaoti.last_context)

    def test_topic_includes_group_history(self):
        self.xiaoti.last_context = None
        handle_message(msg("帮我出3个选题"))
        self.assertIn("预算对比方案", self.xiaoti.last_context)

    def test_full_discussion_returns_history(self):
        out = handle_message(msg("看完整讨论"))
        self.assertTrue(any("预算对比方案" in m.text for m in out))

    def test_full_discussion_not_wired(self):
        controller._pull_history = None
        out = handle_message(msg("看完整讨论"))
        self.assertTrue(any("未接入" in m.text for m in out))

    def test_roundtable_dispatches_three_views(self):
        out = handle_message(msg("大家讨论下预算对比"))
        tags = [m.agent_tag for m in out]
        self.assertEqual(tags, ["小席", "席小题", "席小文", "席小核", "小席"])
        text = "".join(m.text for m in out)
        self.assertIn("圆桌纪要", text)
        self.assertIn("【测试】3个选题", text)
        self.assertIn("圆桌议题", self.xiaoti.last_task)
        self.assertIn("圆桌议题", self.xiaone.last_task)

    def test_roundtable_admin_only(self):
        out = handle_message(msg("开圆桌会议", role="发片同事"))
        self.assertTrue(any("无权限" in m.text for m in out))

    def test_roundtable_uses_last_expert_output_as_subject(self):
        handle_message(msg("帮我出3个选题"))
        out = handle_message(msg("开圆桌会议"))
        text = "".join(m.text for m in out)
        self.assertIn("议题：【测试】3个选题", text)

    def test_roundtable_requires_subject(self):
        controller._recognize = lambda content, history: IntentResult("圆桌讨论", {}, content)
        out = handle_message(msg("开圆桌会议"))
        self.assertTrue(any("需要一个议题" in m.text for m in out))

    def test_roundtable_streams_views_in_order(self):
        emitted_tags = []
        controller._emit = lambda chat_id, text, tag: emitted_tags.append(tag)
        with mock.patch.object(controller.bots, "by_role", side_effect=KeyError):
            out = handle_message(msg("大家讨论下预算对比"))
        self.assertEqual(emitted_tags, ["小席", "席小题", "席小文", "席小核", "小席"])
        self.assertTrue(all(m.emitted for m in out))
        self.assertEqual(len(out), 5)

    def test_write_script_streams_in_order(self):
        emitted_tags = []
        controller._emit = lambda chat_id, text, tag: emitted_tags.append(tag)
        with mock.patch.object(controller.bots, "by_role", side_effect=KeyError):
            out = handle_message(msg("写条脚本，关于预算"))
        self.assertEqual(emitted_tags, ["小席", "席小文", "席小核", "小席"])
        self.assertTrue(all(m.emitted for m in out))
        self.assertEqual(self.xiaone.last_task, "【测试】脚本 v1")

    def test_learn_rules_uses_last_output(self):
        handle_message(msg("帮我出3个选题"))
        out = handle_message(msg("记住这个改法，把开头改成先讲结果"))
        text = "".join(m.text for m in out)
        self.assertIn("待你确认", text)
        self.assertIn("软性表达", text)
        self.assertTrue(rules.has_pending())

    def test_confirm_rules_writes(self):
        handle_message(msg("帮我出3个选题"))
        handle_message(msg("记住这个改法"))
        out = handle_message(msg("确认"))
        self.assertTrue(any("已确认" in m.text for m in out))
        self.assertFalse(rules.has_pending())
        self.assertEqual(len(rules.confirmed_rules()), 1)

    def test_confirm_rules_no_pending(self):
        out = handle_message(msg("确认"))
        self.assertTrue(any("没有待确认的规则" in m.text for m in out))

    def test_learn_without_history(self):
        out = handle_message(msg("记住这个改法"))
        self.assertTrue(any("没有找到上一条产出" in m.text for m in out))

    def test_reflow_records_data(self):
        handle_message(msg("8/3普娃逆袭已发布"))
        out = handle_message(msg("8/3普娃逆袭 播放2.1w 留资23"))
        text = "".join(m.text for m in out)
        self.assertIn("数据已回流", text)
        self.assertIn("播放2.1w", text)
        self.assertEqual(self.fupan.last_task, "日期 8/3 选题 普娃逆袭 数据：播放2.1w 留资23")
        row = scheduler.load_schedule()[0]
        self.assertEqual(row["status"], "回流完成")
        self.assertEqual(row["data"], "播放2.1w 留资23")

    def test_reflow_lists_awaiting(self):
        handle_message(msg("8/3普娃逆袭已发布"))
        out = handle_message(msg("复盘一下"))
        self.assertTrue(any("待回流" in m.text for m in out))

    def test_reflow_no_published(self):
        out = handle_message(msg("复盘一下"))
        self.assertTrue(any("没有待回流数据" in m.text for m in out))

    def test_reflow_not_found(self):
        out = handle_message(msg("9/9不存在 播放2.1w"))
        self.assertTrue(any("没找到" in m.text for m in out))

    def test_roundtable_picks_specified_item(self):
        rounds = [[
            {"speaker": "老板", "text": "帮我出3个选题"},
            {"speaker": "席小题", "text": "1.【信任】野鸡大学回应\n2.【信任】19岁硕士毕业\n3.【曝光】初中毕业读本科"},
        ]]
        subj = controller._roundtable_subject("关于第二条选题，你开个圆桌会议", {}, rounds)
        self.assertIn("19岁硕士毕业", subj)
        self.assertNotIn("野鸡大学", subj)
        subj3 = controller._roundtable_subject("大家讨论下第3条", {}, rounds)
        self.assertIn("初中毕业读本科", subj3)

    def test_dispatch_topic_async_when_bots_configured(self):
        emitted = []
        stored = []
        controller._emit = lambda chat_id, text, tag: emitted.append((chat_id, text, tag))
        controller._context_put = lambda c, r, t, x: stored.append((c, r, x)) or "tok"
        def fake_by_role(role):
            return {"profile": role, "open_id": f"ou_{role}"}
        with mock.patch.object(controller.bots, "by_role", fake_by_role):
            out = handle_message(msg("帮我出3个选题"))
        self.assertEqual(len(out), 0)
        self.assertEqual(len(emitted), 1)
        _, text, tag = emitted[0]
        self.assertEqual(tag, "席小题")
        self.assertIn('<at user_id="ou_席小题"></at>', text)
        self.assertIn("出3个没拍过的选题", text)
        self.assertNotIn("【上下文】", text)
        self.assertNotIn("【最近群讨论", text)
        self.assertTrue(stored, "上下文应写入共享文件而非显示在对话框")
        self.assertIn("【最近群讨论", stored[0][2])

    def test_other_intent_direct_expert_chat_dispatches(self):
        emitted = []
        controller._emit = lambda chat_id, text, tag: emitted.append((chat_id, text, tag))
        def fake_by_role(role):
            return {"profile": role, "open_id": f"ou_{role}"}
        with mock.patch.object(controller.bots, "by_role", fake_by_role):
            out = handle_message(msg("席小题，这事你怎么看", role="老板", mention_names=["席小题"]))
        self.assertEqual(len(out), 0)
        self.assertEqual([t for _, _, t in emitted], ["席小题"])
        text = emitted[0][1]
        self.assertIn('<at user_id="ou_席小题"></at>', text)
        self.assertIn("共情", text)
        self.assertIn("绕回你的本职工作", text)
        self.assertNotIn("需要我做什么", text)

    def test_other_intent_no_mention_keeps_master_chat(self):
        controller._chat_generate = lambda content, history_text="": "自然接话"
        out = handle_message(msg("今天好累"))
        self.assertTrue(any("自然接话" in m.text for m in out))

    def test_write_pipeline_advances_via_bot_output(self):
        emitted = []
        controller._emit = lambda chat_id, text, tag: emitted.append((chat_id, text, tag))
        def fake_by_role(role):
            return {"profile": role, "open_id": f"ou_{role}"}
        with mock.patch.object(controller.bots, "by_role", fake_by_role):
            handle_message(msg("写条脚本，关于预算"))
            self.assertEqual([t for _, _, t in emitted], ["小席", "席小文"])
            wen = InboundMessage(message_id="w1", chat_id="oc_t", content="【测试】脚本 v1",
                                 sender_open_id="ou_席小文", sender_role="席小文", mentions=[])
            handle_bot_output(wen)
            self.assertIn("席小核", [t for _, _, t in emitted])
            he = InboundMessage(message_id="h1", chat_id="oc_t", content="✅ 通过",
                                sender_open_id="ou_席小核", sender_role="席小核", mentions=[])
            replies = handle_bot_output(he)
            self.assertTrue(any("写脚本流水线完成" in r.text for r in replies))
            self.assertEqual([t for _, _, t in emitted][-1], "小席")

    def test_write_pipeline_auto_fixes_on_reject(self):
        emitted = []
        controller._emit = lambda chat_id, text, tag: emitted.append((chat_id, text, tag))
        def fake_by_role(role):
            return {"profile": role, "open_id": f"ou_{role}"}
        with mock.patch.object(controller.bots, "by_role", fake_by_role):
            handle_message(msg("写条脚本，关于预算"))
            wen = InboundMessage(message_id="w1", chat_id="oc_t", content="【测试】脚本 v1",
                                 sender_open_id="ou_席小文", sender_role="席小文", mentions=[])
            handle_bot_output(wen)
            he_bad = InboundMessage(message_id="h1", chat_id="oc_t", content="❌ 红线：命中 ['包录取']，需改。",
                                    sender_open_id="ou_席小核", sender_role="席小核", mentions=[])
            handle_bot_output(he_bad)
            fix_tags = [t for _, _, t in emitted]
            self.assertIn("席小文", fix_tags[-2:])
            fix_text = [text for _, text, tag in emitted if tag == "席小文"][-1]
            self.assertIn("包录取", fix_text)
            self.assertIn("修改这条脚本", fix_text)
            wen_fix = InboundMessage(message_id="w2", chat_id="oc_t", content="【测试】脚本 v2（已去包录取）",
                                     sender_open_id="ou_席小文", sender_role="席小文", mentions=[])
            handle_bot_output(wen_fix)
            self.assertIn("席小核", [t for _, _, t in emitted][-2:])
            he_pass = InboundMessage(message_id="h2", chat_id="oc_t", content="✅ 通过",
                                     sender_open_id="ou_席小核", sender_role="席小核", mentions=[])
            replies = handle_bot_output(he_pass)
            self.assertTrue(any("写脚本流水线完成" in r.text for r in replies))

    def test_write_pipeline_stops_after_max_fix_rounds(self):
        emitted = []
        controller._emit = lambda chat_id, text, tag: emitted.append((chat_id, text, tag))
        def fake_by_role(role):
            return {"profile": role, "open_id": f"ou_{role}"}
        def bot_msg(mid, content, role):
            return InboundMessage(message_id=mid, chat_id="oc_t", content=content,
                                  sender_open_id=f"ou_{role}", sender_role=role, mentions=[])
        with mock.patch.object(controller.bots, "by_role", fake_by_role):
            handle_message(msg("写条脚本"))
            handle_bot_output(bot_msg("w1", "脚本A", "席小文"))
            handle_bot_output(bot_msg("h1", "❌ 黄线：需改", "席小核"))
            handle_bot_output(bot_msg("w2", "脚本B", "席小文"))
            handle_bot_output(bot_msg("h2", "❌ 红线：需改", "席小核"))
            handle_bot_output(bot_msg("w3", "脚本C", "席小文"))
            replies = handle_bot_output(bot_msg("h3", "❌ 红线：需改", "席小核"))
            # 已改 2 轮仍不过 → 不再 @席小文，改请老板定夺
            self.assertTrue(any("老板定夺" in r.text for r in replies))
            self.assertNotIn("席小文", [t for _, _, t in emitted][-3:])

    def test_reflow_flywheel_feeds_experts(self):
        handle_message(msg("8/3普娃逆袭已发布"))
        emitted = []
        controller._emit = lambda chat_id, text, tag: emitted.append((chat_id, text, tag))
        def fake_by_role(role):
            return {"profile": role, "open_id": f"ou_{role}"}
        with mock.patch.object(controller.bots, "by_role", fake_by_role):
            handle_message(msg("8/3普娃逆袭 播放2.1w 留资23"))
            tags = [t for _, _, t in emitted]
            self.assertEqual(tags[0], "席小盘")
            row = scheduler.load_schedule()[0]
            self.assertEqual(row["status"], "回流完成")
            self.assertEqual(row["data"], "播放2.1w 留资23")
            fupan = InboundMessage(message_id="f1", chat_id="oc_t", content="【席小盘】费用对比类爆了，多做",
                                   sender_open_id="ou_席小盘", sender_role="席小盘", mentions=[])
            handle_bot_output(fupan)
            final_tags = [t for _, _, t in emitted]
            self.assertIn("席小题", final_tags)
            self.assertIn("席小文", final_tags)
            ti = [text for _, text, tag in emitted if tag == "席小题"][0]
            self.assertIn("普娃逆袭", ti)
            self.assertIn("播放2.1w", ti)

    def test_revise_triggers_learning(self):
        handle_message(msg("写条脚本"))
        emitted = []
        controller._emit = lambda chat_id, text, tag: emitted.append((chat_id, text, tag))
        def fake_by_role(role):
            return {"profile": role, "open_id": f"ou_{role}"}
        with mock.patch.object(controller.bots, "by_role", fake_by_role):
            out = handle_message(msg("这个脚本不行，把开头改掉"))
        tags = [t for _, _, t in emitted]
        self.assertIn("席小文", tags)
        self.assertIn("席小习", tags)

    def test_roundtable_async_waits_for_three_views(self):
        emitted = []
        controller._emit = lambda chat_id, text, tag: emitted.append((chat_id, text, tag))
        def fake_by_role(role):
            return {"profile": role, "open_id": f"ou_{role}"}
        with mock.patch.object(controller.bots, "by_role", fake_by_role):
            handle_message(msg("大家讨论下预算对比"))
            self.assertEqual([t for _, _, t in emitted], ["小席", "席小题", "席小文", "席小核"])
            for role, text in [("席小题", "选题视角"), ("席小文", "内容视角"), ("席小核", "审核视角")]:
                m = InboundMessage(message_id=f"r-{role}", chat_id="oc_t", content=text,
                                   sender_open_id=f"ou_{role}", sender_role=role, mentions=[])
                handle_bot_output(m)
        summary = [text for _, text, tag in emitted if tag == "小席"][-1]
        self.assertIn("圆桌纪要", summary)
        self.assertIn("选题视角", summary)
        self.assertIn("审核视角", summary)

if __name__ == "__main__":
    unittest.main()
