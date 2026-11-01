# NotebookLM Markdown Export

Requires Python 3.9 or later; no third-party packages are needed.

## Downloading and Resuming

Run these commands from the workspace root. The first command runs a pilot
and stores its cache and checkpoints in `jsontonblm/snapshots/20261030-pilot`:

```console
python jsontonblm/download_kiranico.py --snapshot jsontonblm/snapshots/20261030-pilot
```

Upgrade that pilot to full coverage, or resume an interrupted full run, by
using the same snapshot path and adding `--full`:

```console
python jsontonblm/download_kiranico.py --snapshot jsontonblm/snapshots/20261030-pilot --full
```

Do not change the snapshot path when resuming. Verified HTML pages and parsed
records are checkpointed under the snapshot's `raw/` and `checkpoint/`
directories, so completed work is reused. The downloader checks `robots.txt`
and applies the configured request delay; a full download may take hours.
Pilot runs may finish with `complete: false`; a full snapshot must have complete
coverage before it can be converted.

After a successful full download, generate the NotebookLM Markdown files:

```console
python jsontonblm/convert_notebooklm.py --source jsontonblm/snapshots/20261030-pilot/structured --output jsontonblm/snapshots/20261030-pilot/notebooklm
```

## Sticky Light Bowgun Candidates

After the full snapshot completes, generate a focused candidate report:

```console
python jsontonblm/analyze_sticky_lbg.py --snapshot jsontonblm/snapshots/20261030-pilot
```

The report is written to
`jsontonblm/snapshots/20261030-pilot/analysis/sticky-lbg-mr10-candidates.json`.
It lists Light Bowguns with Sticky ammo, sorts them by supported Sticky ammo
level and capacity, and gathers armor, decoration, and skill records for
Slugger, Artillery, Ammo Up, Recoil Down, Reload Speed, and Spare Shot.
This is a candidate shortlist, not a computed best build: the source data does
not provide reliable MR10 unlock thresholds or normalized armor-slot data.
Manually verify MR10 availability before selecting equipment.

The same command also writes three UTF-8 `.txt` knowledge files to
`jsontonblm/snapshots/20261030-pilot/agent-builder-txt/`. These preserve source
IDs, URLs, relevant tables, materials, and an explicit MR10 verification note.
Upload the three `.txt` files to Agent Builder; do not upload the JSON report.
Use `--text-output-dir` to choose another output directory.

```console
python -m unittest -v test_convert_notebooklm
python -m unittest -v test_analyze_sticky_lbg
python -m unittest -v test_download_kiranico
python convert_notebooklm.py
```

Upload the generated `.md` files in `notebooklm/` as NotebookLM sources.
If the upload picker does not accept Markdown, import them as text sources
or convert the documents to a supported text format. Do not upload the JSON
validation report as game reference material.

The export uses English source names and readable English field labels.
Ask NotebookLM to answer in Traditional Chinese while preserving English names.
Armors are grouped by raw rank code, weapons by raw weapon type code;
other categories are grouped together. Groups are split at 180,000 characters
without splitting individual records. This is a conservative export size,
not a claim about NotebookLM's current upload limits.

```console
python convert_notebooklm.py --source . --output notebooklm --max-chars 120000
```

## Data Integrity

- Original JSON files are never modified.
- Source version and date appear in every document. This is a January 2022
  snapshot, not a verified current or complete Sunbreak dataset.
- Exact duplicate records are removed. Distinct records with the same ID
  remain in the export and are listed in `conversion-report.json`.
- Skills, materials, quantities, unlock conditions, and original fields are
  expanded or preserved. Normal skills include all known level effects.
- Equipped skill levels are not explicitly supplied by these armor records.
  Level effects are reference data, not a claim about levels granted by gear.
- Rampage skills are separate from normal skills, even when IDs overlap.
- Encoded fields, including rarity and decoration slot arrays, are retained
  without guessing their interpretation. Empty lists mean not provided.
- Missing, ambiguous, or name-mismatched references are reported, not invented.
- The report includes input/output counts, duplicate counts, conflicting IDs,
  source SHA-256 hashes, reference warnings, and the generated file list.
- Re-running replaces previously generated documents and removes obsolete
  generated parts. Keep the report with the output; unrelated files are kept.

## Retrieval Checks

After import, check answers against the originals:

- What is Attack Boost's level 4 effect?
- What unlock condition and materials does Mastery Jewel 2 require?
- What are Kamura Head Scarf S's defense and elemental resistances?
- Which details are missing about equipped armor skill levels?

These are source-faithful exports, not corrections to the scraped game data.