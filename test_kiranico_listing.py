import unittest

import bowgun_rules
import kiranico_schema as schema
from kiranico_listing import (AMMO_NAMES, SHARPNESS_COLORS, detail_skill_levels, effect_components,
                              parse_listing)

BASE = schema.BASE_URL


def listing_page(title, rows, path, view_label=None, nav=""):
    """Minimal Kiranico listing page; ``view_label`` adds the view tab that links to itself."""
    tab = f'<a href="{path}">{view_label}</a><a href="{path}">English</a>' if view_label else ""
    body = rows if rows.lstrip().startswith("<div") else f"<table><tbody>{rows}</tbody></table>"
    return (f'<html><body><nav><h2>Database Ver16.0.0</h2>{nav}{tab}</nav><article>'
            f'<h1>{title}</h1>{body}</article></body></html>')


def slots_html(label, levels):
    icons = "".join(f'<img src="https://cdn.kiranico.net/deco{level}.png">' for level in levels)
    return f"<div><small>{label}</small>{icons}</div>"


def sharpness_svg(widths):
    rects = "".join(f'<rect fill="{color}" width="{width}"/>'
                    for (_, color), width in zip(SHARPNESS_COLORS, widths))
    return f"<svg>{rects}</svg>"


def ammo_table(capacities=None, gray=()):
    """Bowgun stats table: every ammo code, zero unless listed in ``capacities``."""
    capacities = capacities or {}
    rows = []
    for code, (_, levels) in AMMO_NAMES.items():
        values = capacities.get(code, [0] * levels)
        cells = "".join(f'<td class="{"text-gray-400" if (code, index) in gray else ""}">{value}</td>'
                        for index, value in enumerate(values))
        rows.append(f"<tr><td>{code}</td>{cells}</tr>")
    columns = "".join(f"<td><table>{''.join(rows[index::4])}</table></td>" for index in range(4))
    return ("<table><tr><td><div>Deviation None</div><div>Recoil Low</div><div>Reload Fast</div></td>"
            f"{columns}</tr></table>")


def weapon_row(record_id, name, weapon_type="Great Sword", rarity=3, attack=100, slots=(2, 1),
               rampage=(1,), stats="", detail=None, extras=""):
    if detail is None:
        detail = (ammo_table() if weapon_type in schema.BOWGUN_TYPES
                  else sharpness_svg([2, 2, 2, 2, 2, 0, 0]) + sharpness_svg([2, 2, 2, 2, 2, 2, 0]))
    return (f'<tr><td><img src="w.webp"></td><td><a href="/data/weapons/{record_id}">{name}</a></td>'
            f'<td>{slots_html("Slots", slots)}{slots_html("Rampage Slots", rampage)}</td>'
            f'<td><span data-key="attack">{attack}</span></td><td>{stats}</td><td>{detail}</td>'
            f'<td><div class="columns-3">{extras}</div></td><td>Rare {rarity}</td></tr>')


def resistance(code, value):
    return f'<span data-key="element" data-value="{code}"><span data-key="elementAttack" data-value="{value}">{value}</span></span>'


def armor_row(record_id, name, slots=(1, 1), skills=(("42", "Slugger", 2),)):
    skill_html = "".join(f'<div><a href="/data/skills/{skill_id}">{skill}</a> Lv {level}</div>'
                         for skill_id, skill, level in skills)
    return (f'<tr><td><img src="m.webp"></td><td><img src="f.webp"></td>'
            f'<td><a href="/data/armors/{record_id}">{name}</a></td>'
            f'<td>{"".join(f"<img src=deco{level}.png>" for level in slots)}</td>'
            f'<td><div>32</div>{resistance(1, 2)}{resistance(2, -1)}</td>'
            f'<td>{resistance(3, 0)}{resistance(4, 1)}{resistance(5, -3)}</td><td>{skill_html}</td></tr>')


def decoration_row(record_id, name, skill=("42", "Slugger", 1)):
    return (f'<tr><td><a href="/data/decorations/{record_id}">{name}</a></td>'
            f'<td><div><a href="/data/skills/{skill[0]}">{skill[1]}</a> Lv {skill[2]}</div></td>'
            f'<td>Stun power +20%</td></tr>')


def skill_row(record_id, name, levels=("Stun power +20%",)):
    level_html = "".join(f"<p><small>Lv {number} {effect}</small></p>"
                         for number, effect in enumerate(levels, 1))
    return (f'<tr><td><a href="/data/skills/{record_id}"><p>{name}</p></a></td>'
            f'<td><p>Makes it easier to stun monsters.</p>{level_html}</td></tr>')


def item_grid(*items):
    return '<div class="grid">' + "".join(f'<a href="/data/items/{record_id}"><p>{name}</p></a>'
                                          for record_id, name in items) + "</div>"


def rampage_decoration_row(name, skill_href="/data/rampage-skills/7", skill="Anti-Aerial Species"):
    return (f'<tr><td>{name}</td><td><a href="{skill_href}">{skill}</a></td>'
            f'<td>A decoration that gives weapons the {skill} skill.</td></tr>')


def rampage_skill_row(record_id, name, effect="Paralysis buildup +2"):
    return f'<tr><td><a href="/data/rampage-skills/{record_id}">{name}</a></td><td>{effect}</td></tr>'


def parse(category, html, url):
    meta, records, errors = parse_listing(category, html.encode(), url)
    return meta, {record["id"]: record for record in records}, errors


class WeaponListingTests(unittest.TestCase):
    def test_melee_row_typed_fields(self):
        row = weapon_row("1", "Blade", rarity=6, attack=200, slots=(1, 3), rampage=(2,),
                         stats=resistance(5, 12) + " Affinity -10%")
        _, records, errors = parse("Weapons", listing_page("Great Sword", row, "/data/weapons?view=0"),
                                   BASE + "/data/weapons?view=0")
        self.assertEqual(errors, [])
        fields = records["1"]["fields"]
        self.assertEqual(fields["rarity"]["value"], 6)
        self.assertEqual(fields["attack"]["value"], 200)
        self.assertEqual((fields["affinity"]["value"], fields["affinity"]["availability"]), (-10, "direct"))
        self.assertEqual(fields["element"]["value"][0]["type"], "Dragon")
        self.assertEqual(fields["slot_notation"]["value"], "3-1-0")
        self.assertEqual(fields["rampage_slot_level"]["value"], 2)
        self.assertEqual(fields["sharpness"]["value"]["max"]["white"], 10)
        self.assertEqual(fields["ammo"]["availability"], "not_applicable")
        self.assertEqual(records["1"]["warnings"], [])

    def test_bowgun_gray_cells_and_threshold_lookup(self):
        capacities = {"Sti": [2, 3, 1], "Nrm": [4, 5, 6]}
        detail = ammo_table(capacities, gray={("Sti", 2)})
        row = weapon_row("2", "Gun", "Light Bowgun", detail=detail, slots=(), rampage=())
        _, records, errors = parse("Weapons", listing_page("Light Bowgun", row, "/data/weapons?view=13"),
                                   BASE + "/data/weapons?view=13")
        self.assertEqual(errors, [])
        fields = records["2"]["fields"]
        ammo = {entry["name"]: entry["capacity"] for entry in fields["ammo"]["value"]}
        self.assertEqual(ammo, {"Normal Ammo 1": 4, "Normal Ammo 2": 5, "Normal Ammo 3": 6,
                                "Sticky Ammo 1": 2, "Sticky Ammo 2": 3})
        self.assertEqual(fields["ammo"]["evidence"]["gray_nonzero_capacity"][0]["name"], "Sticky Ammo 3")
        self.assertEqual(fields["slot_notation"]["value"], "0-0-0")
        self.assertEqual(fields["recoil_lookup"]["value"]["required_level"], 1)
        self.assertEqual(fields["reload_lookup"]["value"]["required_level"], 0)
        self.assertEqual(fields["rapid_fire_ammo"]["availability"], "supplemental_missing")
        self.assertEqual(fields["sharpness"]["availability"], "not_applicable")

    def test_bow_gray_coatings_and_gray_charge_levels(self):
        detail = ('<small><div class="text-green-500">Brace</div><div>Rapid Level 3</div>'
                  '<div>Spread Level 3</div><div class="text-gray-400">Spread Level 4</div></small>')
        extras = ('<div class="">Power Coating</div><div class="text-gray-400">Para Coating</div>'
                  '<div class="text-green-500">Sleep Coating</div>')
        row = weapon_row("4", "Bow", "Bow", detail=detail, extras=extras)
        _, records, errors = parse("Weapons", listing_page("Bow", row, "/data/weapons?view=11"),
                                   BASE + "/data/weapons?view=11")
        self.assertEqual(errors, [])
        fields = records["4"]["fields"]
        self.assertEqual(fields["bow_arc_shot"]["value"], "Brace")
        self.assertEqual(fields["bow_charge_levels"]["value"], ["Rapid Level 3", "Spread Level 3"])
        self.assertEqual(fields["bow_gray_charge_levels"]["value"], ["Spread Level 4"])
        self.assertEqual(fields["bow_coatings"]["value"], ["Power Coating", "Sleep Coating"])
        self.assertEqual(fields["bow_coatings"]["evidence"],
                         {"gray_not_usable": ["Para Coating"], "green_highlighted": ["Sleep Coating"]})
        self.assertEqual(records["4"]["warnings"], [])

    def test_bad_row_is_reported_not_guessed(self):
        row = weapon_row("3", "Broken").replace("<td>Rare 3</td>", "")
        _, records, errors = parse("Weapons", listing_page("Great Sword", row, "/data/weapons?view=0"),
                                   BASE + "/data/weapons?view=0")
        self.assertEqual(records, {})
        self.assertIn("Expected 8 weapon cells", errors[0])


class OtherListingTests(unittest.TestCase):
    def test_armor_rarity_comes_from_view_tab_not_language_link(self):
        html = listing_page("Armor", armor_row("5", "Helm", slots=(1, 4)), "/data/armors?view=7", "Rarity 8")
        _, records, errors = parse("Armors", html, BASE + "/data/armors?view=7")
        self.assertEqual(errors, [])
        fields = records["5"]["fields"]
        self.assertEqual(fields["rarity"]["value"], 8)
        self.assertEqual(fields["slot_notation"]["value"], "4-1-0")
        self.assertEqual(fields["resistances"]["value"],
                         {"fire": 2, "water": -1, "thunder": 0, "ice": 1, "dragon": -3})
        self.assertEqual(fields["skills"]["value"], [{"name": "Slugger", "id": "42", "level": 2}])
        for key in ("part", "gender_names", "defense_max"):
            self.assertEqual(fields[key]["availability"], "unavailable_from_kiranico")

    def test_decoration_size_and_skill(self):
        html = listing_page("Decorations", decoration_row("9", "KO Jewel 2"), "/data/decorations")
        _, records, _ = parse("Decorations", html, BASE + "/data/decorations")
        fields = records["9"]["fields"]
        self.assertEqual(fields["slot_size"]["value"], 2)
        self.assertEqual(fields["skills"]["value"][0]["level"], 1)
        self.assertEqual(fields["unlock"]["availability"], "unavailable_from_kiranico")

    def test_skill_levels_components_and_blank_rows(self):
        rows = (skill_row("1", "Attack Boost", ["Attack +3", "Attack +5% Bonus: +7"])
                + '<tr><td><a href="/data/skills/2"><p>Blank</p></a></td>'
                  '<td><p>Desc</p><p><small></small></p></td></tr>')
        _, records, errors = parse("Skills", listing_page("Skills", rows, "/data/skills"), BASE + "/data/skills")
        self.assertEqual(errors, [])
        attack = records["1"]["fields"]
        self.assertEqual(attack["max_level"]["value"], 2)
        self.assertEqual(attack["levels"]["value"][1]["components"],
                         [{"label": "Attack", "value": 5, "unit": "%", "kind": "delta"},
                          {"label": "Bonus", "value": 7, "unit": None, "kind": "delta"}])
        blank = records["2"]["fields"]
        self.assertEqual(blank["max_level"]["availability"], "not_fetched")
        self.assertEqual(blank["levels"]["availability"], "unavailable_from_kiranico")
        rebuilt = detail_skill_levels({"lv1": "Effect one", "lv2": "Effect two"})
        self.assertEqual((rebuilt["max_level"]["value"], rebuilt["levels"]["availability"]), (2, "direct"))
        self.assertEqual(detail_skill_levels({"lv1": ""})["levels"]["availability"], "unavailable_from_kiranico")
        self.assertEqual(effect_components("Damage x1.1"),
                         [{"label": "Damage", "value": 1.1, "unit": "x", "kind": "multiplier"}])

    def test_rampage_rows_and_empty_skill_link(self):
        rows = rampage_decoration_row("Anti-Aerial Jewel 2") + rampage_decoration_row("Broken Jewel 1", "")
        _, records, errors = parse("RampageDecorations", listing_page("Rampage Decorations", rows,
                                   "/data/rampage-decorations"), BASE + "/data/rampage-decorations")
        self.assertEqual(errors, [])
        self.assertEqual(records["anti-aerial-jewel-2"]["fields"]["rampage_skill"]["value"]["id"], "7")
        self.assertEqual(records["anti-aerial-jewel-2"]["fields"]["slot_size"]["value"], 2)
        self.assertEqual(records["broken-jewel-1"]["fields"]["rampage_skill"]["availability"], "missing")
        self.assertTrue(records["broken-jewel-1"]["warnings"])
        _, skills, _ = parse("RampageSkills", listing_page("Rampage Skills", rampage_skill_row("7", "Boost"),
                             "/data/rampage-skills"), BASE + "/data/rampage-skills")
        self.assertEqual(skills["7"]["fields"]["effect"]["value"], "Paralysis buildup +2")

    def test_items_grid_group(self):
        html = listing_page("Items", item_grid(("1", "Potion"), ("1", "Potion")), "/data/items?view=consume",
                            "Healing/Support")
        _, records, _ = parse("Items", html, BASE + "/data/items?view=consume")
        self.assertEqual(list(records), ["1"])
        self.assertEqual(records["1"]["fields"]["item_group"]["value"], "Healing/Support")


class RulesAndSchemaTests(unittest.TestCase):
    def test_threshold_ladder_matches_user_table(self):
        expected = {("recoil", "Very High"): None, ("recoil", "High"): 3, ("recoil", "Average"): 2,
                    ("recoil", "Low"): 1, ("recoil", "Very Low"): 0, ("reload", "Very Slow"): None,
                    ("reload", "Slow"): 3, ("reload", "Below Avg."): 2, ("reload", "Average"): 1,
                    ("reload", "Fastest"): 0}
        for (kind, label), level in expected.items():
            with self.subTest(kind=kind, label=label):
                result = bowgun_rules.lookup(kind, label, "per_ammo")
                self.assertEqual((result["status"], result["required_level"]), ("mapped", level))
        self.assertEqual(bowgun_rules.lookup("recoil", "Very High", "x")["best_tier_at_lv3"], "Average")
        for kind, label in (("recoil", "Some"), ("reload", "Above Avg.")):
            self.assertEqual(bowgun_rules.lookup(kind, label, "weapon_base_label")["status"], "unmapped")

    def test_field_statistics_flag_unexpected_availability(self):
        melee = {"fields": {"weapon_type": {"value": "Great Sword"},
                            "sharpness": schema.field(None, "missing"),
                            "ammo": schema.field(None, "derived")}}
        bowgun = {"fields": {"weapon_type": {"value": "Light Bowgun"},
                             "ammo": schema.field([], "unavailable_from_kiranico")}}
        stats = schema.field_statistics("Weapons", [melee, bowgun])
        self.assertEqual(stats["sharpness"]["counts"], {"missing": 1})
        self.assertEqual(stats["ammo"]["counts"], {"unavailable_from_kiranico": 1})
        self.assertEqual(stats["ammo"]["unexpected"], 1)
        self.assertEqual(stats["name"]["counts"], {"absent": 2})
        with self.assertRaises(ValueError):
            schema.field(1, "guessed")


if __name__ == "__main__":
    unittest.main()
