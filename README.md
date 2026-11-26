# MHRise Kiranico Data Pipeline

Kiranico (English, Database Ver16.0.0) → typed structured JSON → NotebookLM
Markdown → Sticky Light Bowgun analysis.

Requires Python 3.9 or later. The downloader and the Game8 adapter need
`python -m pip install requests beautifulsoup4`; the converter and the analysis
use only the standard library.

## Quick Start

Run from the workspace root:

```console
python download_kiranico.py --snapshot snapshots/latest --listings
python convert_notebooklm.py --source snapshots/latest/structured --output snapshots/latest/notebooklm
python analyze_sticky_lbg.py --snapshot snapshots/latest
```

Always pass `--source` and `--output` to the converter: its defaults still point
at the workspace root used by the legacy pipeline.

## Snapshot Modes

| Mode | Command flag | Requests | Result |
| --- | --- | --- | --- |
| Pilot | (default) | a few dozen | Sample of each category (`--limit-per-category`); `complete: false` |
| Listings | `--listings` | about 30 | Every listing page parsed into typed fields; no detail pages; `complete: true` |
| Full | `--full` | about 7000 (hours) | Listings plus every detail page: materials, weapon Rampage skills, Skill Lv rows for skills whose listing rows are blank |

- `--refresh` re-downloads listing pages instead of reusing the verified raw cache.
- `--delay` sets the minimum seconds between requests (at least 2). `robots.txt` is checked.
- Resume or upgrade a run by repeating the command with the same `--snapshot` path.
  Verified HTML and parsed records are reused from `raw/` and `checkpoint/`.
- A v1 manifest from an older snapshot is migrated automatically.

Categories:

- Core: Items, Skills, Decorations, Armors, Weapons. Coverage is only
  complete when all of these are parsed.
- Optional: RampageSkills and RampageDecorations (百龍珠).
- Unsupported: ten Kiranico routes (Quests, Monsters, Palamute/Palico equipment,
  Requests, Switch Skills, Dango Skills, Canteen, Endemic Life and Crafting List)
  are listed in the manifest coverage with status `not_supported`. "Complete"
  means the requested datasets, not the whole site.

## Snapshot Layout

```text
snapshots/latest/
  download-manifest.json        run status, mode, complete, coverage, supplements
  raw/, checkpoint/             verified HTML cache and parse checkpoints (about 70 MB)
  structured/<Category>.json    schema_version 2 records
  structured/required-fields.json   field contract for the four datasets
  structured/bowgun-rules.json  v16.0.0 recoil/reload lookup table
  supplemental/game8-lbg.json   optional Game8 supplement (only after game8_lbg.py)
  notebooklm/                   Markdown + conversion-report.json
  analysis/, agent-builder-txt/ Sticky Light Bowgun report and text pack
```

`snapshots/` is not ignored by Git; add it to `.gitignore` if you do not want
to commit the cache.

## Typed Fields

Each record is `{id, name, source_url, detail_status, fields, warnings}`.
Each field is `{value, availability, source}`, plus an optional `rule`,
`evidence` or `note`. Values are never invented. The `availability` code
explains why a value is present or absent:

| Availability | Meaning |
| --- | --- |
| `direct` | Read directly from a Kiranico page |
| `derived` | Converted from Kiranico codes, icons or graphics by the documented `rule` |
| `supplemental` | From an approved secondary source (Game8 Rapid Fire, the user's v16 table) |
| `unavailable_from_kiranico` | Kiranico does not publish this field |
| `not_applicable` | Does not apply to this record type |
| `not_fetched` | Published on Kiranico detail pages, which this mode did not fetch |
| `supplemental_missing` | The approved secondary source was not run or did not match |
| `missing` | Expected source value could not be parsed; see the record warnings |

Each structured file has `field_statistics`, which counts availability codes
per required field. `required-fields.json` lists every required field with its
Chinese requirement name, type, allowed availability codes and the weapon
types it applies to. A value outside the allowed availability codes is
counted as a contract violation in the download summary.

## Required Dataset Coverage

Counts are from the listings snapshot of Database Ver16.0.0.

**Weapons (3953)**

- Name, type, rarity and attack are `direct`.
- Affinity is `direct`. When Kiranico omits it (0%), it is `derived`.
- Element/status values are `derived` from element codes.
- Slots are `derived` from `decoN.png` icons and written as notation such as
  `3-1-0`, together with the Rampage slot level.
- Melee sharpness (3118 weapons) is `derived` from the SVG bars. The full
  bar is base sharpness plus Handicraft Lv5, and 1 px = 5 hits.
- Bowguns (522): ammo list and capacities are `derived`. Gray cells are
  treated as not loadable. Recoil, reload and deviation are `direct`
  weapon-level labels.
- Light Bowgun Rapid Fire ammo is `supplemental` from Game8. It is
  `supplemental_missing` until you run the Game8 supplement.
- Bows (313): arc shot and default charge levels are `direct`. Levels Kiranico
  shows in gray are kept separately as `bow_gray_charge_levels`. Usable coatings
  are `derived` (gray coatings are excluded and kept as evidence).

**Armors (1591)**

- Name, rarity, base defense and built-in skills with levels are `direct`.
- The five resistances and the slots (notation such as `4-1-1`) are `derived`.
- Armor part, male/female names and max defense are
  `unavailable_from_kiranico`.

**Decorations (243)**

- Name and skill with level are `direct`.
- Slot size (1–4) is `derived` from the trailing digit of the name.
- Unlock condition is `unavailable_from_kiranico`.

**Rampage Decorations (27)**

- Slot size (1–3) is `derived`.
- Rampage skill is `direct`. One record is `missing` because Kiranico shows an
  empty link.
- Unlock condition is `unavailable_from_kiranico`.

**Skills (147)**

- Max level and per-level effects are available for 111 skills. Effects are
  split into numeric components, for example
  Attack Boost Lv4 `Attack +5% Bonus: +7`.
- Kiranico publishes blank effects for 36 Sunbreak skills. These are
  `unavailable_from_kiranico`, and their max level is `not_fetched` in
  listings mode.

**Rules**

- `bowgun-rules.json` holds the recoil/reload threshold table (below).

## Recoil and Reload Thresholds

Kiranico publishes only labels, not a skill-level ladder. `bowgun_rules.py`
transcribes the user-provided v16.0.0 table. It maps:

- each recoil label to the Recoil Down level needed for Very Low;
- each reload label to the Reload Speed level needed for Fast/Fastest.

| Recoil label | Recoil Down needed | Best at Lv3 |
| --- | --- | --- |
| Very High | not reachable | Average |
| High | Lv3 | Very Low |
| Average | Lv2 | Very Low |
| Low | Lv1 | Very Low |
| Very Low | none | Very Low |

| Reload label | Reload Speed needed | Best at Lv3 |
| --- | --- | --- |
| Very Slow | not reachable | Normal |
| Slow | Lv3 | Fast/Fastest |
| Below Average (`Below Avg.`) | Lv2 | Fast/Fastest |
| Normal (`Average`) | Lv1 | Fast/Fastest |
| Fast/Fastest (`Fast`, `Fastest`) | none | Fast/Fastest |

Labels outside the table stay `unmapped`, and no level is inferred for them.
In this snapshot that affects:

- recoil `Some`: 61 of 258 Light Bowguns and 59 of 264 Heavy Bowguns;
- reload `Above Avg.`: 49 Light Bowguns and 71 Heavy Bowguns.

Kiranico labels are weapon-level base values. Recoil and reload per ammo type
come only from the Game8 supplement.

## Game8 Supplement (Opt-in)

Game8 is approved only for Light Bowgun Rapid Fire data. The supplement also
stores each ammo type's Game8 recoil and reload labels, with threshold
lookups. Kiranico values are never overwritten; capacity mismatches are kept
as record warnings. The supplement never runs automatically:

```console
python game8_lbg.py --snapshot snapshots/latest
python game8_lbg.py --snapshot snapshots/latest --html saved-pages
python download_kiranico.py --snapshot snapshots/latest --listings
```

- The first command fetches pages, checks `robots.txt`, identifies itself with
  its own User-Agent, and waits at least 2 seconds between requests.
- With `--html`, the second command reads pages saved from a browser instead.
  Each page's canonical link identifies its URL, and the saved files must
  include the Light Bowgun index page.
- The third command reruns the downloader with the same snapshot and mode,
  which merges the supplement. The match summary is stored under
  `supplements.game8_lbg` in the manifest.

The Game8 parser is tested against fixtures only. Check the match summary,
especially the unmatched names, after the first real run.

## NotebookLM Markdown

The converter writes one document group per category:

- Weapons are grouped by type (`Weapons-Light-Bowgun-001.md`).
- Armors are grouped by rarity (`Armors-rarity-8-001.md`).
- Items are grouped by item group.
- Other categories are written as `all`.
- `Rules-bowgun-001.md` holds the threshold table.

Each document header maps every Chinese requirement to its English field
label (`中文需求 = English label`). Absent values state the reason, for
example `Not published by Kiranico`. Derived and supplemental values are
marked. An excerpt:

```text
## Volarevolver
- Slot notation: 1-0-0 (derived)
- Rampage slot level: 2 (derived)
- Recoil (weapon base label): Average
- Recoil Down needed (v16 table): Average: Recoil Down Lv2 reaches Very Low (supplemental: user_provided_v16_table)
- Rapid Fire ammo: Supplement not available (run game8\_lbg.py)
- Rampage skills: Not fetched in this snapshot mode (on Kiranico detail pages)
```

Groups are split at 180,000 characters without splitting records, which gives
50 files for the listings snapshot. Use a larger `--max-chars` for fewer files.
Upload the `.md` files. Do not upload `conversion-report.json`, which is a
validation report. Ask NotebookLM to answer in Traditional Chinese while
keeping English names.

## Sticky Light Bowgun Candidates

```console
python analyze_sticky_lbg.py --snapshot snapshots/latest
```

The analysis requires a successful, complete listings or full snapshot. It
writes `analysis/sticky-lbg-mr10-candidates.json` and three UTF-8 `.txt`
files to `agent-builder-txt/`; use `--text-output-dir` to change that
directory. Upload only the `.txt` files to Agent Builder.

- Light Bowguns with Sticky ammo (157 in this snapshot) are sorted by highest
  Sticky level and capacity.
- Each weapon lists rarity, attack, slots, deviation, recoil/reload thresholds
  and, when merged, Game8 Rapid Fire and per-ammo Sticky handling.
- Armor, decorations and skill effects are included for Slugger, Artillery,
  Ammo Up, Recoil Down, Reload Speed and Spare Shot.
- The analysis gives a shortlist, not an optimized build.
- MR10 availability is unverified.
- Listings mode omits materials and weapon Rampage skills.

## Tests

```console
python -m unittest
```

| Module | Covers |
| --- | --- |
| `test_kiranico_listing` | Listing-row parsers, availability contract, threshold lookups |
| `test_download_kiranico` | Snapshot modes, resume, manifest migration, supplement merge |
| `test_game8_lbg` | Game8 index/family parsing and merge rules |
| `test_convert_notebooklm` | Typed and legacy Markdown conversion |
| `test_analyze_sticky_lbg` | Sticky Light Bowgun report and text pack |

## Data Integrity

- Structured source files are never modified by the converter or the analysis.
  Re-running replaces generated documents and removes obsolete generated parts.
- Missing, ambiguous or name-mismatched references are reported, not invented.
  Exact duplicates are removed; distinct records sharing an ID are kept and
  listed in `conversion-report.json`.
- Rampage skills stay separate from normal skills, even when IDs overlap.
- Legacy (pre-typed) snapshots still convert. Their armor
  `decoration_slot_levels` field is used only when present, and unknown slots
  are shown as `Unknown`.
- The root `notebooklm/` and `agent-builder-txt/` folders are output from the
  legacy pipeline. Current output is written inside the snapshot.

## Retrieval Checks

After importing the files into NotebookLM, check its answers against the source:

- What is Attack Boost's level 4 effect?
- What Recoil Down level does Volarevolver need to reach Very Low recoil?
- What slot size does KO Jewel 2 need, and which skill level does it give?
- Which armor fields does Kiranico not publish?
