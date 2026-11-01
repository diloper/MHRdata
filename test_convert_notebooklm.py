import json
import tempfile
import unittest
from pathlib import Path

from convert_notebooklm import SOURCES, build_documents, convert, load_sources


class ConversionTests(unittest.TestCase):
    def test_workspace_data(self):
        source = Path(__file__).resolve().parent
        original = {category: (source / (category + ".json")).read_bytes() for category in SOURCES}
        documents, report = load_sources(source)
        files = build_documents(documents, report, 180000)
        for category in SOURCES:
            count = sum(content.count("\n## ") for filename, content in files.items()
                        if filename.startswith(category + "-"))
            self.assertEqual(count, report["sources"][category]["output_records"])
        self.assertTrue(all(len(content) <= 180000 for content in files.values()))
        self.assertIn("Attack +5% Bonus: +7", "".join(files.values()))
        self.assertIn("Lazurite Jewel", "".join(files.values()))
        with tempfile.TemporaryDirectory() as directory:
            first = convert(source, directory)
            before = {name: (Path(directory) / name).read_bytes() for name in first["generated_files"]}
            second = convert(source, directory)
            self.assertEqual(first, second)
            self.assertEqual(before, {name: (Path(directory) / name).read_bytes()
                                      for name in second["generated_files"]})
        for category in SOURCES:
            self.assertEqual(original[category], (source / (category + ".json")).read_bytes())

    def test_duplicate_conflict_and_rampage_namespace(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            skill = {"name": "Normal", "id": "1", "lv1": "NORMAL EFFECT"}
            for category in SOURCES:
                records = []
                if category == "Skills":
                    records = [skill, skill.copy(), {"name": "Other", "id": "1"}]
                if category == "Weapons":
                    records = [{"name": "Weapon", "id": "2", "weapon_type": 0,
                                "rampage_skills": [{"skill": {"name": "Rampage", "id": "1"},
                                                    "description": "RAMPAGE EFFECT"}],
                                "forging_materials": []}]
                (source / (category + ".json")).write_text(json.dumps({"data": records}), encoding="utf-8")
            documents, report = load_sources(source)
            files = build_documents(documents, report, 2000)
            self.assertEqual(report["sources"]["Skills"]["exact_duplicates_removed"], 1)
            self.assertEqual(report["sources"]["Skills"]["conflicting_ids"], ["1"])
            weapon = next(content for name, content in files.items() if name.startswith("Weapons-"))
            self.assertIn("RAMPAGE EFFECT", weapon)
            self.assertNotIn("NORMAL EFFECT", weapon)
            self.assertIn("empty list", weapon)
            self.assertEqual(report["warnings"], [])

    def test_split_and_stale_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            for category in SOURCES:
                records = [{"name": f"Record {number}", "id": str(number),
                            "description": "Details " * 100} for number in range(5)]
                (source / (category + ".json")).write_text(json.dumps({"data": records}), encoding="utf-8")
            output = Path(directory) / "output"
            first = convert(source, output, 2000)
            unrelated = output / "personal.md"
            unrelated.write_text("Keep me", encoding="utf-8")
            second = convert(source, output, 180000)
            self.assertGreater(len(first["generated_files"]), len(second["generated_files"]))
            self.assertTrue(unrelated.exists())
            self.assertEqual({path.name for path in output.glob("*.md")},
                             set(second["generated_files"]) | {"personal.md"})


if __name__ == "__main__":
    unittest.main()