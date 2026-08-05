import os
import shutil
import tempfile
import unittest
from unittest import mock

from brain import market

_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>必应</title>
<item><title>新加坡留学 费用 2026</title><link>http://example.com/1</link><description>普通家庭新加坡留学一年费用</description><pubDate>Mon, 03 Aug 2026 20:09:00 GMT</pubDate></item>
<item><title>Windows 11 update</title><link>http://example.com/2</link><description>Microsoft support</description></item>
</channel></rss>"""


class FakeResp:
    def __init__(self, text=_RSS, status=200):
        self.text = text
        self.status_code = status


class TestMarket(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = market._INTEL_DIR
        market._INTEL_DIR = self._tmp

    def tearDown(self):
        market._INTEL_DIR = self._orig
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write(self, key, content, mtime=None):
        path = os.path.join(self._tmp, market._FILES[key])
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        if mtime is not None:
            os.utime(path, (mtime, mtime))

    def test_search_web_parses_and_filters_relevant(self):
        with mock.patch.object(market.requests, "get", return_value=FakeResp()) as m:
            items = market.search_web("新加坡留学 费用", top=5)
        m.assert_called_once()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "新加坡留学 费用 2026")
        self.assertEqual(items[0]["url"], "http://example.com/1")

    def test_search_web_failure_returns_empty(self):
        with mock.patch.object(market.requests, "get", side_effect=OSError("timeout")):
            self.assertEqual(market.search_web("新加坡留学"), [])

    def test_search_web_non_200_returns_empty(self):
        with mock.patch.object(market.requests, "get", return_value=FakeResp(status=503)):
            self.assertEqual(market.search_web("新加坡留学"), [])

    def test_intel_reads_knowledge_files(self):
        self._write("图谱", "# 竞对图谱\n| 竞对 | 劣势 |")
        self._write("热点", "# 新加坡留学热点雷达\n| 热度 | 话题 |", mtime=os.path.getmtime(os.path.dirname(__file__)))
        text = market.intel()
        self.assertIn("【图谱】", text)
        self.assertIn("竞对图谱", text)
        self.assertIn("【热点话题】", text)

    def test_intel_stale_triggers_live_fallback(self):
        self._write("热点", "# 旧热点", mtime=1)
        with mock.patch.object(market, "hot_topics", return_value=[
                {"title": "新加坡留学新热点", "url": "http://x", "snippet": "家长关心"}]):
            text = market.intel()
        self.assertIn("实时热点（搜索·尽力而为", text)
        self.assertIn("新加坡留学新热点", text)

    def test_intel_missing_all_returns_placeholder(self):
        with mock.patch.object(market, "hot_topics", return_value=[]):
            self.assertEqual(market.intel(), "（暂无市场情报，尚未同步雷达/竞对库）")

    def test_refresh_writes_files(self):
        hot = [{"title": "AEIS新规", "url": "http://a", "snippet": "报名门槛"},
               {"title": "PSB收紧", "url": "http://b", "snippet": "不收初中毕业生"}]
        leads = [{"title": "新东方前途", "url": "http://c", "snippet": "文书模板"}]
        with mock.patch.object(market, "hot_topics", return_value=hot), \
             mock.patch.object(market, "competitor_leads", return_value=leads):
            res = market.refresh()
        self.assertEqual(res, {"hot": 2, "leads": 1})
        with open(os.path.join(self._tmp, market._FILES["热点"]), encoding="utf-8") as f:
            hot_text = f.read()
        with open(os.path.join(self._tmp, market._FILES["线索"]), encoding="utf-8") as f:
            leads_text = f.read()
        self.assertIn("AEIS新规", hot_text)
        self.assertIn("新东方前途", leads_text)


if __name__ == "__main__":
    unittest.main()
