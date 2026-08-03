import unittest, json
from unittest import mock
from brain import search

def _payload(hits):
    return json.dumps({"query": {"search": hits}})

class TestSearch(unittest.TestCase):
    def test_verify_returns_hits(self):
        with mock.patch.object(search, "_http", return_value=_payload([
            {"title": "新加坡国立大学", "snippet": "<span>国立大学</span>"}
        ])):
            hits = search.verify("新加坡国立大学")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["title"], "新加坡国立大学")
        self.assertIn("国立大学", hits[0]["snippet"])
        self.assertIn("zh.wikipedia.org", hits[0]["url"])

    def test_verify_no_results(self):
        with mock.patch.object(search, "_http", return_value=_payload([])):
            self.assertEqual(search.verify("不存在的事实"), [])

    def test_verify_network_error_empty(self):
        with mock.patch.object(search, "_http", side_effect=OSError("down")):
            self.assertEqual(search.verify("x"), [])

    def test_verify_hits_wikipedia_api(self):
        captured = {}
        def fake(url):
            captured["url"] = url
            return _payload([])
        with mock.patch.object(search, "_http", side_effect=fake):
            search.verify("新加坡国立大学")
        self.assertIn("zh.wikipedia.org/w/api.php", captured["url"])
        self.assertIn("action=query", captured["url"])
        self.assertIn("srsearch", captured["url"])

if __name__ == "__main__":
    unittest.main()
