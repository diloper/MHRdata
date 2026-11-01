import argparse
import json
import re
from pathlib import Path


WEAPON_VIEW = "view-13"
FOCUS_SKILLS = {
    "Slugger", "Artillery", "Ammo Up", "Recoil Down", "Reload Speed", "Spare Shot",
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def records_for(snapshot, category):
    document = read_json(snapshot / "structured" / (category + ".json"))
    records = document.get("data")
    if not isinstance(records, list):
        raise ValueError(f"{category}.json must contain a data array")
    return records


def sticky_ammo(record):
    capacities = {"1": 0, "2": 0, "3": 0}
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
    return [
        {"name": entry["skill"]["name"], "id": entry["skill"]["id"],
         "source_url": entry["skill"].get("link"),
         "equipped_level": entry.get("equipped_level")}
        for entry in record.get("skills", [])
        if entry.get("skill", {}).get("name") in FOCUS_SKILLS
    ]


def weapon_profile(record):
    ammo = sticky_ammo(record)
    highest_level = max((int(level) for level, count in ammo.items() if count), default=0)
    recoil, reload = recoil_reload(record)
    return {
        "id": record["id"], "name": record["name"], "source_url": record["source_url"],
        "sticky_ammo_capacity": ammo, "highest_sticky_level": highest_level,
        "recoil": recoil, "reload": reload,
    }


def build_report(snapshot):
    snapshot = Path(snapshot).resolve()
    manifest_path = snapshot / "download-manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"Missing download manifest: {manifest_path}")
    manifest = read_json(manifest_path)
    if manifest.get("run_status") != "success":
        raise ValueError("Snapshot has not finished successfully; resume it before analysis")
    if manifest.get("mode") != "full" or not manifest.get("complete"):
        raise ValueError("A complete full snapshot is required for this analysis")

    weapons = records_for(snapshot, "Weapons")
    armors = records_for(snapshot, "Armors")
    decorations = records_for(snapshot, "Decorations")
    skills = records_for(snapshot, "Skills")

    candidates = [
        weapon_profile(record) for record in weapons
        if record.get("source_group") == WEAPON_VIEW
        and max(sticky_ammo(record).values(), default=0) > 0
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

    armor_candidates = [
        {"id": record["id"], "name": record["name"], "source_url": record["source_url"],
         "source_group": record.get("source_group"), "relevant_skills": skill_entries(record)}
        for record in armors if skill_entries(record)
    ]
    decoration_candidates = [
        {"id": record["id"], "name": record["name"], "source_url": record["source_url"],
         "relevant_skills": skill_entries(record)}
        for record in decorations if skill_entries(record)
    ]
    relevant_skills = [
        {"id": record["id"], "name": record["name"], "source_url": record["source_url"],
         "effects": {key: value for key, value in record.items() if re.fullmatch(r"lv\d+", key)}}
        for record in skills if record.get("name") in FOCUS_SKILLS
    ]

    return {
        "schema_version": 1,
        "snapshot_id": manifest.get("snapshot_id"),
        "snapshot_mode": manifest.get("mode"),
        "weapon_category": "Light Bowgun",
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
        "mr10_eligibility": {
            "status": "unverified",
            "reason": "The structured source does not expose a reliable MR unlock threshold for each armor or weapon.",
            "assumption": "No Qurio crafting is assumed; manually verify each candidate is obtainable by MR10.",
        },
        "limitations": [
            "The report does not calculate an optimal five-piece armor, charm, and decoration combination.",
            "Armor slots and MR unlock thresholds are not normalized by the current source parser.",
            "The weapon order is based on sticky-ammo capacity, not measured stun per second or recoil-adjusted firing cycles.",
        ],
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
        if table.get("section") not in sections:
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
        "Snapshot mode: full; complete coverage required.",
        "MR10 eligibility: unverified. Confirm unlock availability in-game.",
        "Ranking is an ammo-capacity shortlist, not a measured KO-performance optimum.",
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
            f"Source listing group: {record.get('source_group') or 'not provided'} (raw code, not decoded)",
        ])
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
        ammo_lines.append(f"Description: {record.get('description') or 'not provided'}")
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
        description="Build an offline sticky Light Bowgun candidate report from a full snapshot."
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
