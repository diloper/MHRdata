"""Typed parsers for Kiranico English listing pages.

Each parser returns ``(meta, records, errors)``.  A record is keyed by its detail URL
(detail-backed categories) or a stable ``source_record_key`` (listing-only
categories) and carries typed ``fields`` shaped ``{value, availability, source, ...}``.
"""

import re
from urllib.parse import parse_qsl, urljoin, urlsplit

from bs4 import BeautifulSoup

import bowgun_rules
from kiranico_schema import (BASE_URL, BOWGUN_TYPES, ELEMENTS, MELEE_TYPES, RESISTANCES,
                             WEAPON_TYPES, field, slot_notation)

LISTING = "kiranico_listing"
SHARPNESS_COLORS = [("red", "#BE3843"), ("orange", "#D3673D"), ("yellow", "#C9B232"),
                    ("green", "#81B034"), ("blue", "#3A58D7"), ("white", "#E2E2E2"),
                    ("purple", "#885AEC")]
HITS_PER_PIXEL = 5
SHARPNESS_RULE = ("Kiranico draws sharpness as SVG bars. Handicraft adds +10 sharpness per level "
                  "(+50 at Lv5) and the base-to-max bar difference is 10 px, so 1 px = 5 hits.")
AMMO_NAMES = {
    "Nrm": ("Normal Ammo", 3), "Prc": ("Pierce Ammo", 3), "Spr": ("Spread Ammo", 3),
    "Shr": ("Shrapnel Ammo", 3), "Sti": ("Sticky Ammo", 3), "Clu": ("Cluster Bomb", 3),
    "Fir/P.": (["Flaming Ammo", "Piercing Fire Ammo"], 2),
    "Wat/P.": (["Water Ammo", "Piercing Water Ammo"], 2),
    "Thn/P.": (["Thunder Ammo", "Piercing Thunder Ammo"], 2),
    "Ice/P.": (["Freeze Ammo", "Piercing Ice Ammo"], 2),
    "Dra/P.": (["Dragon Ammo", "Piercing Dragon Ammo"], 2),
    "Poi": ("Poison Ammo", 2), "Par": ("Paralysis Ammo", 2), "Sle": ("Sleep Ammo", 2),
    "Exh": ("Exhaust Ammo", 2), "Rec": ("Recover Ammo", 2),
    "Dem": ("Demon Ammo", 1), "Amr": ("Armor Ammo", 1), "Sli": ("Slicing Ammo", 1),
    "Wyv": ("Wyvern Ammo", 1), "Tra": ("Tranq Ammo", 1),
}
AMMO_RULE = ("Capacities are read from Kiranico's ammo matrix. Gray (text-gray-400) cells are "
             "ammo the weapon cannot load, even when they show a capacity number, so only "
             "non-gray, non-zero cells are listed.")
COATING_RULE = ("Kiranico lists all bow coatings; gray (text-gray-400) entries are coatings the bow "
                "cannot use and are excluded. Green highlighting is kept as evidence only.")
GRAY_CHARGE_RULE = ("Charge levels Kiranico shows in gray (text-gray-400) are not available by default "
                    "(Bow Charge Plus unlocks one more level); they are kept separate from the default "
                    "charge levels and no unlock rule is inferred for each one.")
SLOT_ICON = re.compile(r"(?:[^?#]*/)?deco([1-4])\.png(?:[?#].*)?")
NOT_FETCHED_NOTE = "Available on Kiranico detail pages; listings mode does not fetch them."


class RowError(ValueError):
    pass


def text(node):
    return " ".join(node.get_text(" ", strip=True).split()) if node is not None else ""


def is_gray(node):
    return "text-gray-400" in (node.get("class") or [])


def absolute(href):
    return urljoin(BASE_URL, href)


def link_id(anchor, slug):
    match = re.fullmatch(rf"/data/{re.escape(slug)}/(\d+)", urlsplit(absolute(anchor["href"])).path)
    if not match:
        raise RowError(f"Unexpected {slug} link: {anchor.get('href')}")
    return match[1]


def listing_meta(soup, url):
    article = soup.find("article")
    if article is None or article.find("h1") is None:
        raise ValueError(f"Missing listing article/title: {url}")
    version = next((text(heading) for heading in soup.select("h2")
                    if re.fullmatch(r"Database Ver[\d.]+", text(heading))), None)
    query = dict(parse_qsl(urlsplit(url).query))
    own_path = urlsplit(url).path + ("?" + urlsplit(url).query if urlsplit(url).query else "")
    # The language switcher also links to this page as "English"; the view tab is the other one.
    view_label = next((text(anchor) for anchor in soup.select("a[href]")
                       if anchor["href"] == own_path and text(anchor) not in {"", "English"}), None)
    return {"url": url, "title": text(article.find("h1")), "view": query.get("view"),
            "view_label": view_label,
            "source_version": version.removeprefix("Database Ver") if version else None}


def table_rows(soup):
    table = soup.find("article").find("table")
    if table is None:
        return []
    body = table.find("tbody") or table
    return [row for row in body.find_all("tr", recursive=False)]


def slot_levels(images, warnings, label):
    levels = []
    for image in images:
        match = SLOT_ICON.fullmatch(image.get("src", ""))
        if not match:
            warnings.append(f"Unrecognized {label} icon: {image.get('src')}")
            return None
        levels.append(int(match[1]))
    return levels


def slot_fields(levels, label, notation_width=3):
    if levels is None:
        return (field(None, "missing", LISTING, note=f"Unrecognized {label} icon"),
                field(None, "missing", LISTING))
    return (field(levels, "derived", LISTING, rule="decoN.png icon -> slot level N"),
            field(slot_notation(levels, notation_width), "derived", LISTING,
                  rule="Slot levels sorted descending and padded with 0 to three slots"))


def element_spans(cell):
    values = []
    for span in cell.select("span[data-key=element]"):
        attack = span.select_one("span[data-key=elementAttack]")
        code = int(span["data-value"])
        if code not in ELEMENTS or attack is None:
            raise RowError(f"Unrecognized element span: {span}")
        name, kind = ELEMENTS[code]
        values.append({"type": name, "kind": kind, "code": code, "value": int(attack["data-value"])})
    return values


def skill_entries(cell, slug="skills"):
    entries = []
    for div in cell.find_all("div", recursive=False) or [cell]:
        anchor = div.find("a", href=True)
        if anchor is None:
            continue
        level = re.search(r"\bLv\s*(\d+)\b", text(div))
        entries.append({"name": text(anchor), "id": link_id(anchor, slug),
                        "level": int(level[1]) if level else None})
    return entries


def parse_sharpness(cell):
    bars = []
    for svg in cell.find_all("svg"):
        rects = svg.find_all("rect")
        if [rect.get("fill", "").upper() for rect in rects] != [color for _, color in SHARPNESS_COLORS]:
            raise RowError("Unexpected sharpness bar colors")
        bars.append([int(rect["width"]) for rect in rects])
    if len(bars) != 2:
        raise RowError(f"Expected two sharpness bars, found {len(bars)}")
    base, maximum = bars
    to_hits = lambda widths: {name: width * HITS_PER_PIXEL
                              for (name, _), width in zip(SHARPNESS_COLORS, widths)}
    value = {"base": to_hits(base), "max": to_hits(maximum),
             "base_total": sum(base) * HITS_PER_PIXEL, "max_total": sum(maximum) * HITS_PER_PIXEL,
             "unit": "hits"}
    return field(value, "derived", LISTING, rule=SHARPNESS_RULE,
                 evidence={"base_px": base, "max_px": maximum})


def parse_bowgun(cell, weapon_type, warnings):
    table = cell.find("table")
    if table is None:
        raise RowError("Missing bowgun stats table")
    columns = table.find("tr").find_all("td", recursive=False)
    if len(columns) != 5:
        raise RowError(f"Expected five bowgun columns, found {len(columns)}")
    labels = {}
    for div in columns[0].find_all("div", recursive=False):
        words = text(div).split(" ", 1)
        if len(words) == 2 and words[0] in {"Deviation", "Recoil", "Reload"}:
            labels[words[0].lower()] = words[1]
    ammo, unloadable, seen = [], [], set()
    for column in columns[1:]:
        for row in column.find("table").find_all("tr"):
            cells = row.find_all("td")
            code = text(cells[0])
            if code not in AMMO_NAMES:
                raise RowError(f"Unknown ammo code: {code}")
            names, levels = AMMO_NAMES[code]
            if len(cells) - 1 != levels:
                raise RowError(f"Unexpected ammo column count for {code}")
            seen.add(code)
            for index, capacity_cell in enumerate(cells[1:]):
                capacity = int(text(capacity_cell))
                gray = is_gray(capacity_cell)
                if isinstance(names, list):
                    name, level = names[index], None
                elif levels == 1:
                    name, level = names, None
                else:
                    name, level = f"{names} {index + 1}", index + 1
                entry = {"code": code, "name": name, "level": level, "capacity": capacity}
                if gray or capacity == 0:
                    if capacity:
                        unloadable.append(entry)
                    continue
                ammo.append(entry)
    if seen != set(AMMO_NAMES):
        raise RowError(f"Missing ammo codes: {sorted(set(AMMO_NAMES) - seen)}")
    fields = {}
    for key in ("deviation", "recoil", "reload"):
        fields[key] = (field(labels[key], "direct", LISTING) if key in labels else
                       field(None, "missing", LISTING, note=f"No {key} label in the stats cell"))
    fields["ammo"] = field(ammo, "derived", LISTING, rule=AMMO_RULE,
                           evidence={"gray_nonzero_capacity": unloadable} if unloadable else None)
    for key in ("recoil", "reload"):
        label = labels.get(key)
        lookup = bowgun_rules.lookup(key, label, "weapon_base_label") if label else None
        fields[key + "_lookup"] = (
            field(lookup, "supplemental", bowgun_rules.RULE_SOURCE,
                  note="Weapon base label only; per-ammo labels from Game8 are authoritative.")
            if lookup else field(None, "missing", bowgun_rules.RULE_SOURCE))
    if weapon_type == "Light Bowgun":
        fields["rapid_fire_ammo"] = field(None, "supplemental_missing", "game8",
                                          note="Kiranico has no Rapid Fire data; run game8_lbg.py.")
    else:
        fields["rapid_fire_ammo"] = field(None, "not_applicable",
                                          note="Rapid Fire is a Light Bowgun mechanic.")
    return fields


def parse_extras(cell, weapon_type, warnings):
    values = [text(div) for div in cell.select("div.columns-3 > div")]
    values = [value for value in values if value]
    fields = {}
    if weapon_type == "Hunting Horn":
        fields["hunting_horn_songs"] = field(values, "direct", LISTING)
    elif weapon_type == "Gunlance":
        match = re.fullmatch(r"(\w+) (\d+)", values[0]) if len(values) == 1 else None
        fields["shelling"] = (field({"type": match[1], "level": int(match[2])}, "direct", LISTING)
                              if match else field(values, "missing", LISTING, note="Unexpected shelling"))
    elif weapon_type in {"Switch Axe", "Charge Blade"}:
        match = re.fullmatch(r"(.+? Phial)(?: (\d+))?", values[0]) if len(values) == 1 else None
        fields["phial"] = (field({"type": match[1], "value": int(match[2]) if match[2] else None},
                                 "direct", LISTING)
                           if match else field(values, "missing", LISTING, note="Unexpected phial"))
    elif weapon_type == "Insect Glaive":
        match = re.fullmatch(r"Kinsect Level (\d+)", values[0]) if len(values) == 1 else None
        fields["kinsect_level"] = (field(int(match[1]), "direct", LISTING) if match else
                                   field(values, "missing", LISTING, note="Unexpected kinsect"))
    elif weapon_type == "Bow":
        divs = [div for div in cell.select("div.columns-3 > div") if text(div)]
        if not divs:
            fields["bow_coatings"] = field(None, "missing", LISTING, note="No coating list found")
        else:
            fields["bow_coatings"] = field(
                [text(div) for div in divs if not is_gray(div)], "derived", LISTING, rule=COATING_RULE,
                evidence={"gray_not_usable": [text(div) for div in divs if is_gray(div)],
                          "green_highlighted": [text(div) for div in divs
                                                if "text-green-500" in (div.get("class") or [])]})
    elif values:
        warnings.append(f"Unexpected extra stats for {weapon_type}: {values}")
    return fields


def parse_weapon_row(cells, meta):
    if len(cells) != 8:
        raise RowError(f"Expected 8 weapon cells, found {len(cells)}")
    weapon_type = meta["title"]
    if weapon_type not in WEAPON_TYPES:
        raise RowError(f"Unknown weapon type heading: {weapon_type}")
    warnings = []
    anchor = cells[1].find("a", href=True)
    if anchor is None:
        raise RowError("Missing weapon link")
    record_id = link_id(anchor, "weapons")
    fields = {"name": field(text(anchor), "direct", LISTING),
              "weapon_type": field(weapon_type, "direct", LISTING, evidence={"view": meta["view"]})}
    if meta["view"] is not None and meta["view"].isdigit() and int(meta["view"]) < len(WEAPON_TYPES) \
            and WEAPON_TYPES[int(meta["view"])] != weapon_type:
        warnings.append(f"View {meta['view']} heading {weapon_type} differs from the registry")
    rarity = re.fullmatch(r"Rare (\d+)", text(cells[7]))
    fields["rarity"] = (field(int(rarity[1]), "direct", LISTING) if rarity else
                        field(None, "missing", LISTING, note=f"Unexpected rarity: {text(cells[7])}"))
    attack = cells[3].select_one("[data-key=attack]")
    fields["attack"] = (field(int(text(attack)), "direct", LISTING)
                        if attack is not None and text(attack).lstrip("-").isdigit()
                        else field(None, "missing", LISTING))
    stats = cells[4]
    elements = element_spans(stats)
    fields["element"] = field(elements, "derived", LISTING,
                              rule=("ElementType code 1-5 = Fire/Water/Thunder/Ice/Dragon, 6-9 = "
                                    "Poison/Sleep/Paralysis/Blast; no element span means no "
                                    "element/status"))
    stats_text = text(stats)
    affinity = re.search(r"Affinity\s*([+-]?\d+)%", stats_text)
    fields["affinity"] = (field(int(affinity[1]), "direct", LISTING) if affinity else
                          field(0, "derived", LISTING, rule="Kiranico omits Affinity when it is 0%"))
    bonus = re.search(r"Defense Bonus\s*\+?(-?\d+)", stats_text)
    fields["defense_bonus"] = (field(int(bonus[1]), "direct", LISTING) if bonus else
                               field(0, "derived", LISTING, rule="Kiranico omits Defense Bonus when 0"))
    residue = re.sub(r"Affinity\s*[+-]?\d+%|Defense Bonus\s*\+?-?\d+", "", stats_text)
    residue = re.sub(r"-?\d+", "", residue).strip()
    if residue:
        warnings.append(f"Unparsed weapon stat text: {residue}")
    normal_slots, rampage_slots = None, None
    for div in cells[2].find_all("div", recursive=False):
        label = text(div.find("small"))
        if label == "Slots":
            normal_slots = slot_levels(div.find_all("img"), warnings, "slot")
        elif label == "Rampage Slots":
            rampage_slots = slot_levels(div.find_all("img"), warnings, "rampage slot")
    fields["decoration_slots"], fields["slot_notation"] = slot_fields(normal_slots, "slot")
    fields["rampage_decoration_slots"] = (
        field(rampage_slots, "derived", LISTING, rule="decoN.png icon -> slot level N")
        if rampage_slots is not None else field(None, "missing", LISTING))
    fields["rampage_slot_level"] = (
        field(max(rampage_slots, default=0), "derived", LISTING,
              rule="Highest Rampage Slots icon level; 0 when no Rampage Slot icon is shown")
        if rampage_slots is not None else field(None, "missing", LISTING))
    detail = cells[5]
    not_applicable = lambda reason: field(None, "not_applicable", note=reason)
    if weapon_type in MELEE_TYPES:
        fields["sharpness"] = parse_sharpness(detail)
    else:
        fields["sharpness"] = not_applicable("Ranged weapons have no sharpness")
    if weapon_type in BOWGUN_TYPES:
        fields.update(parse_bowgun(detail, weapon_type, warnings))
    else:
        for key in ("ammo", "deviation", "recoil", "reload", "recoil_lookup", "reload_lookup",
                    "rapid_fire_ammo"):
            fields[key] = not_applicable("Only bowguns load ammo")
    if weapon_type == "Bow":
        divs = [div for div in detail.find_all("div") if text(div)]
        values = [text(div) for div in divs]
        charge_divs = [div for div in divs[1:] if re.fullmatch(r"\w+ Level \d", text(div))]
        if not values or len(charge_divs) != len(values) - 1:
            warnings.append(f"Unexpected bow shot list: {values}")
        fields["bow_arc_shot"] = field(values[0] if values else None,
                                       "direct" if values else "missing", LISTING)
        fields["bow_charge_levels"] = field([text(div) for div in charge_divs if not is_gray(div)],
                                            "direct", LISTING)
        fields["bow_gray_charge_levels"] = field([text(div) for div in charge_divs if is_gray(div)],
                                                 "derived", LISTING, rule=GRAY_CHARGE_RULE)
    fields.update(parse_extras(cells[6], weapon_type, warnings))
    fields["rampage_skills"] = field(None, "not_fetched", "kiranico_detail", note=NOT_FETCHED_NOTE)
    image = cells[0].find("img")
    fields["image"] = field(image.get("src") if image else None, "direct" if image else "missing",
                            LISTING)
    return absolute(anchor["href"]), record_id, fields, warnings


def parse_armor_row(cells, meta):
    if len(cells) != 7:
        raise RowError(f"Expected 7 armor cells, found {len(cells)}")
    warnings = []
    anchor = cells[2].find("a", href=True)
    if anchor is None:
        raise RowError("Missing armor link")
    record_id = link_id(anchor, "armors")
    rarity = re.fullmatch(r"Rarity (\d+)", meta.get("view_label") or "")
    fields = {"name": field(text(anchor), "direct", LISTING),
              "rarity": (field(int(rarity[1]), "direct", LISTING, evidence={"view": meta["view"]})
                         if rarity else field(None, "missing", LISTING, note="No Rarity view label"))}
    if rarity and meta["view"] and meta["view"].isdigit() and int(meta["view"]) + 1 != int(rarity[1]):
        warnings.append(f"View {meta['view']} label {meta['view_label']} is not view+1")
    images = [cell.find("img") for cell in cells[:2]]
    fields["images"] = field({"male": images[0].get("src") if images[0] else None,
                              "female": images[1].get("src") if images[1] else None},
                             "direct", LISTING)
    fields["gender_names"] = field(None, "unavailable_from_kiranico",
                                   note="Kiranico shows one English name with male/female images.")
    fields["part"] = field(None, "unavailable_from_kiranico",
                           note="Kiranico listings have no armor part code; not guessed from names.")
    defense = cells[4].find("div")
    fields["defense_base"] = (field(int(text(defense)), "direct", LISTING)
                              if defense is not None and text(defense).isdigit()
                              else field(None, "missing", LISTING))
    fields["defense_max"] = field(None, "unavailable_from_kiranico",
                                  note="Kiranico shows a single defense value.")
    resistances = {}
    for span in cells[4].select("span[data-key=element]") + cells[5].select("span[data-key=element]"):
        code = int(span["data-value"])
        value = span.select_one("span[data-key=elementAttack]")
        if code not in RESISTANCES or value is None:
            raise RowError(f"Unrecognized resistance span: {span}")
        resistances[RESISTANCES[code]] = int(value["data-value"])
    fields["resistances"] = (
        field({name: resistances[name] for name in RESISTANCES.values()}, "derived", LISTING,
              rule="ElementType code 1-5 = fire/water/thunder/ice/dragon resistance")
        if set(resistances) == set(RESISTANCES.values())
        else field(resistances or None, "missing", LISTING, note="Expected five resistances"))
    fields["skills"] = field(skill_entries(cells[6]), "direct", LISTING)
    levels = slot_levels(cells[3].find_all("img"), warnings, "slot")
    fields["decoration_slots"], fields["slot_notation"] = slot_fields(levels, "slot")
    return absolute(anchor["href"]), record_id, fields, warnings


def parse_decoration_row(cells, meta):
    if len(cells) != 3:
        raise RowError(f"Expected 3 decoration cells, found {len(cells)}")
    anchor = cells[0].find("a", href=True)
    if anchor is None:
        raise RowError("Missing decoration link")
    name = text(anchor)
    size = re.search(r"\s([1-4])$", name)
    fields = {"name": field(name, "direct", LISTING), "kind": field("decoration", "direct", LISTING),
              "slot_size": (field(int(size[1]), "derived", LISTING,
                                  rule="Trailing digit 1-4 of the decoration name")
                            if size else field(None, "missing", LISTING, note="No size digit")),
              "skills": field(skill_entries(cells[1]), "direct", LISTING),
              "effect": field(text(cells[2]), "direct", LISTING),
              "unlock": field(None, "unavailable_from_kiranico",
                              note="Kiranico lists skills and materials, not unlock conditions.")}
    return absolute(anchor["href"]), link_id(anchor, "decorations"), fields, []


def stable_key(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def parse_rampage_decoration_row(cells, meta):
    if len(cells) != 3:
        raise RowError(f"Expected 3 rampage decoration cells, found {len(cells)}")
    name = text(cells[0])
    anchor = cells[1].find("a", href=True)
    if not name or anchor is None:
        raise RowError("Missing rampage decoration name or skill link")
    warnings = []
    if anchor["href"].strip():
        skill = field({"name": text(anchor), "id": link_id(anchor, "rampage-skills")}, "direct", LISTING)
    else:
        skill = field(None, "missing", LISTING,
                      note="Kiranico row has an empty rampage-skill link; the skill is not inferred.")
        warnings.append("Empty rampage-skill link on Kiranico")
    size = re.search(r"\s([1-3])$", name)
    fields = {"name": field(name, "direct", LISTING),
              "kind": field("rampage_decoration", "direct", LISTING),
              "slot_size": (field(int(size[1]), "derived", LISTING,
                                  rule="Trailing digit 1-3 of the rampage decoration name")
                            if size else field(None, "missing", LISTING, note="No size digit")),
              "rampage_skill": skill,
              "effect": field(text(cells[2]), "direct", LISTING),
              "unlock": field(None, "unavailable_from_kiranico",
                              note="Kiranico lists no unlock condition for rampage decorations.")}
    key = stable_key(name)
    return f"rampage-decorations:{key}", key, fields, warnings


def parse_rampage_skill_row(cells, meta):
    if len(cells) != 2:
        raise RowError(f"Expected 2 rampage skill cells, found {len(cells)}")
    anchor = cells[0].find("a", href=True)
    if anchor is None:
        raise RowError("Missing rampage skill link")
    fields = {"name": field(text(anchor), "direct", LISTING),
              "effect": field(text(cells[1]), "direct", LISTING)}
    return absolute(anchor["href"]), link_id(anchor, "rampage-skills"), fields, []


COMPONENT = re.compile(r"(?P<mult>[x×](?P<factor>\d+(?:\.\d+)?))|(?P<sign>[+-])?(?P<num>\d+(?:\.\d+)?)(?P<pct>%)?")


def effect_components(effect):
    components, start = [], 0
    for match in COMPONENT.finditer(effect):
        if match.start() > 0 and effect[match.start() - 1].isalnum():
            continue
        segment = effect[start:match.start()].rsplit(". ", 1)[-1]
        label = re.sub(r"^(?:and|,)\s+", "", segment.strip(" ,:;.")).strip(" ,:;.") or None
        if match["mult"]:
            component = {"label": label, "value": float(match["factor"]), "unit": "x",
                         "kind": "multiplier"}
        else:
            number = float(match["num"])
            number = int(number) if number.is_integer() else number
            component = {"label": label, "value": -number if match["sign"] == "-" else number,
                         "unit": "%" if match["pct"] else None,
                         "kind": "delta" if match["sign"] else "value"}
        components.append(component)
        start = match.end()
    return components


def parse_skill_row(cells, meta):
    if len(cells) != 2:
        raise RowError(f"Expected 2 skill cells, found {len(cells)}")
    anchor = cells[0].find("a", href=True)
    if anchor is None:
        raise RowError("Missing skill link")
    name_node = anchor.find("p")
    name = text(name_node) if name_node is not None else text(anchor)
    description = next((text(p) for p in cells[1].find_all("p", recursive=False)
                        if p.find("small") is None and text(p)), None)
    levels = []
    for small in cells[1].select("p > small"):
        value = text(small)
        if not value:
            continue
        match = re.fullmatch(r"Lv\s*(\d+)\s*(.*)", value)
        if not match:
            raise RowError(f"Unexpected skill level text: {value}")
        levels.append({"level": int(match[1]), "effect": match[2],
                       "components": effect_components(match[2])})
    numbers = [entry["level"] for entry in levels]
    warnings = [] if numbers == list(range(1, len(numbers) + 1)) else [
        f"Skill levels are not consecutive from 1: {numbers}"]
    blank = not levels and bool(cells[1].select("p > small"))
    fields = {"name": field(name, "direct", LISTING),
              "description": field(description, "direct" if description else "missing", LISTING),
              "max_level": (field(max(numbers), "derived", LISTING,
                                  rule="Highest level listed on Kiranico") if numbers
                            else field(None, "not_fetched", "kiranico_detail", note=BLANK_LEVELS_NOTE)
                            if blank else field(None, "missing", LISTING)),
              "levels": (field(levels, "direct", LISTING, rule=COMPONENT_RULE) if levels
                         else field([], "unavailable_from_kiranico", note=BLANK_EFFECTS_NOTE)
                         if blank else field(None, "missing", LISTING))}
    return absolute(anchor["href"]), link_id(anchor, "skills"), fields, warnings


COMPONENT_RULE = ("components: signed numbers are deltas, xN multipliers, unsigned numbers values; "
                  "label is the preceding words")
BLANK_LEVELS_NOTE = ("Kiranico's skills listing shows blank level rows for this skill; the detail page "
                     "lists the Lv rows (fetched in --full mode).")
BLANK_EFFECTS_NOTE = "Kiranico publishes blank per-level effect text for this skill."


def detail_skill_levels(record):
    """Rebuild the Skills levels/max_level fields from a parsed detail page."""
    levels = sorted((int(key[2:]), value) for key, value in record.items()
                    if re.fullmatch(r"lv\d+", key) and isinstance(value, str))
    if not levels:
        return {}
    entries = [{"level": level, "effect": effect, "components": effect_components(effect)}
               for level, effect in levels]
    max_level = field(levels[-1][0], "derived", "kiranico_detail",
                      rule="Highest Lv row on the Kiranico detail page")
    if any(effect for _, effect in levels):
        return {"max_level": max_level,
                "levels": field(entries, "direct", "kiranico_detail", rule=COMPONENT_RULE)}
    return {"max_level": max_level,
            "levels": field(entries, "unavailable_from_kiranico", "kiranico_detail",
                            note=BLANK_EFFECTS_NOTE)}


def parse_item_links(soup, meta):
    records, seen = [], set()
    for anchor in soup.find("article").select("a[href]"):
        if not re.fullmatch(r"/data/items/\d+", urlsplit(absolute(anchor["href"])).path):
            continue
        url = absolute(anchor["href"]).split("#")[0]
        if url in seen:
            continue
        seen.add(url)
        name_node = anchor.find("p")
        name = text(name_node) if name_node is not None else text(anchor)
        fields = {"name": field(name, "direct", LISTING),
                  "item_group": field(meta.get("view_label"), "direct" if meta.get("view_label")
                                      else "missing", LISTING, evidence={"view": meta["view"]})}
        records.append({"key": url, "id": link_id(anchor, "items"), "fields": fields, "warnings": []})
    return records


ROW_PARSERS = {"Weapons": parse_weapon_row, "Armors": parse_armor_row,
               "Decorations": parse_decoration_row, "Skills": parse_skill_row,
               "RampageDecorations": parse_rampage_decoration_row,
               "RampageSkills": parse_rampage_skill_row}


def parse_listing(category, html, url):
    soup = BeautifulSoup(html, "html.parser")
    meta = listing_meta(soup, url)
    if category == "Items":
        records = parse_item_links(soup, meta)
        return meta, records, [] if records else [f"No item links: {url}"]
    parser = ROW_PARSERS[category]
    records, errors = [], []
    rows = table_rows(soup)
    if not rows:
        return meta, records, [f"No listing rows: {url}"]
    for index, row in enumerate(rows):
        try:
            key, record_id, fields, warnings = parser(row.find_all("td", recursive=False), meta)
        except (RowError, ValueError, KeyError, TypeError, AttributeError, IndexError) as error:
            errors.append(f"{url} row {index + 1}: {error}")
            continue
        records.append({"key": key, "id": record_id, "fields": fields, "warnings": warnings})
    return meta, records, errors
