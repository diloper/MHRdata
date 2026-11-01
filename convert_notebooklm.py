import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


SOURCES = ("Items", "Skills", "Decorations", "Armors", "Weapons")
RELATIONS = {
    "skills": ("Skills", "skill"),
    "rampage_skills": (None, "skill"),
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
}


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
        if manifest.get("mode") == "full" and not manifest.get("complete"):
            raise ValueError("Full download snapshot has incomplete coverage; resume it before converting")
    for category in SOURCES:
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
        document["data"] = unique
        documents[category] = document
    return documents, report


def levels(record):
    return sorted((key for key in record if re.fullmatch(r"lv\d+", key)),
                  key=lambda key: int(key[2:]))


def render_record(category, record, indexes, warnings):
    name = text(record["name"])
    output = [f"## {name}", "", f"- Category: {category}",
              f"- ID: {text(record['id'])}"]
    for key, value in record.items():
        if key in {"name", "id", "tables"} or key in RELATIONS or key in levels(record):
            continue
        label = LABELS.get(key, key)
        output.append(f"- {label}: {text(value)}")
    if category == "Armors" and not any("equipped_level" in entry for entry in record.get("skills", [])):
        output.append("- Equipped skill levels: not explicitly provided; do not infer from skillList.")
    if levels(record):
        output.extend(["", f"### {name}: Skill Level Effects", ""])
        output.extend(f"- Level {key[2:]}: {text(record[key])}" for key in levels(record))
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
            reference_id = str(reference.get("id", ""))
            matches = indexes.get(target_category, {}).get(reference_id, [])
            matching_names = [candidate for candidate in matches
                              if candidate["name"] == reference.get("name")]
            resolved = matching_names[0] if len(matching_names) == 1 else (
                matches[0] if len(matches) == 1 else None)
            if target_category and (resolved is None or resolved["name"] != reference.get("name")):
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
                output.append(f"  - Skill description: {text(resolved.get('description'))}")
                output.extend(f"  - {text(resolved['name'])}, level {key[2:]}: {text(resolved[key])}"
                              for key in levels(resolved))
    for table in record.get("tables", []):
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


def header(category, group, document):
    coverage = document.get("coverage")
    provenance = ""
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
            "All listed skill level effects are reference information, not equipped levels.\n"
            "Rampage skills use a separate ID namespace from normal skills.\n\n")


def build_documents(documents, report, max_chars):
    indexes = {}
    for category in ("Items", "Skills"):
        indexes[category] = defaultdict(list)
        for record in documents[category]["data"]:
            indexes[category][str(record["id"])].append(record)
    files = {}
    for category, document in documents.items():
        groups = defaultdict(list)
        for record in document["data"]:
            group = "all"
            if category in {"Armors", "Weapons"} and record.get("source_group"):
                group = record["source_group"]
            elif category == "Armors":
                group = "rank-" + str(record.get("rank", "unknown"))
            elif category == "Weapons":
                group = "type-" + str(record.get("weapon_type", "unknown"))
            group = re.sub(r"[^a-zA-Z0-9-]", "-", group)
            groups[group].append(record)
        for group, records in groups.items():
            intro = header(category, group, document)
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
    report["generated_files"] = sorted(files)
    return files


def convert(source_dir, output_dir, max_chars=180000):
    source_dir = Path(source_dir).resolve()
    output_dir = Path(output_dir).resolve()
    if source_dir == output_dir:
        raise ValueError("Output must be a separate directory")
    if max_chars < 2000:
        raise ValueError("max-chars must be at least 2000")
    documents, report = load_sources(source_dir)
    files = build_documents(documents, report, max_chars)
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
        if re.fullmatch(r"(?:Items|Skills|Decorations|Armors|Weapons)-[a-zA-Z0-9-]+-\d+\.md", filename):
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
    print(f"Reference warnings: {len(report['warnings'])}; see conversion-report.json.")


if __name__ == "__main__":
    main()