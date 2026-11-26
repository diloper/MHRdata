import unittest

from game8_lbg import apply_supplement, build_supplement, parse_index
from kiranico_schema import field

INDEX = "https://game8.co/games/Monster-Hunter-Rise/archives/324830"
FAMILY = "https://game8.co/games/Monster-Hunter-Rise/archives/400001"
HEADER = "<tr><th>Ammo</th><th>Capacity｜Recoil｜Reload</th><th>Shot Type</th></tr>"


def ammo_rows(*rows):
    return "".join(f"<tr><td>{name}</td><td>{stats}</td><td>{shots}</td></tr>" for name, stats, shots in rows)


INDEX_HTML = (f'<h3>Rathalos Tree</h3><table><tr><td><a class="a-link" href="/games/Monster-Hunter-Rise/'
              f'archives/400001#top">Sticky Gun</a></td><td><a class="a-link" href="{FAMILY}">Sticky Gun II</a>'
              '</td><td><a class="a-link" href="https://evil.example/games/Monster-Hunter-Rise/archives/1">X</a>'
              '</td></tr></table><h3>Popular Guides</h3><table><tr><td><a class="a-link" href="/games/'
              'Monster-Hunter-Rise/archives/999">Not a tree</a></td></tr></table>').encode()
FAMILY_HTML = (f"<h3>Sticky Gun II</h3><table>{HEADER}"
               + ammo_rows(("Sticky Ammo 1", "4｜Low｜Fast", "RF MR"), ("Normal Ammo 1", "6 | Average | Slow", "MS"))
               + f"</table><h3>Sticky Gun</h3><table>{HEADER}"
               + ammo_rows(("Sticky Ammo 1", "3｜Some｜Fast", "SR"), ("Bad Ammo", "broken", "SR"))
               + f"</table><h3>Unrelated</h3><table>{HEADER}</table>").encode()


def light_bowgun(name, ammo):
    return {"id": name, "fields": {"name": field(name, "direct"), "weapon_type": field("Light Bowgun", "direct"),
                                   "ammo": field(ammo, "derived")}, "warnings": []}


class Game8Tests(unittest.TestCase):
    def test_index_and_family_tables(self):
        self.assertEqual(parse_index(INDEX_HTML), {FAMILY: ["Sticky Gun", "Sticky Gun II"]})
        supplement = build_supplement(INDEX_HTML, {FAMILY: (FAMILY_HTML, "2024-01-01T00:00:00+00:00")})
        weapons = {entry["game8_name"]: entry["ammo"] for entry in supplement["weapons"]}
        self.assertEqual(set(weapons), {"Sticky Gun", "Sticky Gun II"})
        self.assertEqual(weapons["Sticky Gun II"][0], {"name": "Sticky Ammo 1", "capacity": 4, "recoil": "Low",
                                                       "reload": "Fast", "shot_types": ["RF", "MR"],
                                                       "rapid_fire": True})
        self.assertEqual(weapons["Sticky Gun II"][1]["recoil"], "Average")
        self.assertEqual([ammo["name"] for ammo in weapons["Sticky Gun"]], ["Sticky Ammo 1"])
        self.assertTrue(any("Bad Ammo" in warning for warning in supplement["warnings"]))
        self.assertTrue(any("Unrelated" in warning for warning in supplement["warnings"]))
        self.assertEqual(supplement["pages"][0]["url"], FAMILY)
        missing = build_supplement(INDEX_HTML, {})
        self.assertEqual((missing["weapons"], len(missing["warnings"])), ([], 1))

    def test_apply_keeps_kiranico_and_reports_mismatches(self):
        supplement = build_supplement(INDEX_HTML, {FAMILY: (FAMILY_HTML, "2024-01-01T00:00:00+00:00")})
        supplement["weapons"].append({"game8_name": "Unknown Gun", "page_url": FAMILY, "ammo": []})
        records = [light_bowgun("Sticky Gun II", [{"name": "Sticky Ammo 1", "capacity": 3}]),
                   light_bowgun("Sticky Gun", [{"name": "Sticky Ammo 1", "capacity": 3}]),
                   {"id": "hbg", "fields": {"name": field("Sticky Gun", "direct"),
                                            "weapon_type": field("Heavy Bowgun", "direct")}}]
        summary = apply_supplement(records, supplement)
        self.assertEqual((summary["matched"], summary["unmatched"], summary["capacity_mismatches"],
                          summary["rapid_fire_weapons"]), (2, ["Unknown Gun"], 1, 1))
        second, first, heavy = records
        self.assertEqual(second["fields"]["ammo"]["value"][0]["capacity"], 3)
        self.assertIn("Kiranico kept", second["warnings"][0])
        self.assertEqual(second["fields"]["rapid_fire_ammo"]["value"], ["Sticky Ammo 1"])
        self.assertEqual(second["fields"]["rapid_fire_ammo"]["availability"], "supplemental")
        handling = second["fields"]["ammo_handling"]["value"][0]
        self.assertEqual(handling["recoil_lookup"]["required_level"], 1)
        self.assertEqual(first["fields"]["rapid_fire_ammo"]["value"], [])
        self.assertEqual(first["fields"]["ammo_handling"]["value"][0]["recoil_lookup"]["status"], "unmapped")
        self.assertNotIn("rapid_fire_ammo", heavy["fields"])


if __name__ == "__main__":
    unittest.main()
