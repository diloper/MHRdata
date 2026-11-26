import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from kiranico_schema import CATEGORIES as REGISTRY, REQUIRED_FIELDS


SOURCES = ("Items", "Skills", "Decorations", "Armors", "Weapons")
OPTIONAL_SOURCES = tuple(entry["name"] for entry in REGISTRY.values() if not entry["required"])
RULES_FILE = "bowgun-rules.json"
REQUIRED_FIELDS_FILE = "required-fields.json"
RELATIONS = {
    "skills": ("Skills", "skill"),
    "rampage_skills": ("RampageSkills", "skill"),
    "forging_materials": ("Items", "item"),
    "upgrade_materials": ("Items", "item"),
    "materials": ("Items", "item"),
}
LABELS = {
    "atk": "Attack", "criticalRate": "Affinity", "defBonus": "Defense bonus",
    "defVal": "Defense", "fireRegVal": "Fire resistance",
    "waterRegVal": "Water resistance", "iceRegVal": "Ice resistance",
    "thunderRegVal": "Thunder resistance", "dragonRegVal": "Dragon resistance",
    "part": "Armor part", "rank": "Rank code", "weapon_type": "Weapon type code",
    "unlockItem": "Unlock item", "unlockMonster": "Unlock monster",
    "unlockProgress": "Unlock progress", "buyPrice": "Buy price",
    "sellPrice": "Sell price", "plMaxCount": "Player carry limit",
    "source_url": "Source URL", "source_version": "Source database version",
    "fetched_at": "Fetched at (UTC)", "snapshot_id": "Snapshot ID",
    "source_group": "Source listing group (not a decoded rank)",
    "source_groups": "Source listing pages", "detail_status": "Detail page",
    "source_record_key": "Source record key", "language": "Language",
}
FIELD_LABELS = {
    "weapon_type": "Weapon type", "rarity": "Rarity", "attack": "Attack", "affinity": "Affinity (%)",
    "defense_bonus": "Defense bonus", "element": "Element/status", "decoration_slots": "Decoration slots",
    "slot_notation": "Slot notation", "rampage_decoration_slots": "Rampage decoration slots",
    "rampage_slot_level": "Rampage slot level", "sharpness": "Sharpness", "deviation": "Deviation",
    "recoil": "Recoil (weapon base label)", "reload": "Reload (weapon base label)",
    "ammo": "Loadable ammo (capacity)", "recoil_lookup": "Recoil Down needed (v16 table)",
    "reload_lookup": "Reload Speed needed (v16 table)", "rapid_fire_ammo": "Rapid Fire ammo",
    "ammo_handling": "Per-ammo recoil/reload (Game8)", "bow_arc_shot": "Arc shot",
    "bow_charge_levels": "Charge levels (default)", "bow_gray_charge_levels": "Charge levels shown gray (locked by default)",
    "bow_coatings": "Coatings (usable)", "hunting_horn_songs": "Songs",
    "shelling": "Shelling", "phial": "Phial", "kinsect_level": "Kinsect level",
    "rampage_skills": "Rampage skills", "gender_names": "Male/female names", "part": "Armor part",
    "defense_base": "Base defense", "defense_max": "Max defense", "resistances": "Resistances",
    "skills": "Skills", "kind": "Kind", "slot_size": "Slot size", "effect": "Effect",
    "unlock": "Unlock condition", "rampage_skill": "Rampage skill", "description": "Description",
    "max_level": "Max level", "levels": "Level effects", "item_group": "Item group", "name": "Name",
}
HIDDEN_FIELDS = {"name", "image", "images"}
SNAPSHOT_KEYS = {"language", "source_version", "snapshot_id", "source_groups", "source_group",
                 "detail_status", "fetched_at"}
STATUS_TEXT = {
    "unavailable_from_kiranico": "Not published by Kiranico",
    "not_fetched": "Not fetched in this snapshot mode (on Kiranico detail pages)",
    "supplemental_missing": "Supplement not available (run game8_lbg.py)",
    "missing": "Missing: could not be parsed (see source warnings)",
}
SHARPNESS_ORDER = ("red", "orange", "yellow", "green", "blue", "white", "purple")


def text(value):
    if value is None or value == "":
        return "Not provided by source"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return re.sub(r"\s+", " ", str(value)).strip().replace("<", "&lt;").replace(
        ">", "&gt;"
    ).replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace(
        "[", "\\["
    ).replace("]", "\\]").replace("`", "\\`")


def load_sources(source_dir):
    documents = {}
    report = {"sources": {}, "warnings": []}
    manifest_path = source_dir.parent / "download-manifest.json"
    if source_dir.name == "structured" and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("run_status") != "success":
            raise ValueError("Download snapshot did not finish successfully; resume it before converting")
        if manifest.get("mode") in {"full", "listings"} and not manifest.get("complete"):
            raise ValueError(f"{manifest['mode'].title()} download snapshot has incomplete coverage; "
                             "resume it before converting")
    categories = list(SOURCES) + [category for category in OPTIONAL_SOURCES
                                  if (source_dir / (category + ".json")).exists()]
    for category in categories:
        source = source_dir / (category + ".json")
        raw = source.read_bytes()
        document = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(document.get("data"), list):
            raise ValueError(f"{source.name}: data must be an array")
        unique = []
        seen = set()
        ids = Counter()
        for record in document["data"]:
            if not isinstance(record, dict) or not record.get("name") or not record.get("id"):
                raise ValueError(f"{source.name}: every record needs name and id")
            signature = json.dumps(record, sort_keys=True, ensure_ascii=False)
            if signature in seen:
                continue
            seen.add(signature)
            unique.append(record)
            ids[str(record["id"])] += 1
        report["sources"][category] = {
            "input_records": len(document["data"]), "output_records": len(unique),
            "exact_duplicates_removed": len(document["data"]) - len(unique),
            "conflicting_ids": sorted(key for key, count in ids.items() if count > 1),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        if "field_statistics" in document:
            report["sources"][category]["required_fields"] = {
                name: values["counts"] for name, values in document["field_statistics"].items()}
        document["data"] = unique
        documents[category] = document
    return documents, report


def load_rules(source_dir):
    rules = {}
    for key, filename in (("bowgun", RULES_FILE), ("required_fields", REQUIRED_FIELDS_FILE)):
        path = source_dir / filename
        if path.exists():
            rules[key] = json.loads(path.read_text(encoding="utf-8"))
    return rules


def levels(record):
    return sorted((key for key in record if re.fullmatch(r"lv\d+", key)),
                  key=lambda key: int(key[2:]))


def typed(record, key):
    return ((record.get("fields") or {}).get(key) or {})


def skill_effects(record):
    """Per-level effects of a Skills record: typed levels first, then detail ``lvN`` keys."""
    entry = typed(record, "levels")
    if entry.get("availability") == "direct" and entry.get("value"):
        return [(level["level"], level["effect"]) for level in entry["value"]]
    return [(int(key[2:]), record[key]) for key in levels(record)]


def resolve(indexes, category, reference):
    matches = indexes.get(category, {}).get(str(reference.get("id", "")), [])
    named = [candidate for candidate in matches if candidate["name"] == reference.get("name")]
    return named[0] if len(named) == 1 else (matches[0] if len(matches) == 1 else None)


def lookup_text(lookup):
    if lookup.get("status") != "mapped":
        return f"{lookup.get('source_label')}: not in the v16 table ({lookup.get('note', 'unmapped')})"
    if lookup.get("required_level") == 0:
        return f"{lookup['source_label']}: already {lookup['target_tier']}; no {lookup['skill']} needed"
    if lookup.get("required_level") is None:
        return (f"{lookup['source_label']}: {lookup['target_tier']} is not reachable; best with "
                f"{lookup['skill']} Lv3 is {lookup['best_tier_at_lv3']}")
    return f"{lookup['source_label']}: {lookup['skill']} Lv{lookup['required_level']} reaches {lookup['target_tier']}"


def sharpness_text(bar):
    return ", ".join(f"{color} {bar.get(color, 0)}" for color in SHARPNESS_ORDER
                     if bar.get(color) or color in {"white", "purple"})


def format_value(key, value, indexes):
    """Return ``(inline text, sub-lines)`` for one typed field value."""
    if key == "element":
        return ", ".join(f"{entry['type']} {entry['value']}" for entry in value) or "None", []
    if key in {"decoration_slots", "rampage_decoration_slots"}:
        return ", ".join(f"Lv{level}" for level in value) or "None", []
    if key == "resistances":
        return ", ".join(f"{name.title()} {amount:+d}" for name, amount in value.items()), []
    if key == "sharpness":
        return (f"base {sharpness_text(value['base'])} (total {value['base_total']} {value['unit']}); "
                f"with Handicraft Lv5 {sharpness_text(value['max'])} (total {value['max_total']} {value['unit']})"), []
    if key == "ammo":
        return "; ".join(f"{ammo['name']} x{ammo['capacity']}" for ammo in value) or "None", []
    if key in {"recoil_lookup", "reload_lookup"}:
        return lookup_text(value), []
    if key == "ammo_handling":
        lines = []
        for ammo in value:
            parts = [f"{ammo['name']}: capacity {ammo['capacity']}, recoil {ammo['recoil']}, reload {ammo['reload']}",
                     "Rapid Fire" if ammo.get("rapid_fire") else None,
                     "shot types " + "/".join(ammo["shot_types"]) if ammo.get("shot_types") else None]
            parts += [lookup_text(ammo[name]) for name in ("recoil_lookup", "reload_lookup") if ammo.get(name)]
            lines.append("; ".join(part for part in parts if part))
        return f"{len(value)} ammo types", lines
    if key == "skills":
        lines = []
        for skill in value:
            line = f"{skill['name']} Lv{skill['level']}" if skill.get("level") else skill["name"]
            resolved = resolve(indexes, "Skills", skill)
            effect = dict(skill_effects(resolved)).get(skill.get("level")) if resolved else None
            lines.append(line + (f": {effect}" if effect else ""))
        return ", ".join(line.split(":")[0] for line in lines) or "None", lines
    if key in {"rampage_skills", "rampage_skill"}:
        entries = value if isinstance(value, list) else [value]
        lines = []
        for skill in entries:
            resolved = resolve(indexes, "RampageSkills", skill)
            effect = typed(resolved, "effect").get("value") if resolved else None
            lines.append(f"{skill['name']} (ID {skill['id']})" + (f": {effect}" if effect else ""))
        return ", ".join(skill["name"] for skill in entries) or "None", lines if len(entries) > 1 or any(
            ": " in line for line in lines) else []
    if key == "shelling":
        return f"{value['type']} Lv{value['level']}", []
    if key == "phial":
        return value["type"] + (f" {value['value']}" if value.get("value") is not None else ""), []
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return ", ".join(value) or "None", []
    if value is None:
        return "None", []
    return value, []


def render_fields(record, indexes):
    output = []
    for key, entry in record["fields"].items():
        if key in HIDDEN_FIELDS or (key in RELATIONS and key in record) or not isinstance(entry, dict):
            continue
        availability = entry.get("availability")
        if availability == "not_applicable":
            continue
        if key == "levels" and availability == "direct":
            continue
        label = FIELD_LABELS.get(key, key.replace("_", " ").capitalize())
        if availability in STATUS_TEXT:
            output.append(f"- {label}: {text(STATUS_TEXT[availability])}")
            continue
        if key == "levels":
            output.append(f"- {label}: {text(len(entry['value']))} levels listed without effect text")
            continue
        inline, lines = format_value(key, entry["value"], indexes)
        marker = {"derived": " (derived)", "supplemental": f" (supplemental: {entry.get('source')})"}.get(
            availability, "")
        output.append(f"- {label}: {text(inline)}{marker}")
        output.extend(f"  - {text(line)}" for line in lines)
    return output


def render_record(category, record, indexes, warnings):
    name = text(record["name"])
    output = [f"## {name}", "", f"- Category: {category}",
              f"- ID: {text(record['id'])}"]
    if isinstance(record.get("fields"), dict):
        output.extend(render_fields(record, indexes))
    hidden = {"name", "id", "tables", "fields", "warnings"} | (
        SNAPSHOT_KEYS if isinstance(record.get("fields"), dict) else set())
    for key, value in record.items():
        if key in hidden or key in RELATIONS or key in levels(record):
            continue
        if category == "Armors" and key in {"decoration_slot_levels", "decoration_slot_note"}:
            continue
        label = LABELS.get(key, key)
        output.append(f"- {label}: {text(value)}")
    if record.get("warnings"):
        output.append("- Source warnings:")
        output.extend(f"  - {text(warning)}" for warning in record["warnings"])
    if category == "Armors" and ("decoration_slot_levels" in record or "fields" not in record):
        slots = record.get("decoration_slot_levels")
        if slots is None:
            slot_text = count_text = "Unknown"
            warning = {"category": category, "id": record["id"], "field": "decoration_slot_levels",
                       "reason": record.get("decoration_slot_note", "Slot levels not provided; reparse the source HTML.")}
            if record.get("source_url"):
                warning["source_url"] = record["source_url"]
            warnings.append(warning)
        else:
            if not isinstance(slots, list) or any(type(level) is not int or not 1 <= level <= 4 for level in slots):
                raise ValueError(f"Armors/{record['id']}/decoration_slot_levels: expected a list of levels 1 to 4")
            slot_text = ", ".join(f"Lv{level}" for level in slots) or "None"
            count_text = str(len(slots))
        output.extend([f"- Decoration slot levels: {slot_text}", f"- Total decoration slots: {count_text}"])
    if (category == "Armors" and "fields" not in record
            and not any("equipped_level" in entry for entry in record.get("skills", []))):
        output.append("- Equipped skill levels: not explicitly provided; do not infer from skillList.")
    effects = skill_effects(record) if category == "Skills" else []
    if effects:
        output.extend(["", f"### {name}: Skill Level Effects", ""])
        output.extend(f"- Level {level}: {text(effect)}" for level, effect in effects)
    for field, (target_category, nested_key) in RELATIONS.items():
        if field not in record:
            continue
        output.extend(["", f"### {name}: {field.replace('_', ' ').title()}", ""])
        entries = record[field]
        if not isinstance(entries, list):
            raise ValueError(f"{category}/{record['id']}/{field}: expected an array")
        if not entries:
            output.append("- Not provided by source (empty list); this does not prove absence.")
        for entry in entries:
            reference = entry.get(nested_key, {})
            resolved = resolve(indexes, target_category, reference)
            if target_category in indexes and (resolved is None or resolved["name"] != reference.get("name")):
                warnings.append({"category": category, "id": record["id"],
                                 "field": field, "reference": reference})
            output.append(f"- {text(reference.get('name'))} (ID: {text(reference.get('id'))})")
            for key, value in reference.items():
                if key not in {"name", "id"}:
                    output.append(f"  - {key}: {text(value)}")
            for key, value in entry.items():
                if key != nested_key:
                    output.append(f"  - {key}: {text(value)}")
            if target_category == "Skills" and resolved is not None:
                output.append(f"  - Skill description: {text(resolved.get('description') or typed(resolved, 'description').get('value'))}")
                output.extend(f"  - {text(resolved['name'])}, level {level}: {text(effect)}"
                              for level, effect in skill_effects(resolved))
            if target_category == "RampageSkills" and resolved is not None:
                output.append(f"  - Rampage skill effect: {text(typed(resolved, 'effect').get('value'))}")
    for table in record.get("tables", []):
        if table.get("nested"):
            continue
        output.extend(["", f"### {name}: {text(table['section'])}", ""])
        if not table["rows"]:
            output.append("- Source table is empty; this does not prove absence.")
        for number, cells in enumerate(table["rows"], 1):
            output.append(f"- Row {number}:")
            for position, cell in enumerate(cells):
                label = (table["headers"][position] if position < len(table["headers"])
                         and table["headers"][position] else f"Column {position + 1} (unlabelled by source)")
                output.append(f"  - {text(label)}: {text(cell['text'])}")
                for annotation in cell.get("annotations", []):
                    output.append(f"    - Source icon/label: {text(annotation)}")
                for link in cell["links"]:
                    output.append(f"    - {text(link['name'])}: {text(link['url'])}")
    return "\n".join(output) + "\n\n"


def field_rules(records):
    rules = defaultdict(set)
    for record in records:
        for key, entry in (record.get("fields") or {}).items():
            if not isinstance(entry, dict) or key in HIDDEN_FIELDS:
                continue
            for note in (entry.get("rule"), entry.get("note")):
                if note and entry.get("availability") != "not_applicable":
                    rules[key].add(note)
    return {key: sorted(values) for key, values in sorted(rules.items())}


def typed_header(category, group, document, records):
    coverage = document.get("coverage") or {}
    lines = [f"# Monster Hunter Rise: {category} ({group})", "",
             f"- Source: Kiranico English database (mhrise.kiranico.com), file {category}.json",
             f"- Source version: {text(document.get('version'))}",
             f"- Snapshot mode: {text(document.get('snapshot_mode'))}; snapshot ID/date: {text(document.get('date'))}",
             "- Language: English (en). Each record lists its Kiranico source URL.",
             f"- Coverage: {coverage.get('parsed')} records parsed; {coverage.get('discovered')} discovered; "
             f"{len(coverage.get('missing', []))} missing; {len(coverage.get('row_errors', []))} row errors.",
             "", "How to read the typed fields:",
             "- Plain values are read directly from Kiranico.",
             "- (derived) values are converted from Kiranico codes, icons or graphics by the rules below.",
             "- (supplemental: source) values come from an approved secondary source.",
             "- 'Not published by Kiranico' means the field does not exist on Kiranico; nothing was inferred.",
             "- Fields that do not apply to a record (for example sharpness on bowguns) are omitted.",
             "- Rampage skills use a separate ID namespace from normal skills."]
    requirements = REQUIRED_FIELDS.get(category, [])
    if requirements:
        lines += ["", "Required fields (中文需求 = English label):"]
        lines += [f"- {text(item['requirement'])} = {text(FIELD_LABELS.get(item['field'], item['field']))}"
                  for item in requirements]
    rules = field_rules(records)
    if rules:
        lines += ["", "Field rules and notes:"]
        for key, notes in rules.items():
            lines += [f"- {text(FIELD_LABELS.get(key, key))}: {text(note)}" for note in notes[:5]]
    return "\n".join(lines) + "\n\n"


def header(category, group, document, records=()):
    if document.get("schema_version", 1) >= 2:
        return typed_header(category, group, document, records)
    coverage = document.get("coverage")
    provenance = ""
    slot_note = ("For armor decoration slots, None means confirmed zero; Unknown means not determined.\n"
                 if category == "Armors" else "")
    if coverage is not None:
        provenance = (f"- Snapshot mode: {text(document.get('snapshot_mode'))}\n"
                      f"- Coverage: {coverage['parsed']} parsed records; "
                      f"{coverage['discovered']} discovered; {len(coverage['missing'])} missing.\n"
                      "- Source date below is the download snapshot date, not the game release date.\n")
    return (f"# Monster Hunter Rise: {category} ({group})\n\n"
            f"- Source file: {category}.json\n"
            f"- Source version: {text(document.get('version'))}\n"
            f"{provenance}"
            f"- Source date: {text(document.get('date'))}\n\n"
            "Historical source snapshot, not verified as current or complete Sunbreak data.\n"
            "English names and original IDs are preserved. Numeric enum codes, rarity,\n"
            "skillList and decorationsNumList retain their original meaning without decoding.\n"
            "Empty values mean not provided, not necessarily absent. Unlock value 'None'\n"
            "is a literal source value, not a verified absence of requirements.\n"
            f"{slot_note}"
            "All listed skill level effects are reference information, not equipped levels.\n"
            "Rampage skills use a separate ID namespace from normal skills.\n\n")


def group_name(category, record):
    weapon_type = typed(record, "weapon_type").get("value")
    rarity = typed(record, "rarity").get("value")
    item_group = typed(record, "item_group").get("value")
    if category == "Weapons" and weapon_type:
        group = weapon_type
    elif category == "Armors" and rarity is not None:
        group = f"rarity-{rarity}"
    elif category == "Items" and item_group:
        group = item_group
    elif category in {"Armors", "Weapons"} and record.get("source_group"):
        group = record["source_group"]
    elif category == "Armors":
        group = "rank-" + str(record.get("rank", "unknown"))
    elif category == "Weapons":
        group = "type-" + str(record.get("weapon_type", "unknown"))
    else:
        group = "all"
    return re.sub(r"-+", "-", re.sub(r"[^a-zA-Z0-9-]", "-", group)).strip("-") or "all"


def rules_document(rules):
    lines = ["# Monster Hunter Rise: Rules (bowgun thresholds and data contract)", ""]
    bowgun = rules.get("bowgun")
    if bowgun:
        lines += [f"- Source: {text(bowgun['rule_source'])} (game version {text(bowgun['rule_version'])})",
                  f"- {text(bowgun['description'])}", f"- Label policy: {text(bowgun['label_policy'])}", ""]
        for rule in bowgun["rules"]:
            lines += [f"## {text(rule['skill'])} ({text(rule['kind'])}) to reach {text(rule['target_tier'])}", ""]
            for tier in rule["tiers"]:
                needed = ("not reachable; best with Lv3 is " + tier["best_tier_at_lv3"]
                          if not tier["achievable"] else
                          "already at the target; no skill needed" if tier["required_level"] == 0
                          else f"{rule['skill']} Lv{tier['required_level']}")
                lines.append(f"- Base {text(tier['tier'])}: {text(needed)}")
            if rule["aliases"]:
                lines.append("- Label aliases: " + text(", ".join(f"{alias} = {target}"
                                                                    for alias, target in rule["aliases"].items())))
            lines.append("")
    contract = rules.get("required_fields")
    if contract:
        lines += ["## Availability codes", ""]
        lines += [f"- {text(key)}: {text(value)}" for key, value in contract["availability"].items()]
        lines += ["", "## Sources", ""]
        lines += [f"- {text(key)}: {text(value)}" for key, value in contract["sources"].items()]
        for dataset, requirements in contract["datasets"].items():
            lines += ["", f"## Required fields: {text(dataset)}", ""]
            lines += [f"- {text(item['requirement'])} ({text(item['field'])}, {text(item['type'])}): allowed "
                      f"{text(', '.join(item['allowed_availability']))}; applies to {text(item['applies_to'])}"
                      for item in requirements]
    return "\n".join(lines).rstrip() + "\n"


def build_documents(documents, report, max_chars, rules=None):
    indexes = {}
    for category in ("Items", "Skills", "RampageSkills"):
        if category not in documents:
            continue
        indexes[category] = defaultdict(list)
        for record in documents[category]["data"]:
            indexes[category][str(record["id"])].append(record)
    files = {}
    report["nested_tables_skipped"] = 0
    for category, document in documents.items():
        groups = defaultdict(list)
        for record in document["data"]:
            groups[group_name(category, record)].append(record)
            report["nested_tables_skipped"] += sum(1 for table in record.get("tables", []) if table.get("nested"))
        for group, records in groups.items():
            intro = header(category, group, document, records)
            content = intro
            part = 1
            for record in records:
                block = render_record(category, record, indexes, report["warnings"])
                if len(intro) + len(block) > max_chars:
                    raise ValueError(f"{category}/{record['id']} exceeds max-chars; increase the limit")
                if len(content) + len(block) > max_chars:
                    files[f"{category}-{group}-{part:03d}.md"] = content
                    part += 1
                    content = intro
                content += block
            files[f"{category}-{group}-{part:03d}.md"] = content
    if rules:
        content = rules_document(rules)
        if len(content) > max_chars:
            raise ValueError("Rules document exceeds max-chars; increase the limit")
        files["Rules-bowgun-001.md"] = content
    report["generated_files"] = sorted(files)
    return files


MANAGED_FILE = re.compile(r"(?:" + "|".join(SOURCES + OPTIONAL_SOURCES + ("Rules",))
                          + r")-[a-zA-Z0-9-]+-\d+\.md")


def convert(source_dir, output_dir, max_chars=180000):
    source_dir = Path(source_dir).resolve()
    output_dir = Path(output_dir).resolve()
    if source_dir == output_dir:
        raise ValueError("Output must be a separate directory")
    if max_chars < 2000:
        raise ValueError("max-chars must be at least 2000")
    documents, report = load_sources(source_dir)
    files = build_documents(documents, report, max_chars, load_rules(source_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "conversion-report.json"
    previous = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    managed = set(previous.get("generated_files", []))
    for filename in files:
        if (output_dir / filename).exists() and filename not in managed:
            raise ValueError(f"Refusing to overwrite unmanaged file: {filename}")
    for filename, content in files.items():
        (output_dir / filename).write_text(content, encoding="utf-8")
    for filename in managed - set(files):
        if MANAGED_FILE.fullmatch(filename):
            (output_dir / filename).unlink(missing_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report

def main():
    parser = argparse.ArgumentParser(description="Convert MHRise JSON snapshots to retrieval-friendly Markdown.")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-chars", type=int, default=180000)
    args = parser.parse_args()
    report = convert(args.source, args.output or args.source / "notebooklm", args.max_chars)
    print(f"Created {len(report['generated_files'])} Markdown files.")
    for category, counts in report["sources"].items():
        print(f"{category}: {counts['input_records']} input, {counts['output_records']} output, "
              f"{counts['exact_duplicates_removed']} duplicates removed")
    print(f"Conversion warnings: {len(report['warnings'])}; see conversion-report.json.")


if __name__ == "__main__":
    main()