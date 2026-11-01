import json
import tempfile
import unittest
from pathlib import Path

from analyze_sticky_lbg import build_report, recoil_reload, write_text_pack


def weapon(record_id, name, view, sticky_row, stats):
    return {
        "id": record_id, "name": name, "source_url": f"https://example.test/{record_id}",
        "source_group": view,
        "tables": [{"section": "Overview", "headers": [], "rows": [
            [{"text": "Sti"}, *({"text": value} for value in sticky_row)],
            [{"text": stats}],
        ]}],
    }


class StickyLightBowgunTests(unittest.TestCase):
    def test_extracts_profiles_and_sorts_sticky_capacity(self):
        light_bowgun = weapon("1", "Sticky III", "view-13", ["1", "2", "3"],
                              "Deviation None Recoil Average Reload Fast Nrm 5 6 4")
        light_bowgun_two = weapon("2", "Sticky II", "view-13", ["3", "3", "0"],
                                  "Deviation Mild Recoil High Reload Slow Nrm 1 2 3")
        heavy_bowgun = weapon("3", "Not an LBG", "view-12", ["3", "3", "3"], "")
        armor = {
            "id": "10", "name": "Slugger Mail", "source_url": "https://example.test/10",
            "source_group": "view-4", "skills": [{
                "skill": {"id": "20", "name": "Slugger"}, "equipped_level": 2,
            }], "forging_materials": [{
                "item": {"id": "40", "name": "Ore", "link": "https://example.test/40"},
                "amount": "x2",
            }],
        }
        decoration = {
            "id": "30", "name": "KO Jewel", "source_url": "https://example.test/30",
            "skills": [{"skill": {"id": "20", "name": "Slugger"}, "equipped_level": 1}],
            "materials": [{
                "item": {"id": "41", "name": "Monster Bone", "link": "https://example.test/41"},
                "amount": "x1",
            }],
        }
        skill = {
            "id": "20", "name": "Slugger", "source_url": "https://example.test/20",
            "lv1": "Stun power +20%", "lv3": "Stun power +40%",
        }
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary)
            (snapshot / "structured").mkdir()
            (snapshot / "download-manifest.json").write_text(json.dumps({
                "run_status": "success", "mode": "full", "complete": True,
                "snapshot_id": "snapshot-1",
            }), encoding="utf-8")
            data = {
                "Weapons": [light_bowgun_two, heavy_bowgun, light_bowgun],
                "Armors": [armor], "Decorations": [decoration], "Skills": [skill],
                "Items": [{
                    "id": "50", "name": "Sticky Ammo 1", "source_url": "https://example.test/50",
                    "description": "A sticky round.", "source_version": "16.0.0",
                    "tables": [{"section": "Weapons", "headers": [], "rows": [[
                        {"text": "Example Bowgun", "links": [{"name": "Example Bowgun", "url": "https://example.test/1"}], "annotations": []},
                    ]]}],
                }],
            }
            for category, records in data.items():
                (snapshot / "structured" / f"{category}.json").write_text(
                    json.dumps({"data": records}), encoding="utf-8")

            report = build_report(snapshot)
            filenames = write_text_pack(snapshot, snapshot / "agent-builder-txt", report)
            weapon_text = (snapshot / "agent-builder-txt" / "01-sticky-lbg-weapons.txt").read_text(encoding="utf-8")
            equipment_text = (snapshot / "agent-builder-txt" / "02-sticky-lbg-equipment-skills.txt").read_text(encoding="utf-8")
            ammo_text = (snapshot / "agent-builder-txt" / "03-sticky-ammo-reference.txt").read_text(encoding="utf-8")

        self.assertEqual([entry["name"] for entry in report["weapons"]], ["Sticky III", "Sticky II"])
        self.assertEqual(report["weapons"][0]["sticky_ammo_capacity"], {"1": 1, "2": 2, "3": 3})
        self.assertEqual((report["weapons"][0]["recoil"], report["weapons"][0]["reload"]),
                         ("Average", "Fast"))
        self.assertEqual(report["armor_candidates"][0]["relevant_skills"][0]["equipped_level"], 2)
        self.assertEqual(report["decoration_candidates"][0]["name"], "KO Jewel")
        self.assertEqual(report["relevant_skills"][0]["effects"]["lv3"], "Stun power +40%")
        self.assertEqual(report["mr10_eligibility"]["status"], "unverified")
        self.assertEqual(len(filenames), 3)
        self.assertIn("Sticky ammo capacity: Lv1 1; Lv2 2; Lv3 3", weapon_text)
        self.assertIn("Source URL: https://example.test/1", weapon_text)
        self.assertIn("MR10 eligibility: unverified", weapon_text)
        self.assertIn("Slugger (level 2; ID: 20)", equipment_text)
        self.assertIn("Ore x2 (ID: 40)", equipment_text)
        self.assertIn("Monster Bone x1 (ID: 41)", equipment_text)
        self.assertIn("Sticky Ammo 1", ammo_text)
        self.assertIn("MR10 availability: not established", ammo_text)
        self.assertIn("https://example.test/1", ammo_text)

    def test_requires_successful_complete_full_snapshot(self):
        for manifest in (
                {"run_status": "running", "mode": "full", "complete": False},
                {"run_status": "success", "mode": "pilot", "complete": False},
                {"run_status": "success", "mode": "full", "complete": False}):
            with self.subTest(manifest=manifest), tempfile.TemporaryDirectory() as temporary:
                snapshot = Path(temporary)
                (snapshot / "download-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaises(ValueError):
                    build_report(snapshot)

    def test_recoil_reload_parser_handles_multiword_values(self):
        record = weapon("1", "Example", "view-13", ["1", "0", "0"],
                        "Deviation LR Mild Recoil Above Avg. Reload Above Avg. Nrm 1 2 3")
        self.assertEqual(recoil_reload(record), ("Above Avg.", "Above Avg."))


if __name__ == "__main__":
    unittest.main()
