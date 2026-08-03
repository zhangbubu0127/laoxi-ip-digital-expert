import unittest, tempfile, os
import brain.material as material

class TestMaterial(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        material._MATERIAL_DIR = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_writes_file_and_roundtrips(self):
        name = material.save_material("普娃预算对比的文章要点", source="测试")
        self.assertTrue(name.endswith(".md"))
        with open(os.path.join(self.tmp.name, name), "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("普娃预算对比的文章要点", text)
        self.assertIn("来源：测试", text)

    def test_save_returns_unique_names(self):
        a = material.save_material("第一条")
        b = material.save_material("第二条")
        self.assertNotEqual(a, b)

    def test_save_rejects_empty(self):
        with self.assertRaises(ValueError):
            material.save_material("   ")

    def test_recent_materials_empty(self):
        self.assertEqual(material.recent_materials(), "（暂无素材）")

    def test_recent_materials_returns_latest_first(self):
        material.save_material("第一条素材")
        material.save_material("第二条素材")
        out = material.recent_materials()
        self.assertIn("第二条素材", out)
        self.assertLess(out.index("第二条素材"), out.index("第一条素材"))

    def test_recent_materials_truncates_long(self):
        material.save_material("甲" * 500)
        out = material.recent_materials(per_file_chars=100)
        self.assertIn("…", out)
        self.assertLess(len(out), 150)

if __name__ == "__main__":
    unittest.main()
