import argparse
import json
import re
from pathlib import Path

import bowgun_rules


WEAPON_TYPE = "Light Bowgun"
WEAPON_VIEW = "view-13"
FOCUS_SKILLS = {
    "Slugger", "Artillery", "Ammo Up", "Recoil Down", "Reload Speed", "Spare Shot",
}
STICKY_AMMO = re.compile(r"Sticky Ammo ([123])")
ANALYSIS_MODES = {"full", "listings"}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def records_for(snapshot, category):
    document = read_json(snapshot / "structured" / (category + ".json"))
    records = document.get("data")
    if not isinstance(records, list):
        raise ValueError(f"{category}.json must contain a data array")
    return records


def typed(record, key):
    """Return the typed field entry, or ``{}`` for legacy records and absent fields."""
    return (record.get("fields") or {}).get(key) or {}


def typed_value(record, key, default=None):
    entry = typed(record, key)
    if entry.get("availability") in {"direct", "derived", "supplemental"}:
        return entry.get("value")
    return default


def is_light_bowgun(record):
    weapon_type = typed(record, "weapon_type").get("value")
    if weapon_type is not None:
        return weapon_type == WEAPON_TYPE
    return record.get("source_group") == WEAPON_VIEW


def sticky_ammo(record):
    capacities = {"1": 0, "2": 0, "3": 0}
    ammo = typed_value(record, "ammo")
    if ammo is not None:
        for entry in ammo:
            match = STICKY_AMMO.fullmatch(entry.get("name", ""))
            if match:
                capacities[match[1]] = max(capacities[match[1]], entry.get("capacity") or 0)
        return capacities
    for table in record.get("tables", []):
        for row in table.get("rows", []):
            if not row or row[0].get("text", "").strip().casefold() != "sti":
                continue
            for level, cell in zip(capacities, row[1:4]):
                try:
                    capacities[level] = max(capacities[level], int(cell["text"].strip()))
                except (KeyError, TypeError, ValueError):
                    continue
    return capacities


def recoil_reload(record):
    recoil, reload = typed_value(record, "recoil"), typed_value(record, "reload")
    if recoil is not None or reload is not None:
        return recoil, reload
    text = " ".join(
        " ".join(cell.get("text", "") for row in table.get("rows", []) for cell in row)
        for table in record.get("tables", []) if table.get("section") == "Overview"
    )
    text = " ".join(text.split())
    match = re.search(
        r"\bRecoil\s+(?P<recoil>.*?)\s+Reload\s+(?P<reload>.*?)(?:\s+(?:Nrm|Prc|Spr)\s|$)",
        text, re.IGNORECASE,
    )
    if not match:
        return None, None
    return match.group("recoil").strip(), match.group("reload").strip()


def skill_entries(record):
    skills = typed_value(record, "skills")
    if skills is not None:
        return [{"name": skill["name"], "id": skill["id"],
                 "source_url": skill.get("link"), "equipped_level": skill.get("level")}
                for skill in skills if skill["name"] in FOCUS_SKILLS]
    return [
        {"name": entry["skill"]["name"], "id": entry["skill"]["id"],
         "source_url": entry["skill"].get("link"),
         "equipped_level": entry.get("equipped_level")}
        for entry in record.get("skills", [])
        if entry.get("skill", {}).get("name") in FOCUS_SKILLS
    ]


def skill_effects(record):
    levels = typed(record, "levels")
    if levels.get("availability") == "direct" and levels.get("value"):
        return {f"lv{level['level']}": level["effect"] for level in levels["value"]}
    return {key: value for key, value in record.items() if re.fullmatch(r"lv\d+", key)}


def field_status(record, key):
    """Value plus availability for report fields that may be unavailable."""
    entry = typed(record, key)
    return {"value": entry.get("value"), "availability": entry.get("availability", "absent")}


def threshold(record, kind, label):
    lookup = typed_value(record, kind + "_lookup")
    if lookup is None and label:
        lookup = bowgun_rules.lookup(kind, label, "weapon_base_label")
    return lookup


def sticky_handling(record):
    """Per-ammo Sticky recoil/reload from the Game8 supplement, when merged."""
    handling = typed_value(record, "ammo_handling") or []
    return [ammo for ammo in handling if STICKY_AMMO.fullmatch(ammo.get("name", ""))]


def weapon_profile(record):
    ammo = sticky_ammo(record)
    highest_level = max((int(level) for level, count in ammo.items() if count), default=0)
    recoil, reload = recoil_reload(record)
    return {
        "id": record["id"], "name": record["name"], "source_url": record["source_url"],
        "sticky_ammo_capacity": ammo, "highest_sticky_level": highest_level,
        "recoil": recoil, "reload": reload,
        "recoil_lookup": threshold(record, "recoil", recoil),
        "reload_lookup": threshold(record, "reload", reload),
        "rarity": typed_value(record, "rarity"), "attack": typed_value(record, "attack"),
        "affinity": typed_value(record, "affinity"), "element": typed_value(record, "element"),
        "decoration_slots": typed_value(record, "decoration_slots"),
        "slot_notation": typed_value(record, "slot_notation"),
        "rampage_slot_level": typed_value(record, "rampage_slot_level"),
        "deviation": typed_value(record, "deviation"),
        "rapid_fire_ammo": field_status(record, "rapid_fire_ammo"),
        "sticky_ammo_handling": sticky_handling(record),
        "rampage_skills": field_status(record, "rampage_skills"),
    }


def armor_profile(record):
    return {"id": record["id"], "name": record["name"], "source_url": record["source_url"],
            "source_group": record.get("source_group"), "relevant_skills": skill_entries(record),
            "rarity": typed_value(record, "rarity"), "defense_base": typed_value(record, "defense_base"),
            "resistances": typed_value(record, "resistances"),
            "decoration_slots": typed_value(record, "decoration_slots",
                                            record.get("decoration_slot_levels")),
            "slot_notation": typed_value(record, "slot_notation"),
            "part": field_status(record, "part")}


def build_report(snapshot):
    snapshot = Path(snapshot).resolve()
    manifest_path = snapshot / "download-manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"Missing download manifest: {manifest_path}")
    manifest = read_json(manifest_path)
    if manifest.get("run_status") != "success":
        raise ValueError("Snapshot has not finished successfully; resume it before analysis")
    if manifest.get("mode") not in ANALYSIS_MODES or not manifest.get("complete"):
        raise ValueError("A complete listings or full snapshot is required for this analysis")

    weapons = records_for(snapshot, "Weapons")
    armors = records_for(snapshot, "Armors")
    decorations = records_for(snapshot, "Decorations")
    skills = records_for(snapshot, "Skills")

    candidates = [
        weapon_profile(record) for record in weapons
        if is_light_bowgun(record) and max(sticky_ammo(record).values(), default=0) > 0
    ]
    candidates.sort(key=lambda weapon: (
        -weapon["highest_sticky_level"],
        -weapon["sticky_ammo_capacity"]["3"],
        -weapon["sticky_ammo_capacity"]["2"],
        -weapon["sticky_ammo_capacity"]["1"],
        weapon["name"].casefold(),
    ))
    for rank, weapon in enumerate(candidates, 1):
        weapon["ammo_screening_rank"] = rank

    armor_candidates = [armor_profile(record) for record in armors if skill_entries(record)]
    decoration_candidates = [
        {"id": record["id"], "name": record["name"], "source_url": record["source_url"],
         "relevant_skills": skill_entries(record), "slot_size": typed_value(record, "slot_size")}
        for record in decorations if skill_entries(record)
    ]
    relevant_skills = [
        {"id": record["id"], "name": record["name"], "source_url": record["source_url"],
         "max_level": typed_value(record, "max_level"), "effects": skill_effects(record)}
        for record in skills if record.get("name") in FOCUS_SKILLS
    ]
    supplements = manifest.get("supplements") or {}
    listings = manifest.get("mode") == "listings"

    return {
        "schema_version": 2,
        "snapshot_id": manifest.get("snapshot_id"),
        "snapshot_mode": manifest.get("mode"),
        "weapon_category": WEAPON_TYPE,
        "weapon_source_group": WEAPON_VIEW,
        "weapon_candidate_count": len(candidates),
        "weapon_ranking_basis": [
            "Highest supported sticky ammo level, then its loaded capacity, then lower levels.",
            "This is an ammunition-capability shortlist, not a validated KO-performance optimum.",
        ],
        "weapons": candidates,
        "armor_candidates": armor_candidates,
        "decoration_candidates": decoration_candidates,
        "relevant_skills": relevant_skills,
        "recoil_reload_rules": bowgun_rules.rules_document(),
        "supplements": supplements,
        "mr10_eligibility": {
            "status": "unverified",
            "reason": "The structured source does not expose a reliable MR unlock threshold for each armor or weapon.",
            "assumption": "No Qurio crafting is assumed; manually verify each candidate is obtainable by MR10.",
        },
        "limitations": [
            "The report does not calculate an optimal five-piece armor, charm, and decoration combination.",
            "Armor slots are listed, but MR unlock thresholds are not established.",
            "The weapon order is based on sticky-ammo capacity, not measured stun per second or recoil-adjusted firing cycles.",
            "Recoil/reload lookups use the weapon-level Kiranico label; per-ammo Sticky recoil/reload "
            "needs the Game8 supplement." if "game8_lbg" not in supplements else
            "Per-ammo Sticky recoil/reload comes from the Game8 supplement.",
        ] + (["Listings mode does not fetch detail pages: materials and weapon rampage skills are "
              "not included; run a full snapshot for them."] if listings else []),
    }


def write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def render_source_tables(record, sections):
    lines = []
    for table in record.get("tables", []):
        if table.get("section") not in sections or table.get("nested"):
            continue
        lines.append(f"Source table: {table.get('section', 'Unlabelled')}")
        headers = table.get("headers", [])
        for row in table.get("rows", []):
            cells = []
            for position, cell in enumerate(row):
                label = (headers[position] if position < len(headers) and headers[position]
                         else f"Column {position + 1}")
                value = cell.get("text", "").strip()
                details = [f"{label}: {value}" if value else f"{label}: not provided"]
                details.extend(f"annotation: {value}" for value in cell.get("annotations", []))
                details.extend(f"link: {link.get('name') or 'source'} ({link['url']})"
                               for link in cell.get("links", []) if link.get("url"))
                cells.append("; ".join(details))
            if cells:
                lines.append("- " + " | ".join(cells))
        if not table.get("rows"):
            lines.append("- No rows provided by source.")
    return lines


def render_materials(record, fields):
    lines = []
    if record.get("detail_status") == "not_fetched" and not any(field in record for field, _ in fields):
        labels = ", ".join(label.lower() for _, label in fields)
        return [f"{labels.capitalize()}: not fetched in listings mode; see the source URL."]
    for field, label in fields:
        if field not in record:
            continue
        lines.append(f"{label}:")
        entries = record[field]
        if not entries:
            lines.append("- No entries provided by source.")
            continue
        for entry in entries:
            reference = entry.get("item", {})
            name = reference.get("name", "Unknown item")
            record_id = reference.get("id", "unknown")
            amount = entry.get("amount")
            amount_text = f" {amount}" if amount else ""
            url = reference.get("link")
            source = f"; source: {url}" if url else ""
            lines.append(f"- {name}{amount_text} (ID: {record_id}){source}")
    return lines


def source_metadata(record):
    return [
        f"Name: {record.get('name', 'Unknown')}",
        f"ID: {record.get('id', 'unknown')}",
        f"Database version: {record.get('source_version') or 'not provided'}",
        f"Source URL: {record.get('source_url') or 'not provided'}",
    ]


def lookup_line(label, lookup):
    if not lookup:
        return f"{label}: no label to look up"
    if lookup["status"] != "mapped":
        return f"{label}: '{lookup['source_label']}' is not in the v16 table; no level inferred"
    if lookup["required_level"] == 0:
        return f"{label}: already {lookup['target_tier']} (no {lookup['skill']} needed)"
    if lookup["required_level"] is None:
        return (f"{label}: {lookup['target_tier']} not reachable; {lookup['skill']} Lv3 gives "
                f"{lookup['best_tier_at_lv3']}")
    return f"{label}: {lookup['skill']} Lv{lookup['required_level']} for {lookup['target_tier']}"


def optional(value, formatter=str):
    return "not provided" if value is None else formatter(value)


def weapon_stat_lines(candidate):
    rapid = candidate["rapid_fire_ammo"]
    rapid_text = (", ".join(rapid["value"]) or "none" if rapid["availability"] == "supplemental"
                  else "not available (Kiranico has no Rapid Fire data; run game8_lbg.py)")
    lines = [
        f"Rarity: {optional(candidate['rarity'])}",
        f"Attack: {optional(candidate['attack'])}; affinity: {optional(candidate['affinity'], lambda v: f'{v}%')}",
        "Element: " + optional(candidate["element"], lambda value: ", ".join(
            f"{entry['type']} {entry['value']}" for entry in value) or "none"),
        f"Decoration slots: {optional(candidate['slot_notation'])}; "
        f"rampage slot level: {optional(candidate['rampage_slot_level'])}",
        f"Deviation: {optional(candidate['deviation'])}",
        lookup_line("Recoil threshold (v16 table, weapon label)", candidate["recoil_lookup"]),
        lookup_line("Reload threshold (v16 table, weapon label)", candidate["reload_lookup"]),
        f"Rapid Fire ammo (Game8): {rapid_text}",
    ]
    for ammo in candidate["sticky_ammo_handling"]:
        lines.append(f"{ammo['name']} (Game8): capacity {ammo['capacity']}, recoil {ammo['recoil']}, "
                     f"reload {ammo['reload']}")
        lines.append("  " + lookup_line("Recoil threshold (per ammo)", ammo.get("recoil_lookup")))
        lines.append("  " + lookup_line("Reload threshold (per ammo)", ammo.get("reload_lookup")))
    return lines


def build_text_documents(snapshot, report):
    snapshot = Path(snapshot).resolve()
    weapons = records_for(snapshot, "Weapons")
    armors = records_for(snapshot, "Armors")
    decorations = records_for(snapshot, "Decorations")
    skills = records_for(snapshot, "Skills")
    items = records_for(snapshot, "Items")
    weapon_by_url = {record.get("source_url"): record for record in weapons}
    armor_by_url = {record.get("source_url"): record for record in armors}
    decoration_by_url = {record.get("source_url"): record for record in decorations}

    weapon_lines = [
        "Sticky Light Bowgun Candidates",
        f"Snapshot ID: {report.get('snapshot_id')}",
        f"Snapshot mode: {report.get('snapshot_mode')}; complete coverage required.",
        "MR10 eligibility: unverified. Confirm unlock availability in-game.",
        "Ranking is an ammo-capacity shortlist, not a measured KO-performance optimum.",
        "Recoil/reload thresholds use the user-provided v16.0.0 table; labels outside it stay unmapped.",
        "Source table columns are kept in source order; Column N means the source did not label that column.",
        "",
    ]
    for candidate in report["weapons"]:
        record = weapon_by_url[candidate["source_url"]]
        weapon_lines.extend([f"## Candidate {candidate['ammo_screening_rank']}: {candidate['name']}"])
        weapon_lines.extend(source_metadata(record))
        weapon_lines.extend([
            f"Sticky ammo capacity: Lv1 {candidate['sticky_ammo_capacity']['1']}; "
            f"Lv2 {candidate['sticky_ammo_capacity']['2']}; "
            f"Lv3 {candidate['sticky_ammo_capacity']['3']}",
            f"Recoil: {candidate.get('recoil') or 'not parsed'}",
            f"Reload: {candidate.get('reload') or 'not parsed'}",
        ])
        weapon_lines.extend(weapon_stat_lines(candidate))
        weapon_lines.append(
            f"Source listing group: {record.get('source_group') or 'not provided'} (raw code, not decoded)")
        weapon_lines.extend(render_source_tables(record, {"Overview"}))
        weapon_lines.extend(render_materials(record, [
            ("forging_materials", "Forging materials"),
            ("upgrade_materials", "Upgrade materials"),
        ]))
        weapon_lines.append("")

    equipment_lines = [
        "Sticky Light Bowgun: Equipment and Skills Reference",
        f"Snapshot ID: {report.get('snapshot_id')}",
        "MR10 eligibility: unverified. Source listing groups are preserved as raw codes, not rank claims.",
        "Only armor and decorations granting a selected relevant skill are included.",
        "",
        "Relevant skills and level effects:",
    ]
    for skill in report["relevant_skills"]:
        record = next((entry for entry in skills if entry.get("id") == skill["id"]), {})
        equipment_lines.append(f"## {skill['name']} (ID: {skill['id']})")
        equipment_lines.append(f"Source URL: {skill.get('source_url') or 'not provided'}")
        if skill.get("max_level") is not None:
            equipment_lines.append(f"Max level: {skill['max_level']}")
        for level, effect in sorted(skill["effects"].items(), key=lambda pair: int(pair[0][2:])):
            equipment_lines.append(f"- {level.upper()}: {effect}")
        equipment_lines.extend(render_source_tables(record, {"Overview"}))

    equipment_lines.extend(["", "Relevant armor:"])
    for candidate in report["armor_candidates"]:
        record = armor_by_url[candidate["source_url"]]
        equipment_lines.append(f"## {candidate['name']}")
        equipment_lines.extend(source_metadata(record))
        equipment_lines.append(
            f"Source listing group: {record.get('source_group') or 'not provided'} (raw code, not decoded)"
        )
        if "fields" in record:
            resistances = candidate["resistances"] or {}
            equipment_lines.extend([
                f"Rarity: {optional(candidate['rarity'])}; base defense: {optional(candidate['defense_base'])}",
                "Resistances: " + (", ".join(f"{name} {value:+d}" for name, value in resistances.items())
                                   or "not provided"),
                f"Decoration slots: {optional(candidate['slot_notation'])}",
                "Armor part: not published by Kiranico",
            ])
        for skill in candidate["relevant_skills"]:
            level = skill.get("equipped_level")
            level_text = f"level {level}" if level is not None else "level not provided"
            equipment_lines.append(f"- {skill['name']} ({level_text}; ID: {skill['id']})")
        equipment_lines.extend(render_source_tables(record, {"Overview"}))
        equipment_lines.extend(render_materials(record, [("forging_materials", "Forging materials")]))
        equipment_lines.append("")

    equipment_lines.extend(["Relevant decorations:"])
    for candidate in report["decoration_candidates"]:
        record = decoration_by_url[candidate["source_url"]]
        equipment_lines.append(f"## {candidate['name']}")
        equipment_lines.extend(source_metadata(record))
        if candidate.get("slot_size") is not None:
            equipment_lines.append(f"Slot size: {candidate['slot_size']}")
        for skill in candidate["relevant_skills"]:
            level = skill.get("equipped_level")
            level_text = f"level {level}" if level is not None else "level not provided"
            equipment_lines.append(f"- {skill['name']} ({level_text}; ID: {skill['id']})")
        equipment_lines.extend(render_source_tables(record, {"Overview", "Required Materials"}))
        equipment_lines.extend(render_materials(record, [("materials", "Required materials")]))
        equipment_lines.append("")

    ammo_lines = [
        "Sticky Ammo Reference",
        f"Snapshot ID: {report.get('snapshot_id')}",
        "MR10 availability: not established by this reference; verify progression separately.",
        "Only source-provided descriptions and tables are included; no crafting recipe is inferred.",
        "",
    ]
    ammo_items = [record for record in items if record.get("name") in {
        "Sticky Ammo 1", "Sticky Ammo 2", "Sticky Ammo 3",
    }]
    ammo_items.sort(key=lambda record: int(record["name"].rsplit(" ", 1)[-1]))
    for record in ammo_items:
        ammo_lines.append(f"## {record['name']}")
        ammo_lines.extend(source_metadata(record))
        description = record.get("description") or (
            "not fetched in listings mode" if record.get("detail_status") == "not_fetched" else "not provided")
        ammo_lines.append(f"Description: {description}")
        ammo_lines.extend(render_source_tables(record, {"Weapons", "Armor", "Decorations"}))
        ammo_lines.append("")

    return {
        "01-sticky-lbg-weapons.txt": "\n".join(weapon_lines).rstrip() + "\n",
        "02-sticky-lbg-equipment-skills.txt": "\n".join(equipment_lines).rstrip() + "\n",
        "03-sticky-ammo-reference.txt": "\n".join(ammo_lines).rstrip() + "\n",
    }

def write_text_pack(snapshot, output_directory, report):
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    documents = build_text_documents(snapshot, report)
    for filename, content in documents.items():
        path = output_directory / filename
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    return sorted(documents)


def main():
    default_snapshot = Path(__file__).resolve().parent / "snapshots" / "latest"
    parser = argparse.ArgumentParser(
        description="Build an offline sticky Light Bowgun candidate report from a complete listings or full snapshot."
    )
    parser.add_argument("--snapshot", type=Path, default=default_snapshot)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--text-output-dir", type=Path)
    args = parser.parse_args()
    try:
        report = build_report(args.snapshot)
        output = args.output or args.snapshot / "analysis" / "sticky-lbg-mr10-candidates.json"
        write_report(output, report)
        text_output = args.text_output_dir or args.snapshot / "agent-builder-txt"
        text_files = write_text_pack(args.snapshot, text_output, report)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.exit(1, f"Analysis failed: {error}\n")
    print(f"Wrote {report['weapon_candidate_count']} Light Bowgun candidates to {output}")
    print(f"Wrote {len(text_files)} Agent Builder text files to {text_output}")
    print("MR10 eligibility is unverified; inspect the report limitations before choosing a build.")


if __name__ == "__main__":
    main()
