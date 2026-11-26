"""Opt-in Game8 supplement: Light Bowgun Rapid Fire ammo (approved scope only).

Kiranico publishes no Rapid Fire data, so this adapter reads Game8's Light Bowgun family
pages and writes ``<snapshot>/supplemental/game8-lbg.json``.  ``download_kiranico.py``
re-applies that file on every run; Kiranico values are never overwritten.

Run it yourself (it is never run automatically):

    python game8_lbg.py --snapshot snapshots/latest            # fetch (robots.txt-checked)
    python game8_lbg.py --snapshot snapshots/latest --html DIR # parse pages saved from a browser
"""

import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

import bowgun_rules
from kiranico_schema import field

GAME8_ORIGIN = "https://game8.co"
INDEX_URL = GAME8_ORIGIN + "/games/Monster-Hunter-Rise/archives/324830"
FAMILY_PATH = re.compile(r"/games/Monster-Hunter-Rise/archives/(\d+)")
USER_AGENT = "MHRdataGame8Supplement/1.0 (personal research; user-run; sequential requests)"
SEPARATOR = re.compile(r"\s*[｜|]\s*")
SHOT_TYPES = {"MS": "Moving Shot", "MR": "Moving Reload", "AR": "Auto Reload", "RF": "Rapid Fire",
              "SR": "Single Reload"}
SCOPE_NOTE = "Game8 is approved for Light Bowgun Rapid Fire data only; Kiranico values are not overwritten."


def text(node):
    return " ".join(node.get_text(" ", strip=True).split()) if node is not None else ""


def normalize(name):
    name = name.casefold().replace("l. bowgun", "light bowgun").replace("’", "'")
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def parse_index(html, url=INDEX_URL):
    """Return ``{family_url: [weapon names]}`` from the Light Bowgun tree index."""
    soup = BeautifulSoup(html, "html.parser")
    families = {}
    for heading in soup.select("h3"):
        if not text(heading).endswith(" Tree"):
            continue
        table = heading.find_next("table")
        if table is None or table.find_previous("h3") is not heading:
            continue
        for anchor in table.select("a.a-link[href]"):
            target = urljoin(url, anchor["href"]).split("#")[0]
            if urlsplit(target).netloc == "game8.co" and FAMILY_PATH.fullmatch(urlsplit(target).path):
                names = families.setdefault(target, [])
                if text(anchor) and text(anchor) not in names:
                    names.append(text(anchor))
    return families


def is_ammo_header(cells):
    labels = [text(cell) for cell in cells]
    return (len(labels) >= 3 and labels[0] == "Ammo" and "Shot Type" in labels
            and any(label.replace(" ", "").startswith("Capacity｜Recoil｜Reload")
                    or label.replace(" ", "").startswith("Capacity|Recoil|Reload") for label in labels))


def parse_ammo_cells(cells, warnings, context):
    entries = []
    for start in range(0, len(cells) - len(cells) % 3, 3):
        name_cell, stats_cell, shot_cell = cells[start:start + 3]
        name = text(name_cell)
        if not name or name in {"-", "―", "—"}:
            continue
        parts = SEPARATOR.split(text(stats_cell))
        if len(parts) != 3:
            warnings.append(f"{context}: unexpected Capacity｜Recoil｜Reload text for {name}: {text(stats_cell)}")
            continue
        capacity = int(parts[0]) if parts[0].isdigit() else None
        codes = [code for code in re.findall(r"[A-Z]{2}", text(shot_cell)) if code in SHOT_TYPES]
        rapid = "RF" in codes or "rapid fire" in text(shot_cell).casefold()
        entries.append({"name": name, "capacity": capacity, "recoil": parts[1] or None,
                        "reload": parts[2] or None, "shot_types": codes, "rapid_fire": rapid})
    if len(cells) % 3:
        warnings.append(f"{context}: ammo row with {len(cells)} cells is not a multiple of 3")
    return entries


def parse_family(html, url, weapon_names):
    """Parse a family page into ``[{game8_name, page_url, ammo}]`` plus warnings."""
    soup = BeautifulSoup(html, "html.parser")
    names = sorted(weapon_names, key=len, reverse=True)
    weapons, warnings = [], []
    for table in soup.select("table"):
        rows = table.find_all("tr")
        if not rows or not is_ammo_header(rows[0].find_all(["th", "td"])):
            continue
        heading = table.find_previous(["h2", "h3", "h4"])
        label = text(heading)
        name = next((candidate for candidate in names if normalize(candidate) in normalize(label)), None)
        if name is None:
            warnings.append(f"{url}: ammo table under heading '{label}' matches no family weapon")
            continue
        ammo = []
        for row in rows[1:]:
            ammo.extend(parse_ammo_cells(row.find_all(["td", "th"]), warnings, f"{url} {name}"))
        weapons.append({"game8_name": name, "page_url": url, "ammo": ammo})
    if not weapons:
        warnings.append(f"{url}: no Ammo｜Capacity｜Recoil｜Reload｜Shot Type table found")
    return weapons, warnings


def build_supplement(index_html, family_pages, index_url=INDEX_URL):
    """``family_pages`` maps family URL to ``(html, fetched_at)``."""
    families = parse_index(index_html, index_url)
    weapons, warnings, pages = [], [], []
    for url, names in sorted(families.items()):
        if url not in family_pages:
            warnings.append(f"{url}: family page not available")
            continue
        html, fetched_at = family_pages[url]
        pages.append({"url": url, "sha256": hashlib.sha256(html).hexdigest(), "fetched_at": fetched_at})
        parsed, page_warnings = parse_family(html, url, names)
        weapons.extend(parsed)
        warnings.extend(page_warnings)
    return {"schema_version": 1, "source": "game8", "approved_scope": SCOPE_NOTE,
            "index_url": index_url, "created_at": datetime.now(timezone.utc).isoformat(),
            "pages": pages, "weapons": weapons, "warnings": warnings}


def ammo_key(name):
    return normalize(name).replace("ammo ", "").replace(" ammo", "")


def apply_supplement(weapons, supplement):
    """Merge Game8 Rapid Fire data into Light Bowgun records in place; return a summary."""
    light = [record for record in weapons
             if (record.get("fields", {}).get("weapon_type") or {}).get("value") == "Light Bowgun"]
    exact, loose = {}, {}
    for record in light:
        name = record["fields"]["name"]["value"]
        exact.setdefault(name, []).append(record)
        loose.setdefault(normalize(name), []).append(record)
    summary = {"matched": 0, "unmatched": [], "ambiguous": [], "capacity_mismatches": 0,
               "rapid_fire_weapons": 0, "source_warnings": len(supplement.get("warnings", []))}
    for entry in supplement.get("weapons", []):
        candidates = exact.get(entry["game8_name"]) or loose.get(normalize(entry["game8_name"]), [])
        if len(candidates) != 1:
            summary["ambiguous" if candidates else "unmatched"].append(entry["game8_name"])
            continue
        record = candidates[0]
        fields = record["fields"]
        evidence = {"game8_name": entry["game8_name"], "page_url": entry["page_url"]}
        rapid = [ammo["name"] for ammo in entry["ammo"] if ammo["rapid_fire"]]
        fields["rapid_fire_ammo"] = field(rapid, "supplemental", "game8", evidence=evidence, note=SCOPE_NOTE)
        fields["ammo_handling"] = field([
            {**ammo, "recoil_lookup": bowgun_rules.lookup("recoil", ammo["recoil"], "per_ammo")
             if ammo["recoil"] else None,
             "reload_lookup": bowgun_rules.lookup("reload", ammo["reload"], "per_ammo")
             if ammo["reload"] else None} for ammo in entry["ammo"]],
            "supplemental", "game8", evidence=evidence, note=SCOPE_NOTE)
        capacities = {ammo_key(ammo["name"]): ammo["capacity"]
                      for ammo in (fields.get("ammo") or {}).get("value") or []}
        for ammo in entry["ammo"]:
            known = capacities.get(ammo_key(ammo["name"]))
            if known is not None and ammo["capacity"] is not None and known != ammo["capacity"]:
                record.setdefault("warnings", []).append(
                    f"Game8 capacity {ammo['capacity']} for {ammo['name']} differs from Kiranico {known}; "
                    "Kiranico kept")
                summary["capacity_mismatches"] += 1
        summary["matched"] += 1
        summary["rapid_fire_weapons"] += bool(rapid)
    return summary


class Game8Client:
    def __init__(self, cache_directory, delay=2.0):
        import requests
        self.cache = Path(cache_directory)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.delay = max(2.0, delay)
        self.last = 0.0
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.robots = None

    def get(self, url):
        pause = self.delay - (time.monotonic() - self.last)
        if pause > 0:
            time.sleep(pause)
        self.last = time.monotonic()
        response = self.session.get(url, timeout=(15, 60))
        response.raise_for_status()
        return response

    def check_robots(self):
        rules = RobotFileParser()
        rules.parse(self.get(GAME8_ORIGIN + "/robots.txt").text.splitlines())
        if not rules.can_fetch(USER_AGENT, INDEX_URL):
            raise PermissionError("game8.co robots.txt disallows this client; use --html with saved pages")
        self.delay = max(self.delay, rules.crawl_delay(USER_AGENT) or 0)
        self.robots = rules

    def fetch(self, url):
        if self.robots is None or not self.robots.can_fetch(USER_AGENT, url):
            raise PermissionError(f"robots.txt disallows {url}")
        path = self.cache / (hashlib.sha256(url.encode("utf-8")).hexdigest() + ".html")
        if path.exists():
            return path.read_bytes(), datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        content = self.get(url).content
        path.write_bytes(content)
        return content, datetime.now(timezone.utc).isoformat()


def load_saved_pages(directory):
    """Read browser-saved pages; each file's canonical link or og:url identifies its URL."""
    pages = {}
    for path in sorted(Path(directory).glob("*.htm*")):
        html = path.read_bytes()
        soup = BeautifulSoup(html, "html.parser")
        link = soup.find("link", rel="canonical") or soup.find("meta", property="og:url")
        url = (link.get("href") or link.get("content")) if link else None
        if url:
            pages[url.split("#")[0]] = (html, datetime.fromtimestamp(path.stat().st_mtime,
                                                                     timezone.utc).isoformat())
    return pages


def main():
    parser = argparse.ArgumentParser(description="Build the opt-in Game8 Light Bowgun Rapid Fire supplement.")
    parser.add_argument("--snapshot", type=Path, default=Path(__file__).resolve().parent / "snapshots" / "latest")
    parser.add_argument("--html", type=Path, help="Directory of Game8 pages saved from a browser (no network)")
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between requests (at least 2)")
    args = parser.parse_args()
    output = args.snapshot / "supplemental"
    output.mkdir(parents=True, exist_ok=True)
    if args.html:
        pages = load_saved_pages(args.html)
        if INDEX_URL not in pages:
            parser.exit(1, f"Saved pages must include the index {INDEX_URL}\n")
        index_html = pages[INDEX_URL][0]
    else:
        client = Game8Client(output / "game8", args.delay)
        try:
            client.check_robots()
            index_html, _ = client.fetch(INDEX_URL)
            pages = {url: client.fetch(url) for url in sorted(parse_index(index_html))}
        except Exception as error:  # report network/robots failures without a traceback
            parser.exit(1, f"Game8 download failed: {error}\n")
    supplement = build_supplement(index_html, pages)
    path = output / "game8-lbg.json"
    path.write_text(json.dumps(supplement, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {path} ({len(supplement['weapons'])} weapons, {len(supplement['warnings'])} warnings). "
          "Re-run download_kiranico.py with the same --snapshot to merge it.")


if __name__ == "__main__":
    main()
