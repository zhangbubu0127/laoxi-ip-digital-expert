import os
import tempfile
import unittest

from brain import used_topics


class TestUsedTopics(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "已用选题.md")
        used_topics._USED_PATH = self.path

    def tearDown(self):
        self.dir.cleanup()

    def test_load_empty_when_missing(self):
        self.assertEqual(used_topics.load_used(), [])

    def test_add_and_load_in_order(self):
        used_topics.add_used("选题A")
        used_topics.add_used("选题B")
        self.assertEqual(used_topics.load_used(), ["选题A", "选题B"])

    def test_add_skip_blank(self):
        used_topics.add_used("  ")
        used_topics.add_used("")
        self.assertEqual(used_topics.load_used(), [])

    def test_add_dedup(self):
        used_topics.add_used("选题A")
        used_topics.add_used("选题A")
        self.assertEqual(used_topics.load_used(), ["选题A"])

    def test_load_skips_header_and_dedups(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("# 已用选题库\n- 选题A\n- 选题A\n- 选题B\n")
        self.assertEqual(used_topics.load_used(), ["选题A", "选题B"])


if __name__ == "__main__":
    unittest.main()
