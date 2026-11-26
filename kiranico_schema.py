"""Kiranico category registry, required-field contract, and typed-field helpers."""

import re

BASE_URL = "https://mhrise.kiranico.com"

# Supported categories.  ``detail`` categories have numeric detail pages that a full
# crawl fetches; ``listing`` categories exist only as listing rows.
CATEGORIES = {
    "items": {"name": "Items", "kind": "detail", "views_required": True, "required": True},
    "skills": {"name": "Skills", "kind": "detail", "views_required": False, "required": True},
    "decorations": {"name": "Decorations", "kind": "detail", "views_required": False, "required": True},
    "armors": {"name": "Armors", "kind": "detail", "views_required": True, "required": True},
    "weapons": {"name": "Weapons", "kind": "detail", "views_required": True, "required": True},
    "rampage-skills": {"name": "RampageSkills", "kind": "listing", "views_required": False,
                       "required": False},
    "rampage-decorations": {"name": "RampageDecorations", "kind": "listing", "views_required": False,
                            "required": False},
}
CATEGORY_NAMES = {slug: entry["name"] for slug, entry in CATEGORIES.items()}
SLUGS = {entry["name"]: slug for slug, entry in CATEGORIES.items()}
DETAIL_CATEGORIES = [entry["name"] for entry in CATEGORIES.values() if entry["kind"] == "detail"]
LISTING_ONLY_CATEGORIES = [entry["name"] for entry in CATEGORIES.values() if entry["kind"] == "listing"]
ALL_CATEGORIES = [entry["name"] for entry in CATEGORIES.values()]
CORE_CATEGORIES = [entry["name"] for entry in CATEGORIES.values() if entry["required"]]
DETAIL_PATH = re.compile(r"/data/(items|skills|decorations|armors|weapons)/(\d+)")
LISTING_PATH = re.compile(r"/data/(" + "|".join(re.escape(slug) for slug in CATEGORIES) + r")")
REFERENCE_PATH = re.compile(r"/data/(rampage-skills)/(\d+)")

# Navigation routes seen on the Kiranico v16.0.0 home page that this project does not
# parse.  They are reported in coverage so "complete" never implies whole-site coverage.
UNSUPPORTED_ROUTES = [
    {"route": "/data/quests", "views": ["event", "mystery", "follower", "hub_master", "hub_high",
                                         "hub_low", "village", "arena", "training"],
     "label": "Quests"},
    {"route": "/data/monsters", "views": ["lg", "sm"], "label": "Monsters"},
    {"route": "/data/palamutes", "views": ["weapons", "armor"], "label": "Palamute equipment"},
    {"route": "/data/palicoes", "views": ["weapons", "armor"], "label": "Palico equipment"},
    {"route": "/data/npc-missions", "views": [], "label": "Requests"},
    {"route": "/data/switch-actions", "views": [], "label": "Switch Skills"},
    {"route": "/data/kitchen-skills", "views": [], "label": "Dango Skills"},
    {"route": "/data/dangos", "views": [], "label": "Canteen"},
    {"route": "/data/environment-creatures", "views": [], "label": "Endemic Life"},
    {"route": "/data/item-mix", "views": [], "label": "Crafting List"},
]
UNSUPPORTED_REASON = "not_supported: outside the four requested datasets; not parsed by this project"

WEAPON_TYPES = ["Great Sword", "Sword & Shield", "Dual Blades", "Long Sword", "Hammer",
                "Hunting Horn", "Lance", "Gunlance", "Switch Axe", "Charge Blade",
                "Insect Glaive", "Bow", "Heavy Bowgun", "Light Bowgun"]
MELEE_TYPES = set(WEAPON_TYPES[:11])
BOWGUN_TYPES = {"Heavy Bowgun", "Light Bowgun"}

ELEMENTS = {1: ("Fire", "element"), 2: ("Water", "element"), 3: ("Thunder", "element"),
            4: ("Ice", "element"), 5: ("Dragon", "element"), 6: ("Poison", "status"),
            7: ("Sleep", "status"), 8: ("Paralysis", "status"), 9: ("Blast", "status")}
RESISTANCES = {1: "fire", 2: "water", 3: "thunder", 4: "ice", 5: "dragon"}

AVAILABILITY = {
    "direct": "Value read directly from a Kiranico page.",
    "derived": "Value converted from Kiranico codes, icons, or graphics by a documented rule.",
    "supplemental": "Value from an approved secondary source (Game8 Rapid Fire, user lookup table).",
    "unavailable_from_kiranico": "Kiranico does not publish this field; no value is inferred.",
    "not_applicable": "Field does not apply to this record type.",
    "not_fetched": "Kiranico publishes this on detail pages, which this snapshot mode did not fetch.",
    "supplemental_missing": "Approved secondary source was not run or did not match this record.",
    "missing": "Expected source value could not be parsed; see the record warnings.",
}
SOURCES = {
    "kiranico_listing": "Kiranico English listing row",
    "kiranico_detail": "Kiranico English detail page",
    "game8": "Game8 Light Bowgun family page (approved for Rapid Fire only)",
    "user_provided_v16_table": "User-provided v16.0.0 recoil/reload lookup table",
}


def field(value, availability, source=None, **extra):
    if availability not in AVAILABILITY:
        raise ValueError(f"Unknown availability: {availability}")
    result = {"value": value, "availability": availability}
    if source:
        result["source"] = source
    result.update({key: item for key, item in extra.items() if item is not None})
    return result


def slot_notation(levels, width=3):
    padded = sorted(levels, reverse=True) + [0] * max(0, width - len(levels))
    return "-".join(str(level) for level in padded)


def _req(name, requirement, value_type, availability, applies_to="all"):
    return {"field": name, "requirement": requirement, "type": value_type,
            "allowed_availability": availability, "applies_to": applies_to}


_LISTED = ["direct", "missing"]
REQUIRED_FIELDS = {
    "Weapons": [
        _req("name", "武器名稱", "string", _LISTED),
        _req("weapon_type", "武器種類", "string", _LISTED),
        _req("rarity", "稀有度", "integer", _LISTED),
        _req("attack", "基礎攻擊力", "integer", _LISTED),
        _req("affinity", "會心率 (%)", "integer", ["direct", "derived", "missing"]),
        _req("element", "屬性/異常數值", "list[{type,kind,code,value}]", ["derived", "missing"]),
        _req("decoration_slots", "一般孔位", "list[int]", ["derived", "missing"]),
        _req("slot_notation", "一般孔位表示法 (如 3-1-0)", "string", ["derived", "missing"]),
        _req("rampage_slot_level", "百龍孔等級", "integer", ["derived", "missing"]),
        _req("sharpness", "各色斬味長度 (白/紫)", "object", ["derived", "missing", "not_applicable"],
             "melee"),
        _req("ammo", "可裝填彈種清單與裝填數", "list[{code,name,level,capacity}]",
             ["derived", "missing", "not_applicable"], "bowgun"),
        _req("recoil", "反動基礎值", "string", ["direct", "missing", "not_applicable"], "bowgun"),
        _req("reload", "裝填速度基礎值", "string", ["direct", "missing", "not_applicable"], "bowgun"),
        _req("deviation", "偏移基礎值", "string", ["direct", "missing", "not_applicable"], "bowgun"),
        _req("rapid_fire_ammo", "速射彈種", "list[string]",
             ["supplemental", "supplemental_missing", "not_applicable"], "bowgun"),
    ],
    "Armors": [
        _req("name", "防具名稱", "string", _LISTED),
        _req("gender_names", "男女異名對照", "object", ["unavailable_from_kiranico"]),
        _req("part", "部位", "string", ["unavailable_from_kiranico"]),
        _req("rarity", "稀有度", "integer", _LISTED),
        _req("defense_base", "基礎防禦力", "integer", _LISTED),
        _req("defense_max", "最大防禦力", "integer", ["unavailable_from_kiranico"]),
        _req("resistances", "五屬性耐性", "object", ["derived", "missing"]),
        _req("skills", "內建技能與等級", "list[{name,id,level}]", _LISTED),
        _req("decoration_slots", "預設孔位", "list[int]", ["derived", "missing"]),
        _req("slot_notation", "預設孔位表示法 (如 4-1-1)", "string", ["derived", "missing"]),
    ],
    "Decorations": [
        _req("name", "珠子名稱", "string", _LISTED),
        _req("slot_size", "所需孔位大小 (1-4)", "integer", ["derived", "missing"]),
        _req("skills", "提供技能與等級", "list[{name,id,level}]", _LISTED),
        _req("unlock", "解鎖條件", "string", ["unavailable_from_kiranico"]),
    ],
    "RampageDecorations": [
        _req("name", "百龍珠名稱", "string", _LISTED),
        _req("slot_size", "所需百龍孔大小 (1-3)", "integer", ["derived", "missing"]),
        _req("rampage_skill", "提供百龍技能", "object", _LISTED),
        _req("unlock", "解鎖條件", "string", ["unavailable_from_kiranico"]),
    ],
    "Skills": [
        _req("name", "技能名稱", "string", _LISTED),
        _req("max_level", "最大等級", "integer", ["derived", "not_fetched", "missing"]),
        _req("levels", "每級效果與數值", "list[{level,effect,components}]",
             ["direct", "unavailable_from_kiranico", "missing"]),
    ],
    "Rules": [
        _req("recoil_reload_ladder", "反動/裝填等級對照表", "object", ["supplemental"]),
    ],
}


def required_fields_document():
    return {"schema_version": 1, "availability": AVAILABILITY, "sources": SOURCES,
            "datasets": REQUIRED_FIELDS}


def applies(requirement, weapon_type):
    scope = requirement["applies_to"]
    if scope == "all":
        return True
    if scope == "melee":
        return weapon_type in MELEE_TYPES
    if scope == "bowgun":
        return weapon_type in BOWGUN_TYPES
    return False


def field_statistics(category, records):
    """Count availability per required field for one structured category."""
    stats = {}
    for requirement in REQUIRED_FIELDS.get(category, []):
        counts = {}
        for record in records:
            fields = record.get("fields") or {}
            weapon_type = (fields.get("weapon_type") or {}).get("value")
            if category == "Weapons" and not applies(requirement, weapon_type):
                continue
            value = fields.get(requirement["field"])
            availability = value.get("availability") if isinstance(value, dict) else "absent"
            counts[availability] = counts.get(availability, 0) + 1
        allowed = set(requirement["allowed_availability"])
        stats[requirement["field"]] = {
            "requirement": requirement["requirement"], "counts": counts,
            "unexpected": sum(count for key, count in counts.items() if key not in allowed),
        }
    return stats
