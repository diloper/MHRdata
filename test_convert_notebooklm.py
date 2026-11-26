import json
import tempfile
import unittest
from pathlib import Path

import bowgun_rules
import kiranico_schema as schema
from convert_notebooklm import SOURCES, build_documents, convert, load_rules, load_sources, render_record
from kiranico_listing import parse_listing
from test_kiranico_listing import (ammo_table, armor_row, decoration_row, item_grid, listing_page,
                                   rampage_skill_row, skill_row, weapon_row)

LISTINGS = {
    "Weapons": [("Great Sword", weapon_row("1", "Blade"), "/data/weapons?view=0", None),
                ("Light Bowgun", weapon_row("2", "Gun", "Light Bowgun", detail=ammo_table({"Sti": [3, 2, 1]})),
                 "/data/weapons?view=13", None)],
    "Armors": [("Armor", armor_row("5", "Helm"), "/data/armors?view=7", "Rarity 8")],
    "Decorations": [("Decorations", decoration_row("9", "KO Jewel 2"), "/data/decorations", None)],
    "Skills": [("Skills", skill_row("42", "Slugger", ["Stun power +20%", "Stun power +30%"])
                + skill_row("1", "Attack Boost", ["Attack +3", "Attack +5% Bonus: +7"]), "/data/skills", None)],
    "Items": [("Items", item_grid(("3", "Potion")), "/data/items?view=0", "Healing")],
    "RampageSkills": [("Rampage Skills", rampage_skill_row("7", "Paralysis Boost"), "/data/rampage-skills", None)],
}


def typed_snapshot(directory):
    """Write schema-v2 structured files parsed from fixture listing pages; return the source dir."""
    source = directory / "structured"
    source.mkdir()
    for category, pages in LISTINGS.items():
        data = []
        for title, rows, path, label in pages:
            _, records, errors = parse_listing(category, listing_page(title, rows, path, label).encode(),
                                               schema.BASE_URL + path)
            assert not errors, errors
            data += [{"id": record["id"], "name": record["fields"]["name"]["value"], "source_url": record["key"],
                      "detail_status": "not_fetched", "fields": record["fields"], "warnings": record["warnings"]}
                     for record in records]
        if category == "Items":
            data[0]["tables"] = [{"section": "Nested", "headers": [], "rows": [], "nested": True}]
        document = {"schema_version": 2, "category": category, "version": "16.0.0", "date": "snapshot-1",
                    "snapshot_mode": "listings", "field_statistics": schema.field_statistics(category, data),
                    "coverage": {"parsed": len(data), "discovered": len(data), "missing": [], "row_errors": []},
                    "data": data}
        (source / f"{category}.json").write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    (source / "required-fields.json").write_text(json.dumps(schema.required_fields_document()), encoding="utf-8")
    (source / "bowgun-rules.json").write_text(json.dumps(bowgun_rules.rules_document()), encoding="utf-8")
    return source


class ConversionTests(unittest.TestCase):
    def test_legacy_armor_slots_are_unknown_not_inferred(self):
        armor = {"id": "1", "name": "Legacy", "decorationsNumList": [0, 2, 1, 0]}
        warnings = []
        content = render_record("Armors", armor, {}, warnings)
        self.assertIn("- Decoration slot levels: Unknown\n- Total decoration slots: Unknown\n", content)
        self.assertIn("decorationsNumList", content)
        self.assertEqual(warnings[0]["field"], "decoration_slot_levels")
        self.assertEqual(warnings[0]["id"], "1")
        for invalid in ([0], [True], "2"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "Armors/1/decoration_slot_levels"):
                render_record("Armors", dict(armor, decoration_slot_levels=invalid), {}, [])

    def test_typed_snapshot_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            source = typed_snapshot(Path(directory))
            original = {path.name: path.read_bytes() for path in source.iterdir()}
            documents, report = load_sources(source)
            files = build_documents(documents, report, 180000, load_rules(source))
            for category in documents:
                count = sum(content.count("\n## ") for filename, content in files.items()
                            if filename.startswith(category + "-"))
                self.assertEqual(count, report["sources"][category]["output_records"])
            self.assertTrue(all(len(content) <= 180000 for content in files.values()))
            self.assertEqual(sorted(files), [
                "Armors-rarity-8-001.md", "Decorations-all-001.md", "Items-Healing-001.md",
                "RampageSkills-all-001.md", "Rules-bowgun-001.md", "Skills-all-001.md",
                "Weapons-Great-Sword-001.md", "Weapons-Light-Bowgun-001.md"])
            self.assertEqual(report["sources"]["Armors"]["required_fields"]["part"],
                             {"unavailable_from_kiranico": 1})
            armor = files["Armors-rarity-8-001.md"]
            self.assertIn("- 部位 = Armor part\n", armor)
            self.assertIn("- Armor part: Not published by Kiranico\n", armor)
            self.assertIn("- Slot notation: 1-1-0 (derived)\n", armor)
            self.assertIn("- Skills: Slugger Lv2\n  - Slugger Lv2: Stun power +30%\n", armor)
            self.assertNotIn("Decoration slot levels", armor)
            gun = files["Weapons-Light-Bowgun-001.md"]
            self.assertIn("- Rapid Fire ammo: Supplement not available (run game8\\_lbg.py)\n", gun)
            self.assertIn("- Rampage skills: Not fetched in this snapshot mode", gun)
            self.assertIn("Gray (text-gray-400) cells", gun)
            self.assertNotIn("Sharpness", gun.split("\n## ", 1)[1])
            self.assertIn("Attack +5% Bonus: +7", files["Skills-all-001.md"])
            self.assertIn("- Effect: Paralysis buildup +2", files["RampageSkills-all-001.md"])
            self.assertIn("## Recoil Down (recoil) to reach", files["Rules-bowgun-001.md"])
            self.assertEqual(report["nested_tables_skipped"], 1)
            output = Path(directory) / "output"
            first = convert(source, output)
            before = {name: (output / name).read_bytes() for name in first["generated_files"]}
            second = convert(source, output)
            self.assertEqual(first, second)
            self.assertEqual(before, {name: (output / name).read_bytes() for name in second["generated_files"]})
            self.assertEqual(original, {path.name: path.read_bytes() for path in source.iterdir()})

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