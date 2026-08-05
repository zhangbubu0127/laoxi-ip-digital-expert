import unittest, tempfile, os, time
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
    if "更新情报" in content or "搜竞对" in content:
        return IntentResult("更新市场情报", {}, content)
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
    if "审核" in content:
        return IntentResult("审核脚本", {}, content)
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
        controller._last_passed.clear()
        controller._pending_action.clear()
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
        controller._pipelines.clear()

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

    def test_last_expert_output_skips_probe(self):
        store.clear()
        store.add_round("oc_t", [
            {"speaker": "老板", "text": "出3个选题"},
            {"speaker": "席小题", "text": "1.选题A\n2.选题B"},
            {"speaker": "席小题", "text": "需要我给你看选题的依据么？"},
        ])
        speaker, prev = controller._last_expert_output(store.history("oc_t"))
        self.assertEqual(speaker, "席小题")
        self.assertEqual(prev, "1.选题A\n2.选题B")

    def test_verdict_classify_states(self):
        # 问题4回归：绝不能让「通过：无红线」被判成需改
        self.assertEqual(controller._verdict_classify("✅ 通过：无红线，可进入发布流程。"), "pass")
        self.assertEqual(controller._verdict_classify("【审核结论】通过"), "pass")
        self.assertEqual(controller._verdict_classify("❌ 红线：命中['包录取']，需改。"), "fail")
        self.assertEqual(controller._verdict_classify("【审核结论】需老板确认：费用待核"), "doubt")

    def test_resolve_script_task_picks_nth_topic(self):
        # 问题1回归：「第2个写条脚本」必须解析成具体选题，而不是让写手猜
        store.add_round("oc_t", [
            {"speaker": "老板", "text": "帮我出3个选题"},
            {"speaker": "席小题", "text": "1.【信任】野鸡大学回应\n2.【信任】19岁硕士毕业\n3.【曝光】初中毕业读本科"},
        ])
        task = controller._resolve_script_task("第2个写条脚本", "第2个写条脚本", store.history("oc_t"))
        self.assertIn("19岁硕士毕业", task)
        self.assertNotIn("野鸡大学", task)

    def test_resolve_script_task_picks_nth_via_basis_archive(self):
        # 问题1延伸：「选题三」经席小题依据存档解析成具体选题，不让写手靠猜
        with mock.patch.object(controller.context_store, "load_basis",
                               return_value=("1.【信任】选题甲\n2.【信任】选题乙\n3.【曝光】选题丙", "1.依据甲\n2.依据乙\n3.依据丙")):
            task = controller._resolve_script_task("选题三给我出两条脚本看看", "选题三给我出两条脚本看看", [], "oc_t")
        self.assertIn("选题丙", task)
        self.assertNotIn("选题甲", task)

    def test_pick_passed_topic_uses_finalized(self):
        # 老板确认刚定稿选题（「这条选题通过了」）→ 用刚定稿的选题+类型，不生成占位
        controller._last_passed["oc_t"] = {"topic": "新加坡私立大学=野鸡大学？90%的家长都搞错了", "content_type": "信任"}
        picked = controller._try_pick_topic("这条选题通过了，把类型和选题都上传", {}, [], "oc_t")
        self.assertIsNotNone(picked)
        self.assertEqual(picked["topic"], "新加坡私立大学=野鸡大学？90%的家长都搞错了")
        self.assertEqual(picked["content_type"], "信任")
        self.assertNotIn("曝光选题", picked["topic"])

    def test_pick_nth_topic_parses_type(self):
        # 无刚定稿上下文时回落「排上第2个」→ 从选题清单行首解析类型（不再硬编码曝光）
        store.add_round("oc_t", [
            {"speaker": "老板", "text": "帮我出3个选题"},
            {"speaker": "席小题", "text": "1.【曝光】PSB直读本科\n2.【信任】19岁硕士毕业\n3.【曝光】初中毕业读本科"},
        ])
        picked = controller._try_pick_topic("排上第2个", {}, store.history("oc_t"), "oc_t")
        self.assertIsNotNone(picked)
        self.assertEqual(picked["topic"], "19岁硕士毕业")
        self.assertEqual(picked["content_type"], "信任")

    def test_pick_no_source_returns_none(self):
        # 无刚定稿上下文、也无序号 → 返回 None（不生成占位，由上层给老板明说失败）
        self.assertIsNone(controller._try_pick_topic("这条选题通过了，把类型和选题都上传", {}, [], "oc_t"))

    def test_lookup_topic_record_from_basis(self):
        # 老板问「刚才的选题还能看到么」→ 真读选题存档回执真实选题，不闲聊承诺
        replies = []
        with mock.patch.object(controller.context_store, "load_basis",
                               return_value=("1.【曝光】预算对比\n2.【信任】费用拆分", "依据")):
            out = controller._lookup_topic_record("oc_t", "刚才的选题你还能看到么", [], replies)
        self.assertIn("预算对比", out)
        self.assertIn("【曝光】", out)
        self.assertIn("选题存档", out)
        self.assertEqual(replies, [])

    def test_lookup_topic_record_from_memory(self):
        # 存档空了但会话记忆还有席小题产出 → 用记忆回执
        store.add_round("oc_t", [{"speaker": "席小题", "text": "1.【信任】普娃逆袭\n2.【留资】费用对比"}])
        replies = []
        with mock.patch.object(controller.context_store, "load_basis", return_value=("", "")):
            out = controller._lookup_topic_record("oc_t", "刚才的选题还在么", store.history("oc_t"), replies)
        self.assertIn("普娃逆袭", out)
        self.assertIn("会话记忆", out)
        self.assertEqual(replies, [])
        store.clear()

    def test_lookup_topic_record_none_honest(self):
        # 存档和记忆都没有 → 如实说找不到 + 引导给方向，不编造选题
        replies = []
        with mock.patch.object(controller.context_store, "load_basis", return_value=("", "")):
            out = controller._lookup_topic_record("oc_t", "选题找不回来了", [], replies)
        self.assertIn("没找到", out)
        self.assertIn("重新出选题", out)
        self.assertEqual(replies, [])

    def test_lookup_topic_record_dispatches_when_direction_recoverable(self):
        # 无存档但记忆里有席小文产出 → 真实派单席小题按方向重出一版（说到做到）
        store.add_round("oc_t", [{"speaker": "席小文", "text": "【席小文】## 脚本 v1：预算对比的坑"}])
        replies = []
        with mock.patch.object(controller.context_store, "load_basis", return_value=("", "")):
            out = controller._lookup_topic_record("oc_t", "刚才的选题找不回来了", store.history("oc_t"), replies)
        self.assertIn("席小题", out)
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].agent_tag, "席小题")
        store.clear()

    def test_handle_message_lookup_record_when_intent_other(self):
        # 意图归「其他」的查选题记录请求 → 真核对回执，不再闲聊承诺
        with mock.patch.object(controller.context_store, "load_basis",
                               return_value=("1.【曝光】预算对比\n2.【信任】费用拆分", "依据")):
            old = controller._recognize
            controller._recognize = lambda content, history_text="": IntentResult("其他", {}, content)
            try:
                replies = handle_message(msg("刚才的选题你还能看到么"))
            finally:
                controller._recognize = old
        texts = [r.text for r in replies]
        self.assertTrue(any("预算对比" in t for t in texts))
        self.assertFalse(any(t == "需要我做什么？出选题/写脚本/看排期/讨论？" for t in texts))

    def test_ask_for_xiaone_review_reasons(self):
        # 问题3：追问席小核审核理由 → 直发存档详情（中文白话），不重新生成
        store.add_round("oc_t", [
            {"speaker": "老板", "text": "写条脚本"},
            {"speaker": "席小文", "text": "脚本 v1"},
            {"speaker": "席小核", "text": "【审核结论】通过\n需要给您展示理由吗？"},
        ])
        with mock.patch.object(controller.context_store, "load_review",
                               return_value="费用数字与知识库一致，无红线。"):
            out = handle_message(msg("为什么"))
        self.assertTrue(any("费用数字与知识库一致" in m.text for m in out))
        self.assertTrue(any(m.agent_tag == "席小核" for m in out))

    def test_other_intent_yes_shows_review_reasons(self):
        # 问题3：席小核问「需要给您展示理由吗？」，老板回「要」→ 直发详情
        store.add_round("oc_t", [
            {"speaker": "老板", "text": "写条脚本"},
            {"speaker": "席小文", "text": "脚本 v1"},
            {"speaker": "席小核", "text": "【审核结论】不通过\n需要给您展示理由吗？"},
        ])
        with mock.patch.object(controller.context_store, "load_review",
                               return_value="命中红线「包录取」，承诺性说法不能发。"):
            out = handle_message(msg("要"))
        self.assertTrue(any("承诺性说法不能发" in m.text for m in out))
        self.assertTrue(any(m.agent_tag == "席小核" for m in out))

    def test_ask_for_xiaone_review_reasons_falls_back_to_regenerate(self):
        # 审核模型省略详情段（没存档）时，追问理由 → 席小核现生成简洁理由，别让老板吃闭门羹
        store.add_round("oc_t", [
            {"speaker": "老板", "text": "写条脚本"},
            {"speaker": "席小文", "text": "脚本 v1"},
            {"speaker": "席小核", "text": "【审核结论】通过\n需要给您展示理由吗？"},
        ])
        with mock.patch.object(controller.context_store, "load_review", return_value=""):
            out = handle_message(msg("为什么"))
        self.assertTrue(any("通过" in m.text for m in out))
        self.assertTrue(any(m.agent_tag == "席小核" for m in out))
        self.assertIsNotNone(self.xiaone.last_explain_context)

    def test_other_intent_yes_shows_review_reasons_fallback(self):
        # 「要」→ 其他意图 → 席小核刚审完且没存档 → 现生成理由，不死回「没存详情」
        store.add_round("oc_t", [
            {"speaker": "老板", "text": "写条脚本"},
            {"speaker": "席小文", "text": "脚本 v1"},
            {"speaker": "席小核", "text": "【审核结论】通过\n需要给您展示理由吗？"},
        ])
        with mock.patch.object(controller.context_store, "load_review", return_value=""):
            out = handle_message(msg("要"))
        self.assertTrue(any("通过" in m.text for m in out))
        self.assertTrue(any(m.agent_tag == "席小核" for m in out))
        self.assertIsNotNone(self.xiaone.last_explain_context)

    def test_ask_appends_basis_archive(self):
        with mock.patch.object(controller.context_store, "load_basis",
                               return_value=("1.选题A\n2.选题B", "1.依据A\n2.依据B")):
            handle_message(msg("帮我出3个选题"))
            self.xiaoti.last_explain_context = None
            handle_message(msg("为什么选这个角度"))
            self.assertIn("你上次的选题与依据", self.xiaoti.last_explain_context)
            self.assertIn("1.依据A", self.xiaoti.last_explain_context)

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
        self.assertEqual(self.xiaone.last_task, "审核这条脚本")
        self.assertIn("【测试】脚本 v1", self.xiaone.last_context)

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

    def test_market_refresh_posts_result(self):
        with mock.patch.object(controller.market, "refresh", return_value={"hot": 2, "leads": 1}):
            out = handle_message(msg("更新情报"))
        text = "".join(m.text for m in out)
        self.assertIn("市场情报已更新", text)
        self.assertIn("热点 2 条", text)
        self.assertIn("竞对线索 1 条", text)

    def test_market_refresh_failure_keeps_old(self):
        with mock.patch.object(controller.market, "refresh", return_value={"hot": 0, "leads": 0}):
            out = handle_message(msg("更新情报"))
        text = "".join(m.text for m in out)
        self.assertIn("保持上次数据", text)

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

    def test_other_intent_with_content_cmd_forces_script(self):
        handle_message(msg("帮我出3个选题"))
        self.xiaowen.last_task = None
        out = handle_message(msg("根据这个出内容吧"))
        self.assertTrue(any(m.agent_tag == "席小文" for m in out))
        self.assertIsNotNone(self.xiaowen.last_task)
        self.assertIn("写脚本", self.xiaowen.last_task)
        self.assertIn("【测试】3个选题", self.xiaowen.last_task)
        self.assertFalse(any("需要我做什么" in m.text for m in out))

    def test_content_cmd_without_prior_output_stays_chat(self):
        controller._chat_generate = lambda content, history_text="": "自然接话"
        out = handle_message(msg("根据这个出内容吧"))
        self.assertTrue(any("自然接话" in m.text for m in out))
        self.assertFalse(any(m.agent_tag == "席小文" for m in out))

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

    def test_late_wen_output_still_triggers_review_after_timeout(self):
        # 流水线被超时清理后，席小文迟到脚本仍要@席小核审核（问题：说了审核不@）
        emitted = []
        controller._emit = lambda chat_id, text, tag: emitted.append((chat_id, text, tag))
        def fake_by_role(role):
            return {"profile": role, "open_id": f"ou_{role}"}
        with mock.patch.object(controller.bots, "by_role", fake_by_role):
            handle_message(msg("写条脚本，关于预算"))
            for p in controller._pipelines.values():
                p["ts"] = 0
            controller.sweep_pipelines()
            wen = InboundMessage(message_id="w-late", chat_id="oc_t",
                                 content="【席小文】## 脚本 v1：迟到的稿子",
                                 sender_open_id="ou_席小文", sender_role="席小文", mentions=[])
            handle_bot_output(wen)
        self.assertIn("席小核", [t for _, _, t in emitted])
        self.assertEqual(controller._pipelines["oc_t"]["step"], "wait_he")

    def test_late_wen_output_reviewed_inline_when_no_bots(self):
        # 单bot兜底：超时后迟到脚本仍进程内走审核
        handle_message(msg("写条脚本，关于预算"))
        for p in controller._pipelines.values():
            p["ts"] = 0
        controller.sweep_pipelines()
        self.xiaone.last_task = None
        wen = InboundMessage(message_id="w-late2", chat_id="oc_t",
                             content="【席小文】## 脚本 v1：迟到的稿子",
                             sender_open_id="ou_席小文", sender_role="席小文", mentions=[])
        replies = handle_bot_output(wen)
        self.assertTrue(any(r.agent_tag == "席小核" for r in replies))
        self.assertEqual(self.xiaone.last_task, "审核这条脚本")

    def test_late_wen_non_script_output_not_reviewed(self):
        # 迟到的闲聊/追问回复不算脚本，不触发审核
        emitted = []
        controller._emit = lambda chat_id, text, tag: emitted.append((chat_id, text, tag))
        def fake_by_role(role):
            return {"profile": role, "open_id": f"ou_{role}"}
        with mock.patch.object(controller.bots, "by_role", fake_by_role):
            handle_message(msg("写条脚本，关于预算"))
            for p in controller._pipelines.values():
                p["ts"] = 0
            controller.sweep_pipelines()
            wen = InboundMessage(message_id="w-late3", chat_id="oc_t",
                                 content="这个问题得结合您家情况细聊",
                                 sender_open_id="ou_席小文", sender_role="席小文", mentions=[])
            handle_bot_output(wen)
        self.assertNotIn("席小核", [t for _, _, t in emitted])

    def test_review_script_dispatches_xiaone_for_last_script(self):
        # 老板「审核一下」→ 派席小核审会话里上一条席小文脚本，而不是只聊天
        store.add_round("oc_t", [
            {"speaker": "老板", "text": "写条脚本"},
            {"speaker": "席小文", "text": "【席小文】## 脚本 v1：预算对比"},
        ])
        self.xiaone.last_task = None
        out = handle_message(msg("审核一下"))
        self.assertTrue(any(m.agent_tag == "席小核" for m in out))
        self.assertEqual(self.xiaone.last_task, "审核这条脚本")
        self.assertIn("【席小文】## 脚本 v1：预算对比", self.xiaone.last_context)

    def test_review_script_without_script_clarifies(self):
        out = handle_message(msg("审核一下"))
        self.assertTrue(any("要审核哪条" in m.text for m in out))
        self.assertFalse(any(m.agent_tag == "席小核" for m in out))

    def test_review_script_dispatches_xiaone_via_at_in_multibot(self):
        emitted = []
        controller._emit = lambda chat_id, text, tag: emitted.append((chat_id, text, tag))
        def fake_by_role(role):
            return {"profile": role, "open_id": f"ou_{role}"}
        store.add_round("oc_t", [
            {"speaker": "老板", "text": "写条脚本"},
            {"speaker": "席小文", "text": "【席小文】## 脚本 v1：预算对比"},
        ])
        with mock.patch.object(controller.bots, "by_role", fake_by_role):
            out = handle_message(msg("审核一下"))
        self.assertEqual([t for _, _, t in emitted], ["席小核"])
        self.assertIn('<at user_id="ou_席小核"></at>', emitted[0][1])
        self.assertIn("审核这条脚本", emitted[0][1])
        self.assertEqual(len(out), 0)

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

    def test_doubt_pauses_and_mentions_boss(self):
        # 席小核给疑问点（需老板确认）→ 暂停流水线 wait_boss + @老板拍板，不再只弹一句就丢
        emitted = []
        controller._emit = lambda chat_id, text, tag: emitted.append((chat_id, text, tag))
        def fake_by_role(role):
            return {"profile": role, "open_id": f"ou_{role}"}
        with mock.patch.object(controller.bots, "by_role", fake_by_role), \
             mock.patch.object(controller.identity, "load_roles",
                               return_value={"boss_open_ids": [], "product_open_ids": ["ou_product"]}):
            handle_message(msg("写条脚本，关于预算"))
            wen = InboundMessage(message_id="w1", chat_id="oc_t", content="【测试】脚本 v1",
                                 sender_open_id="ou_席小文", sender_role="席小文", mentions=[])
            handle_bot_output(wen)
            he_doubt = InboundMessage(message_id="h1", chat_id="oc_t",
                                      content="【审核结论】需老板确认\n费用数字和知识库对不上，需要老板确认按哪个算。",
                                      sender_open_id="ou_席小核", sender_role="席小核", mentions=[])
            replies = handle_bot_output(he_doubt)
        self.assertEqual(controller._pipelines["oc_t"]["step"], "wait_boss")
        self.assertTrue(any('<at user_id="ou_product"></at>' in r.text for r in replies))

    def test_boss_answer_learns_and_modifies(self):
        # 老板答复疑问 → 落已确认规则（席小核/席小文都学）+ 席小文按答案修改 + wait_fix 复审
        controller._pipelines["oc_t"] = {"step": "wait_boss", "script": "【席小文】## 脚本 v1",
                                         "reviewer": "【审核结论】需老板确认\n费用数字待确认",
                                         "fix_rounds": 0, "ts": 0}
        emitted = []
        controller._emit = lambda chat_id, text, tag: emitted.append((chat_id, text, tag))
        def fake_by_role(role):
            return {"profile": role, "open_id": f"ou_{role}"}
        with mock.patch.object(controller.bots, "by_role", fake_by_role):
            out = handle_message(msg("费用按8万算没问题", role="老板"))
        self.assertTrue(any("收到老板答案" in r.text for r in out))
        self.assertEqual(controller._pipelines["oc_t"]["step"], "wait_fix")
        self.assertTrue(any(r["status"] == "已确认" and "费用按8万算没问题" in r["rule"]
                            for r in rules.load_rules()))
        wen_tags = [text for _, text, tag in emitted if tag == "席小文"]
        self.assertTrue(wen_tags)
        self.assertIn("费用按8万算没问题", wen_tags[-1])
        self.assertIn("修改这条脚本", wen_tags[-1])

    def test_boss_abort_cancels(self):
        controller._pipelines["oc_t"] = {"step": "wait_boss", "script": "脚本",
                                         "reviewer": "需老板确认", "fix_rounds": 0, "ts": 0}
        emitted = []
        controller._emit = lambda chat_id, text, tag: emitted.append((chat_id, text, tag))
        def fake_by_role(role):
            return {"profile": role, "open_id": f"ou_{role}"}
        with mock.patch.object(controller.bots, "by_role", fake_by_role):
            out = handle_message(msg("不用了", role="老板"))
        self.assertTrue(any("已取消" in r.text for r in out))
        self.assertNotIn("oc_t", controller._pipelines)
        self.assertNotIn("席小文", [t for _, _, t in emitted])

    def test_boss_pass_finalizes_without_rule(self):
        # 老板回「确定了可以下一步了，就这么样」＝放行：按当前脚本通过定稿，不落规则、不派席小文改（防死循环）
        controller._pipelines["oc_t"] = {"step": "wait_boss", "script": "【席小文】## 脚本 v1",
                                         "reviewer": "【审核结论】需老板确认\n费用数字待确认",
                                         "fix_rounds": 0, "ts": 0}
        emitted = []
        controller._emit = lambda chat_id, text, tag: emitted.append((chat_id, text, tag))
        def fake_by_role(role):
            return {"profile": role, "open_id": f"ou_{role}"}
        with mock.patch.object(controller.bots, "by_role", fake_by_role):
            out = handle_message(msg("确定了可以下一步了，就这么样", role="老板"))
        self.assertTrue(any("按通过定稿" in r.text for r in out))
        self.assertNotIn("oc_t", controller._pipelines)
        self.assertNotIn("席小文", [t for _, _, t in emitted])
        self.assertNotIn("确定了可以下一步了", [r["rule"] for r in rules.load_rules()])

    def test_boss_pass_short_phrases(self):
        # 放行词表命中：可以了/继续/就按这个来/没问题 都按通过定稿，不落规则
        for phrase in ("可以了", "继续", "就按这个来", "没问题"):
            controller._pipelines["oc_t"] = {"step": "wait_boss", "script": "脚本",
                                             "reviewer": "需老板确认", "fix_rounds": 0, "ts": 0}
            out = handle_message(msg(phrase, role="老板"))
            self.assertTrue(any("按通过定稿" in r.text for r in out), phrase)
            self.assertNotIn("oc_t", controller._pipelines, phrase)
            self.assertNotIn(phrase, [r["rule"] for r in rules.load_rules()], phrase)

    def test_boss_substantive_answer_still_learns(self):
        # 带具体信息的答案仍是实质答案：落规则 + 席小文改 + wait_fix（「没问题」出现也不误判为放行）
        controller._pipelines["oc_t"] = {"step": "wait_boss", "script": "【席小文】## 脚本 v1",
                                         "reviewer": "【审核结论】需老板确认\n费用数字待确认",
                                         "fix_rounds": 0, "ts": 0}
        emitted = []
        controller._emit = lambda chat_id, text, tag: emitted.append((chat_id, text, tag))
        def fake_by_role(role):
            return {"profile": role, "open_id": f"ou_{role}"}
        with mock.patch.object(controller.bots, "by_role", fake_by_role):
            out = handle_message(msg("费用按8万算没问题", role="老板"))
        self.assertTrue(any("收到老板答案" in r.text for r in out))
        self.assertEqual(controller._pipelines["oc_t"]["step"], "wait_fix")
        self.assertTrue(any("费用按8万算没问题" in r["rule"] for r in rules.load_rules()))

    def test_nonadmin_message_not_routed_while_wait_boss(self):
        controller._pipelines["oc_t"] = {"step": "wait_boss", "script": "脚本",
                                         "reviewer": "需老板确认", "fix_rounds": 0, "ts": 0}
        handle_message(msg("看下排期表", role="发片同事"))
        self.assertEqual(controller._pipelines["oc_t"]["step"], "wait_boss")

    def test_register_proposal_captures_pending(self):
        controller._register_proposal("oc_t", "要不要重新出一版？【提议】重新出选题：按预算方向出3个")
        pending = controller._pending_action.get("oc_t")
        self.assertIsNotNone(pending)
        self.assertEqual(pending["intent"], "出选题")
        self.assertEqual(pending["task"], "按预算方向出3个")

    def test_register_proposal_script_intent(self):
        controller._register_proposal("oc_t", "要不要写一条？【提议】写脚本：按预算方向写一条")
        self.assertEqual(controller._pending_action.get("oc_t", {}).get("intent"), "写脚本")

    def test_register_proposal_rejects_schedule(self):
        # 改排期需具体日期/选题，聊天提议锁定不了，不登记——防肯定回复后按占位生成垃圾（8/5 12:35 bug 同类）
        controller._register_proposal("oc_t", "要不要排上？【提议】改排期：把这条排到明天9点")
        self.assertIsNone(controller._pending_action.get("oc_t"))

    def test_chat_reply_registers_and_strips_marker(self):
        controller._chat_generate = lambda content, history_text="": "要不要按预算方向重新出一版？【提议】重新出选题：按预算方向出3个"
        replies = []
        controller._chat_reply(msg("随便聊聊"), "随便聊聊", [], replies)
        pending = controller._pending_action.get("oc_t")
        self.assertIsNotNone(pending)
        self.assertEqual(pending["intent"], "出选题")
        self.assertEqual(pending["task"], "按预算方向出3个")
        self.assertNotIn("【提议】", replies[0].text)
        self.assertIn("重新出一版", replies[0].text)

    def test_handle_message_affirmative_runs_pending_topic(self):
        controller._pending_action["oc_t"] = {"intent": "出选题", "params": {}, "task": "按预算方向出3个", "ts": time.time()}
        out = handle_message(msg("好"))
        self.assertTrue(any("席小题" in m.agent_tag for m in out))
        self.assertNotIn("oc_t", controller._pending_action)

    def test_handle_message_affirmative_runs_pending_script(self):
        controller._pending_action["oc_t"] = {"intent": "写脚本", "params": {}, "task": "按预算方向写一条", "ts": time.time()}
        out = handle_message(msg("可以"))
        tags = [m.agent_tag for m in out]
        self.assertIn("席小文", tags)
        self.assertIn("席小核", tags)
        self.assertNotIn("oc_t", controller._pending_action)

    def test_handle_message_affirmative_no_pending_safe(self):
        # 无待定动作时「好」不被劫持，走正常意图路径不崩溃
        out = handle_message(msg("好"))
        self.assertTrue(out)
        self.assertTrue(any(m.agent_tag == "小席" for m in out))

    def test_handle_message_affirmative_view_schedule(self):
        controller._pending_action["oc_t"] = {"intent": "看排期表", "params": {}, "task": "", "ts": time.time()}
        out = handle_message(msg("行"))
        self.assertIn("排期表", out[0].text)
        self.assertNotIn("oc_t", controller._pending_action)

    def test_register_action_pending(self):
        # 【行动】待确认 → 登记待定动作，老板点头后真执行
        controller._register_actions("oc_t", '要不要重出一版？\n【行动】{"action":"重新出选题","task":"按预算方向出3个","when":"待确认"}', [], [])
        pending = controller._pending_action.get("oc_t")
        self.assertIsNotNone(pending)
        self.assertEqual(pending["intent"], "出选题")
        self.assertEqual(pending["task"], "按预算方向出3个")

    def test_register_action_pending_default_task(self):
        controller._register_actions("oc_t", '【行动】{"action":"出选题","task":"","when":"待确认"}', [], [])
        self.assertEqual(controller._pending_action.get("oc_t", {}).get("task"), "重新出一版选题")

    def test_register_action_immediate_lookup(self):
        # 【行动】核对选题记录（立即）→ 真读存档补回执，不空头承诺
        replies = []
        with mock.patch.object(controller.context_store, "load_basis",
                               return_value=("1.【曝光】预算对比\n2.【信任】费用拆分", "依据")):
            controller._register_actions("oc_t", '【行动】{"action":"核对选题记录","task":"","when":"立即"}', [], replies)
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0].agent_tag, "小席")
        self.assertIn("预算对比", replies[0].text)
        self.assertIn("选题存档", replies[0].text)

    def test_register_action_immediate_schedule(self):
        replies = []
        controller._register_actions("oc_t", '【行动】{"action":"看排期表","task":"","when":"立即"}', [], replies)
        self.assertEqual(len(replies), 1)
        self.assertIn("排期表", replies[0].text)

    def test_chat_reply_registers_action_and_strips(self):
        # 小席聊天回复带【行动】行：登记待定动作，显示时剥掉契约行
        controller._chat_generate = lambda content, history_text="": "懂了，要不要重新出一版？\n【行动】{\"action\":\"重新出选题\",\"task\":\"按预算方向出3个\",\"when\":\"待确认\"}"
        replies = []
        controller._chat_reply(msg("随便聊聊"), "随便聊聊", [], replies)
        self.assertEqual(controller._pending_action.get("oc_t", {}).get("intent"), "出选题")
        self.assertNotIn("【行动】", replies[0].text)
        self.assertIn("重新出一版", replies[0].text)

    def test_register_expert_proposal(self):
        # 专家回复带【动作名】建议下一步 → 登记待确认动作，task 取标记后的内容
        controller._register_expert_proposal("oc_t", "要不要我【重新出选题】按预算对比方向出3个？")
        pending = controller._pending_action.get("oc_t")
        self.assertEqual(pending["intent"], "出选题")
        self.assertEqual(pending["task"], "按预算对比方向出3个")

    def test_register_expert_proposal_write_script(self):
        controller._register_expert_proposal("oc_t", "要不要我【写脚本】把这个角度落成一条60秒？")
        self.assertEqual(controller._pending_action.get("oc_t", {}).get("intent"), "写脚本")

    def test_lookup_topic_record_force(self):
        # force=True 跳过关键词对：意图已判定查记录 / 小席立即动作时直接用
        replies = []
        with mock.patch.object(controller.context_store, "load_basis",
                               return_value=("1.【曝光】预算对比", "依据")):
            out = controller._lookup_topic_record("oc_t", "随便一句话", [], replies, force=True)
        self.assertIn("预算对比", out)

    def test_lookup_topic_record_broadened_pairs(self):
        # 「能看见」口语变体现在能命中关键词对，不再塌进闲聊
        replies = []
        with mock.patch.object(controller.context_store, "load_basis",
                               return_value=("1.【曝光】预算对比", "依据")):
            out = controller._lookup_topic_record("oc_t", "刚才的选题你还能看见么", [], replies)
        self.assertIn("预算对比", out)

    def test_handle_message_lookup_record_intent(self):
        # 意图「查记录」→ 真核对回执，不再是空口承诺
        with mock.patch.object(controller.context_store, "load_basis",
                               return_value=("1.【曝光】预算对比\n2.【信任】费用拆分", "依据")):
            controller._recognize = lambda content, history_text="": IntentResult("查记录", {}, content)
            out = handle_message(msg("刚才的选题你还能看见么"))
        self.assertTrue(any("预算对比" in m.text for m in out))
        self.assertTrue(any(m.agent_tag == "小席" for m in out))

if __name__ == "__main__":
    unittest.main()
