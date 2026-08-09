import unittest
from unittest import mock
from brain import research


class FakeGen:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def __call__(self, system, user, **kw):
        self.calls.append((system, user, kw))
        return self.text


def hit(title, url, snippet):
    return {"title": title, "url": url, "snippet": snippet}


class TestResearch(unittest.TestCase):
    def test_queries_strip_prefix(self):
        self.assertEqual(research._queries("调研一下中本贯通是什么"), ["中本贯通是什么"])

    def test_queries_split_on_conjunction(self):
        qs = research._queries("新加坡低龄留学和陪读政策")
        self.assertEqual(qs, ["新加坡低龄留学和陪读政策", "新加坡低龄留学 陪读政策"])

    def test_research_returns_generated_answer_with_sources(self):
        fg = FakeGen("中本贯通：初中毕业直通本科（来源）。")
        with mock.patch.object(research.market, "search_web", return_value=[
                hit("中本贯通政策解读", "http://a", "初中毕业读本科"),
                hit("中本贯通费用", "http://b", "一年20万")]):
            out = research.research("调研一下中本贯通是什么", generate=fg)
        self.assertEqual(out, "中本贯通：初中毕业直通本科（来源）。")
        user = fg.calls[0][1]
        self.assertIn("中本贯通", user)   # 搜索结果拼进 prompt
        self.assertIn("http://a", user)
        self.assertIn("http://b", user)

    def test_search_called_with_sg_filter_off(self):
        fg = FakeGen("结论")
        with mock.patch.object(research.market, "search_web", return_value=[hit("t", "u", "s")]) as sw:
            research.research("中本贯通是什么", generate=fg)
        sw.assert_called_once()
        self.assertFalse(sw.call_args.kwargs.get("sg_filter", True))

    def test_no_results_honest(self):
        with mock.patch.object(research.market, "search_web", return_value=[]):
            out = research.research("调研一个超冷门词", generate=FakeGen("x"))
        self.assertIn("没抓到", out)
        self.assertIn("超冷门词", out)

    def test_generate_failure_returns_raw_hits(self):
        def boom(*a, **k):
            raise ConnectionError("LLM down")
        with mock.patch.object(research.market, "search_web", return_value=[hit("标题1", "http://x", "摘要")]):
            out = research.research("调研X", generate=boom)
        self.assertIn("http://x", out)
        self.assertIn("生成没成", out)

    def test_empty_question(self):
        out = research.research("", generate=FakeGen("x"))
        self.assertIn("没听懂", out)

    def test_system_prompt_has_competition_positioning(self):
        fg = FakeGen("结论")
        with mock.patch.object(research.market, "search_web", return_value=[hit("t", "u", "s")]):
            research.research("调研一下中本贯通和国际高中", generate=fg)
        system = fg.calls[0][0]
        self.assertIn("各说各的", system)
        self.assertIn("不得比较谁好谁坏", system)

    def test_research_never_raises(self):
        def boom(*a, **k):
            raise RuntimeError("unexpected")
        with mock.patch.object(research.market, "search_web", side_effect=RuntimeError("search down")):
            out = research.research("调研X", generate=boom)
        self.assertIsInstance(out, str)
        self.assertIn("出错", out)


if __name__ == "__main__":
    unittest.main()
