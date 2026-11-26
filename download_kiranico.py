import argparse
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

import bowgun_rules
import kiranico_listing
import kiranico_schema as schema
from kiranico_schema import BASE_URL, field


CATEGORIES = {slug: entry["name"] for slug, entry in schema.CATEGORIES.items() if entry["kind"] == "detail"}
LISTING_CATEGORIES = dict(schema.CATEGORY_NAMES)
USER_AGENT = "KiranicoSnapshot/1.0 (personal research; sequential requests)"
SCHEMA_VERSION = 2
PARSER_VERSION = 2
MODES = ("pilot", "listings", "full")


def canonical_url(value, base=BASE_URL):
    parts = urlsplit(urljoin(base, value))
    if parts.scheme != "https" or parts.netloc != "mhrise.kiranico.com":
        raise ValueError(f"Outside the allowed HTTPS origin: {value}")
    if parts.username or parts.password:
        raise ValueError("Credentials in URLs are not supported")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") or "/",
                       urlencode(sorted(parse_qsl(parts.query))), ""))


def identity(url):
    match = schema.DETAIL_PATH.fullmatch(urlsplit(canonical_url(url)).path)
    if not match:
        raise ValueError(f"Not a supported English detail page: {url}")
    return CATEGORIES[match[1]], match[2]


def page_links(soup, base):
    links = {}
    for anchor in soup.select("a[href]"):
        try:
            url = canonical_url(anchor["href"], base)
        except ValueError:
            continue
        links.setdefault(url, anchor.get_text(" ", strip=True))
    return links


def armor_slot_levels(slot_cells):
    if len(slot_cells) != 1:
        return None, "Expected one armor Overview slot cell next to the Defense value."
    cell = slot_cells[0]
    if cell.get_text(strip=True) or any(tag.name != "img" for tag in cell.find_all(True)):
        return None, "Unrecognized content in the armor slot cell."
    levels = []
    for image in cell.find_all("img"):
        src = image.get("src", "")
        match = re.fullmatch(r"(?:[^?#]*/)?deco([1-4])\.png(?:[?#].*)?", src)
        if not match:
            return None, f"Unrecognized armor slot icon: {src or '(missing src)'}"
        levels.append(int(match[1]))
    return levels, None


def parse_detail(html, url):
    url = canonical_url(url)
    category, record_id = identity(url)
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("article")
    if article is None or article.find("h1") is None:
        raise ValueError(f"Missing article/title: {url}")
    title = article.find("h1").get_text(" ", strip=True)
    if not title or title.lower() in {"not found", "access denied", "error"}:
        raise ValueError(f"Invalid detail title: {url}")
    description = soup.find("meta", attrs={"name": "description"})
    record = {"id": record_id, "name": title, "source_url": url, "language": "en",
              "description": description.get("content") if description else None,
              "tables": []}
    if article.select("svg rect, svg path, canvas"):
        record["visual_data_note"] = "Source contains graphics; graphical values are not numerically decoded."
    version = next((heading.get_text(" ", strip=True) for heading in soup.select("h2")
                    if re.fullmatch(r"Database Ver[\d.]+", heading.get_text(" ", strip=True))), None)
    record["source_version"] = version.removeprefix("Database Ver") if version else None
    section = "Overview"
    slot_cells = []
    rampage_skills = {}
    for element in article.find_all(["h1", "h2", "h3", "h4", "table"]):
        if element.name != "table":
            section = "Overview" if element.name == "h1" else element.get_text(" ", strip=True)
            continue
        table = {"section": section, "headers": [], "rows": []}
        if element.find_parent("table") is not None:
            table["nested"] = True
        for row in element.find_all("tr"):
            if row.find_parent("table") is not element:
                continue
            source_cells = row.find_all(["td", "th"], recursive=False)
            if (category == "Armors" and section == "Overview" and len(source_cells) == 8
                    and all(cell.name == "td" for cell in source_cells)
                    and re.fullmatch(r"\d+\s+Defense", source_cells[1].get_text(" ", strip=True))):
                slot_cells.append(source_cells[0])
            cells = []
            for cell in source_cells:
                annotations = sorted({value for tag in cell.select("[title], [aria-label], img, use")
                                      for value in (tag.get("title"), tag.get("aria-label"),
                                                    tag.get("alt"), tag.get("src"),
                                                    tag.get("href"), tag.get("xlink:href"))
                                      if value})
                annotations.extend(f"{tag['data-key']}={tag['data-value']}"
                                   for tag in cell.select("[data-key][data-value]"))
                links = []
                for anchor in cell.select("a[href]"):
                    try:
                        target_url = canonical_url(anchor["href"], url)
                    except ValueError:
                        continue
                    link = {"name": anchor.get_text(" ", strip=True), "url": target_url}
                    siblings = page_links(anchor.parent, url)
                    if sum("/data/skills/" in sibling for sibling in siblings) == 1:
                        level = re.search(r"\bLv\s*(\d+)\b", anchor.parent.get_text(" ", strip=True))
                        if level:
                            link["equipped_level"] = int(level[1])
                    links.append(link)
                cells.append({"text": cell.get_text(" ", strip=True), "links": links,
                              "annotations": annotations})
            if not cells:
                continue
            if row.find_parent("thead"):
                table["headers"] = [cell["text"] for cell in cells]
            else:
                table["rows"].append(cells)
        record["tables"].append(table)
        relation = {"Forging Materials": "forging_materials",
                    "Upgrade Materials": "upgrade_materials",
                    "Required Materials": "materials",
                    "Materials": "materials"}.get(section)
        if relation:
            record[relation] = []
        for cells in table["rows"]:
            row_text = " ".join(cell["text"] for cell in cells)
            if category == "Skills" and section == "Overview" and len(cells) >= 2:
                level = re.fullmatch(r"Lv\s*(\d+)", cells[0]["text"])
                if level:
                    record["lv" + level[1]] = " ".join(cell["text"] for cell in cells[1:])
            for cell in cells:
                for link in cell["links"]:
                    reference_match = schema.REFERENCE_PATH.fullmatch(urlsplit(link["url"]).path)
                    if category == "Weapons" and reference_match and section == "Overview":
                        rampage_skills.setdefault(reference_match[2], {"skill": {
                            "id": reference_match[2], "name": link["name"], "link": link["url"]}})
                    try:
                        target, target_id = identity(link["url"])
                    except ValueError:
                        continue
                    reference = {"id": target_id, "name": link["name"], "link": link["url"]}
                    if relation and target == "Items":
                        quantities = re.findall(r"(?:^|\s)(x\d+)(?=\s|$)", cell["text"])
                        amount = quantities[0] if len(quantities) == 1 else next(
                            (part["text"] for part in cells if re.fullmatch(r"x\d+", part["text"])), None)
                        if amount is None:
                            raise ValueError(f"Missing material amount: {url}/{section}")
                        record[relation].append({"item": reference, "amount": amount})
                    if target == "Skills" and category in {"Armors", "Decorations"}:
                        entry = {"skill": reference}
                        if "equipped_level" in link:
                            entry["equipped_level"] = link["equipped_level"]
                        elif sum("/data/skills/" in other["url"] for part in cells for other in part["links"]) == 1:
                            level = re.search(r"\bLv\s*(\d+)\b", row_text)
                            if level:
                                entry["equipped_level"] = int(level[1])
                        record.setdefault("skills", []).append(entry)
    if not record["tables"]:
        raise ValueError(f"No data tables found: {url}")
    if category == "Armors":
        record["decoration_slot_levels"], note = armor_slot_levels(slot_cells)
        if note:
            record["decoration_slot_note"] = note
    if category == "Weapons":
        record["rampage_skills"] = list(rampage_skills.values())
    return category, record


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class DownloadError(RuntimeError):
    pass


class SnapshotClient:
    def __init__(self, directory, delay=2.0):
        if delay < 2:
            raise ValueError("Download delay must be at least 2 seconds")
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "raw").mkdir(exist_ok=True)
        self.checkpoint_directory = self.directory / "checkpoint"
        self.page_state_directory = self.checkpoint_directory / "pages"
        self.record_directory = self.checkpoint_directory / "records"
        self.page_state_directory.mkdir(parents=True, exist_ok=True)
        self.record_directory.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.directory / "download-manifest.json"
        self.manifest = (json.loads(self.manifest_path.read_text(encoding="utf-8"))
                         if self.manifest_path.exists() else
                         {"schema_version": SCHEMA_VERSION, "origin": BASE_URL, "language": "en",
                          "snapshot_id": utc_now(), "pages": {}, "complete": False})
        if self.manifest.get("origin") != BASE_URL or self.manifest.get("schema_version") not in {1, SCHEMA_VERSION}:
            raise ValueError("Incompatible snapshot manifest")
        if self.manifest["schema_version"] == 1:
            # v1 stored discovery for five categories only; rediscover from the verified raw cache.
            for key in ("discovered", "listing_urls", "coverage"):
                self.manifest.pop(key, None)
            self.manifest.update({"schema_version": SCHEMA_VERSION, "migrated_from_schema_version": 1,
                                  "complete": False})
        self.load_page_states()
        self.delay = delay
        self.last_request = 0.0
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.robots = None

    def save(self):
        write_json(self.manifest_path, self.manifest)

    def page_state_path(self, url):
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.page_state_directory / (key + ".json")

    def load_page_states(self):
        for path in self.page_state_directory.glob("*.json"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                url, metadata = state["url"], state["metadata"]
                if isinstance(metadata, dict) and path == self.page_state_path(url):
                    self.manifest["pages"][url] = metadata
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def save_page_state(self, url, metadata):
        self.manifest["pages"][url] = metadata
        write_json(self.page_state_path(url), {"url": url, "metadata": metadata})

    def merge_page_states(self):
        self.load_page_states()

    def cached_raw(self, url):
        url = canonical_url(url)
        cached = self.manifest["pages"].get(url, {})
        if cached.get("status") != "success":
            return None
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        path = self.directory / "raw" / (key + ".html")
        if not path.exists():
            return None
        raw = path.read_bytes()
        return raw if hashlib.sha256(raw).hexdigest() == cached.get("sha256") else None

    def reject(self, url, error):
        metadata = self.manifest["pages"].setdefault(url, {})
        metadata["status"] = "invalid"
        metadata["error"] = str(error)
        self.save_page_state(url, metadata)
        raise DownloadError(str(error))

    def close(self):
        self.session.close()

    def request(self, url):
        url = canonical_url(url)
        for redirect in range(4):
            if self.robots is not None and not self.robots.can_fetch(USER_AGENT, url):
                raise DownloadError(f"robots.txt disallows {url}")
            for attempt in range(3):
                pause = max(0, self.delay - (time.monotonic() - self.last_request))
                if pause:
                    time.sleep(pause)
                self.last_request = time.monotonic()
                try:
                    response = self.session.get(url, timeout=(15, 60), allow_redirects=False)
                except requests.RequestException as error:
                    if attempt == 2:
                        raise DownloadError(f"Request failed: {url}: {error}") from error
                    time.sleep(self.delay * (2 ** attempt))
                    continue
                if response.status_code == 429 or 500 <= response.status_code < 600:
                    if attempt == 2:
                        raise DownloadError(f"Persistent HTTP {response.status_code}: {url}")
                    retry = response.headers.get("Retry-After")
                    try:
                        wait = float(retry) if retry else self.delay * (2 ** attempt)
                    except ValueError:
                        try:
                            wait = (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds()
                        except (TypeError, ValueError, OverflowError) as error:
                            raise DownloadError(f"Invalid Retry-After: {retry}") from error
                    if wait > 300:
                        raise DownloadError(f"Server requests a long pause ({wait}s); resume later")
                    time.sleep(max(self.delay, wait))
                    continue
                break
            if response.status_code in {301, 302, 303, 307, 308}:
                try:
                    url = canonical_url(response.headers.get("Location", ""), url)
                except ValueError as error:
                    raise DownloadError("Refusing off-origin redirect") from error
                continue
            if response.status_code != 200:
                raise DownloadError(f"HTTP {response.status_code}: {url}")
            return response
        raise DownloadError(f"Too many redirects: {url}")

    def check_robots(self):
        response = self.request(BASE_URL + "/robots.txt")
        rules = RobotFileParser()
        rules.parse(response.text.splitlines())
        self.robots = rules
        crawl_delay = rules.crawl_delay(USER_AGENT) or rules.crawl_delay("*")
        rate = rules.request_rate(USER_AGENT) or rules.request_rate("*")
        self.delay = max(self.delay, crawl_delay or 0,
                         rate.seconds / rate.requests if rate and rate.requests else 0)
        self.manifest["robots"] = {"checked_at": utc_now(), "text": response.text,
                                   "effective_delay": self.delay}
        self.save()

    def fetch(self, url, refresh=False):
        url = canonical_url(url)
        if self.robots is None:
            raise DownloadError("Check robots.txt before downloading pages")
        if not self.robots.can_fetch(USER_AGENT, url):
            raise DownloadError(f"robots.txt disallows {url}")
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        path = self.directory / "raw" / (key + ".html")
        cached = None if refresh else self.cached_raw(url)
        if cached is not None:
            return cached
        try:
            response = self.request(url)
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type:
                raise DownloadError(f"Not an HTML page: {url} ({content_type})")
            if canonical_url(response.url) != url:
                raise DownloadError(f"Unexpected final page URL: {response.url}")
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(response.content)
            temporary.replace(path)
            self.save_page_state(url, {
                "status": "success", "http_status": response.status_code,
                "final_url": response.url, "content_type": content_type,
                "fetched_at": utc_now(), "sha256": hashlib.sha256(response.content).hexdigest(),
                "cache_file": "raw/" + path.name,
                "etag": response.headers.get("ETag"),
                "last_modified": response.headers.get("Last-Modified"),
            })
            return response.content
        except (DownloadError, ValueError) as error:
            self.save_page_state(url, {"status": "failed", "error": str(error),
                                       "attempted_at": utc_now()})
            raise


def listing_category(url):
    parts = urlsplit(url)
    match = schema.LISTING_PATH.fullmatch(parts.path)
    query = parse_qsl(parts.query)
    if not match or any(key not in {"view", "page"} for key, _ in query):
        return None
    if schema.CATEGORIES[match[1]]["views_required"] and not dict(query).get("view"):
        return None
    return LISTING_CATEGORIES[match[1]]


def unsupported_route(url):
    path = urlsplit(url).path
    return next((route["route"] for route in schema.UNSUPPORTED_ROUTES if route["route"] == path), None)


def valid_discovery_state(state):
    return (state.get("schema_version") == SCHEMA_VERSION
            and all(isinstance(state["details"].get(category), dict) for category in CATEGORIES.values())
            and isinstance(state["rows"], dict) and isinstance(state["unsupported"], dict))


def load_discovery_state(checkpoint_path):
    try:
        state = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        return state if valid_discovery_state(state) else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def discover(client, refresh=False):
    checkpoint_path = client.checkpoint_directory / "discovery.json"
    state = load_discovery_state(checkpoint_path)
    # An interrupted --refresh is resumed (with or without --refresh) until it completes, so
    # pages already re-downloaded in that refresh are not fetched again and stale manifest
    # discovery is never mixed with refreshed pages.
    resuming_refresh = bool(state and state.get("refresh") and not state.get("complete"))
    refresh = refresh or resuming_refresh
    existing = client.manifest.get("discovered")
    if (not refresh and isinstance(existing, dict) and isinstance(client.manifest.get("listing_urls"), list)
            and all(isinstance(existing.get(category), dict) for category in CATEGORIES.values())):
        return existing
    if refresh and not resuming_refresh:
        state = None
        for key in ("discovered", "listing_urls", "listing_rows"):
            client.manifest.pop(key, None)
        client.save()
    if state:
        pending, visited = set(state["pending"]), set(state["visited"])
        details, rows = state["details"], state["rows"]
        unsupported = {route: set(urls) for route, urls in state["unsupported"].items()}
    else:
        pending, visited = {BASE_URL + "/data/skills"}, set()
        details = {category: {} for category in CATEGORIES.values()}
        rows, unsupported = {}, {}

    def save_checkpoint(complete=False):
        write_json(checkpoint_path, {
            "schema_version": SCHEMA_VERSION, "pending": sorted(pending), "visited": sorted(visited),
            "details": details, "rows": rows,
            "unsupported": {route: sorted(urls) for route, urls in unsupported.items()},
            "complete": complete, "refresh": refresh,
        })

    if state is None:
        save_checkpoint()
    while pending:
        url = min(pending)
        html = client.fetch(url, refresh=refresh)
        soup = BeautifulSoup(html, "html.parser")
        article = soup.find("article")
        if article is None or article.find("h1") is None:
            client.reject(url, f"Missing listing article/title: {url}")
        visited.add(url)
        for link in page_links(soup, url):
            if listing_category(link):
                if link not in visited:
                    pending.add(link)
            elif unsupported_route(link):
                unsupported.setdefault(unsupported_route(link), set()).add(link)
        category = listing_category(url)
        if category in schema.LISTING_ONLY_CATEGORIES:
            _, records, errors = kiranico_listing.parse_listing(category, html, url)
            count = len(records) + len(errors)
        else:
            count = 0
            for link, name in page_links(article, url).items():
                try:
                    target, _ = identity(link)
                except ValueError:
                    continue
                if target != category:
                    continue
                item = details[category].setdefault(link, {"name": name, "listing_urls": []})
                if url not in item["listing_urls"]:
                    item["listing_urls"].append(url)
                count += 1
        if count == 0:
            client.reject(url, f"Listing contains no supported rows: {url}")
        rows[url] = count
        print(f"Discovered {count} {category} rows: {url}", flush=True)
        pending.remove(url)
        save_checkpoint()
        if len(visited) + len(pending) > 200:
            raise DownloadError("Listing discovery exceeded 200 pages; inspect pagination")
    if any(not details[category] for category in schema.CORE_CATEGORIES):
        raise DownloadError("Discovery did not find all five core categories")
    client.manifest["listing_urls"] = sorted(visited)
    client.manifest["listing_rows"] = rows
    client.manifest["unsupported_routes"] = [
        {**route, "status": "not_supported", "reason": schema.UNSUPPORTED_REASON,
         "seen_urls": sorted(unsupported.get(route["route"], ()))}
        for route in schema.UNSUPPORTED_ROUTES]
    client.manifest["discovered"] = details
    save_checkpoint(complete=True)
    client.save()
    return details


def parse_listings(client):
    """Re-parse every verified cached listing page into typed rows keyed by detail URL."""
    rows = {category: {} for category in schema.ALL_CATEGORIES}
    pages = {category: [] for category in schema.ALL_CATEGORIES}
    errors = {category: [] for category in schema.ALL_CATEGORIES}
    for url in client.manifest["listing_urls"]:
        category = listing_category(url)
        raw = client.fetch(url)
        try:
            meta, records, row_errors = kiranico_listing.parse_listing(category, raw, url)
        except ValueError as error:
            client.reject(url, error)
        pages[category].append({**meta, "rows": len(records) + len(row_errors)})
        errors[category].extend(row_errors)
        for entry in records:
            key = entry["key"]
            if key.startswith(("https:", "http:", "/")):
                try:
                    key = canonical_url(key)
                except ValueError as error:
                    errors[category].append(f"{url}: {error}")
                    continue
            row = rows[category].get(key)
            if row is None:
                rows[category][key] = {"id": entry["id"], "fields": entry["fields"],
                                       "warnings": list(entry["warnings"]), "listing_urls": [url],
                                       "source_version": meta["source_version"]}
            else:
                row["listing_urls"].append(url)
                row["warnings"].append(f"Also listed on {url}; the first row is kept")
    return rows, pages, errors


def add_source_groups(category, record, listing_urls):
    record["source_groups"] = list(listing_urls)
    views = {dict(parse_qsl(urlsplit(group).query)).get("view") for group in listing_urls}
    if schema.CATEGORIES[schema.SLUGS[category]]["views_required"] and len(views) == 1 and None not in views:
        record["source_group"] = "view-" + next(iter(views))


def listing_record(category, key, row, snapshot_id):
    fields = row["fields"]
    record = {"id": row["id"], "name": (fields.get("name") or {}).get("value"),
              "source_url": key if key.startswith("https://") else row["listing_urls"][0],
              "language": "en", "source_version": row["source_version"], "snapshot_id": snapshot_id,
              "fields": fields, "warnings": row["warnings"],
              "detail_status": "not_fetched" if category in CATEGORIES.values() else "not_applicable"}
    if not key.startswith("https://"):
        record["source_record_key"] = key
    add_source_groups(category, record, row["listing_urls"])
    return record


def merged_record(category, record, row):
    """Combine a parsed detail page with its typed listing row (detail values fill listing gaps)."""
    record = dict(record)
    fields = dict(row["fields"]) if row else {}
    warnings = list(row["warnings"]) if row else [
        "No Kiranico listing row; reached through a detail-page reference."]
    if category == "Skills" and (fields.get("max_level") or {}).get("availability") != "derived":
        fields.update(kiranico_listing.detail_skill_levels(record))
    if category == "Weapons" and "rampage_skills" in record:
        fields["rampage_skills"] = field([entry["skill"] for entry in record["rampage_skills"]],
                                         "direct", "kiranico_detail")
    record.update({"fields": fields, "warnings": warnings, "detail_status": "parsed"})
    return record


def record_checkpoint_path(client, url):
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return client.record_directory / (key + ".json")


def load_record_checkpoint(client, url, raw):
    path = record_checkpoint_path(client, url)
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
        if (checkpoint.get("source_url") != url or checkpoint.get("parser_version") != PARSER_VERSION
                or checkpoint.get("source_sha256") != hashlib.sha256(raw).hexdigest()):
            return None
        category, record_id = identity(url)
        record = checkpoint["record"]
        if record.get("id") != record_id or record.get("source_url") != url:
            return None
        if category == "Armors" and "decoration_slot_levels" not in record:
            return None
        return category, record
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def save_record_checkpoint(client, url, raw, record):
    write_json(record_checkpoint_path(client, url), {
        "source_url": url, "source_sha256": hashlib.sha256(raw).hexdigest(),
        "parser_version": PARSER_VERSION, "record": record,
    })


def fetch_details(client, details, full, limit):
    parsed = {category: {} for category in CATEGORIES.values()}
    selected = {url for entries in details.values() for url in sorted(entries)[:None if full else limit]}
    selected.add(BASE_URL + "/data/items/1665412677")
    if not full:
        selected.update(url for url, entry in details["Skills"].items() if entry["name"] == "Attack Boost")
    queue = set(selected)
    processed = set()
    while queue:
        url = min(queue)
        queue.remove(url)
        if url in processed:
            continue
        raw = client.cached_raw(url)
        cached_record = load_record_checkpoint(client, url, raw) if raw is not None else None
        if cached_record is None:
            raw = client.fetch(url)
            try:
                category, record = parse_detail(raw, url)
            except ValueError as error:
                client.reject(url, error)
            record["fetched_at"] = client.manifest["pages"][url]["fetched_at"]
            record["snapshot_id"] = client.manifest["snapshot_id"]
            save_record_checkpoint(client, url, raw, record)
        else:
            category, record = cached_record
        parsed[category][url] = record
        processed.add(url)
        for relation, nested in (("forging_materials", "item"), ("upgrade_materials", "item"),
                                 ("materials", "item"), ("skills", "skill")):
            for entry in record.get(relation, []):
                reference = entry[nested]["link"]
                if reference not in processed:
                    queue.add(reference)
        if len(processed | queue) > 20000:
            raise DownloadError("Reference discovery exceeded 20000 pages")
        print(f"Parsed {category}/{record['id']} ({len(processed)} done, {len(queue)} queued)", flush=True)
    return parsed


def apply_supplements(client, weapons):
    path = client.directory / "supplemental" / "game8-lbg.json"
    if not path.exists():
        client.manifest.pop("supplements", None)
        return
    import game8_lbg
    summary = game8_lbg.apply_supplement(weapons, json.loads(path.read_text(encoding="utf-8")))
    client.manifest["supplements"] = {"game8_lbg": {"file": "supplemental/game8-lbg.json", **summary}}


def build_coverage(mode, details, rows, pages, row_errors, parsed, records):
    scope = {"listings": "none", "pilot": "sample", "full": "all"}[mode]
    coverage, statistics = {}, {}
    for category in schema.ALL_CATEGORIES:
        entries = list(records[category].values())
        is_detail = category in CATEGORIES.values()
        listing_missing = sorted(set(details[category]) - set(rows[category])) if is_detail else []
        missing = (sorted(set(details[category]) - set(parsed[category]))
                   if is_detail and mode != "listings" else listing_missing)
        statistics[category] = schema.field_statistics(category, entries)
        coverage[category] = {
            "kind": "detail" if is_detail else "listing",
            "required": category in schema.CORE_CATEGORIES,
            "listing_pages": len(pages[category]),
            "rows": sum(page["rows"] for page in pages[category]),
            "row_errors": row_errors[category],
            "listing_missing": listing_missing,
            "discovered": len(details[category]) if is_detail else len(rows[category]),
            "parsed": len(entries), "missing": missing,
            "detail_scope": scope if is_detail else "not_applicable",
            "warnings": sum(len(record.get("warnings", [])) for record in entries),
            "contract_violations": sum(value["unexpected"] for value in statistics[category].values()),
        }
    return coverage, statistics


def is_complete(mode, coverage):
    if mode == "pilot":
        return False
    return (all(coverage[category]["parsed"] for category in schema.CORE_CATEGORIES)
            and all(not values["row_errors"] and not values["listing_missing"] and not values["missing"]
                    and not values["contract_violations"] for values in coverage.values()))


def write_structured(client, mode, coverage, statistics, pages, records):
    output = client.directory / "structured"
    output.mkdir(exist_ok=True)
    snapshot_id = client.manifest["snapshot_id"]
    for category in schema.ALL_CATEGORIES:
        path = output / (category + ".json")
        if category not in schema.CORE_CATEGORIES and not pages[category]:
            path.unlink(missing_ok=True)
            continue
        entries = records[category]
        versions = {record["source_version"] for record in entries.values() if record.get("source_version")}
        write_json(path, {
            "schema_version": SCHEMA_VERSION, "category": category, "kind": coverage[category]["kind"],
            "version": next(iter(versions)) if len(versions) == 1 else None,
            "date": snapshot_id, "snapshot_id": snapshot_id, "snapshot_mode": mode,
            "coverage": {**coverage[category], "pages": pages[category]},
            "field_statistics": statistics[category],
            "data": sorted(entries.values(), key=lambda record: (record["id"], record["source_url"])),
        })
    write_json(output / "required-fields.json", schema.required_fields_document())
    write_json(output / "bowgun-rules.json", bowgun_rules.rules_document())


def download_snapshot(directory, full=False, limit=1, delay=2.0, listings=False, refresh=False):
    if limit < 1:
        raise ValueError("Pilot limit must be positive")
    if full and listings:
        raise ValueError("Choose either --full or --listings")
    mode = "full" if full else "listings" if listings else "pilot"
    client = SnapshotClient(directory, delay)
    try:
        client.manifest.update({"complete": False, "run_status": "running", "mode": mode,
                                "stage": "discovery", "parser_version": PARSER_VERSION})
        client.save()
        client.check_robots()
        details = discover(client, refresh)
        client.manifest["stage"] = "listings"
        client.save()
        rows, pages, row_errors = parse_listings(client)
        parsed = {category: {} for category in CATEGORIES.values()}
        if mode != "listings":
            client.manifest["stage"] = "details"
            client.save()
            parsed = fetch_details(client, details, full, limit)
        client.manifest["stage"] = "finalizing"
        client.save()
        snapshot_id = client.manifest["snapshot_id"]
        records = {category: {} for category in schema.ALL_CATEGORIES}
        for category in schema.ALL_CATEGORIES:
            if category in CATEGORIES.values() and mode != "listings":
                for url, record in parsed[category].items():
                    merged = merged_record(category, record, rows[category].get(url))
                    add_source_groups(category, merged, details[category].get(url, {}).get("listing_urls", []))
                    records[category][url] = merged
            else:
                records[category] = {key: listing_record(category, key, row, snapshot_id)
                                     for key, row in rows[category].items()}
        apply_supplements(client, list(records["Weapons"].values()))
        coverage, statistics = build_coverage(mode, details, rows, pages, row_errors, parsed, records)
        write_structured(client, mode, coverage, statistics, pages, records)
        client.merge_page_states()
        client.manifest["coverage"] = coverage
        client.manifest["complete"] = is_complete(mode, coverage)
        client.manifest["run_status"] = "success"
        client.manifest["stage"] = "complete"
        client.manifest.pop("error", None)
        client.manifest.pop("failed_stage", None)
        client.save()
        return client.manifest
    except (DownloadError, ValueError, OSError, KeyboardInterrupt) as error:
        client.merge_page_states()
        client.manifest["run_status"] = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
        client.manifest["failed_stage"] = client.manifest.get("stage")
        client.manifest["stage"] = client.manifest["run_status"]
        client.manifest["error"] = str(error)
        client.save()
        raise
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description="Download an English Kiranico snapshot; pilot by default.")
    parser.add_argument("--snapshot", type=Path, default=Path(__file__).resolve().parent / "snapshots" / "latest")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--full", action="store_true",
                       help="Download every listing and detail page (about 7000 requests; hours)")
    modes.add_argument("--listings", action="store_true",
                       help="Parse every listing page into typed fields (about 30 requests); no detail pages")
    parser.add_argument("--refresh", action="store_true",
                        help="Re-download listing pages instead of reusing the verified raw cache")
    parser.add_argument("--limit-per-category", type=int, default=1, help="Pilot sample size; pilot mode only")
    parser.add_argument("--delay", type=float, default=2.0, help="Minimum seconds between requests (at least 2)")
    args = parser.parse_args()
    try:
        report = download_snapshot(args.snapshot, args.full, args.limit_per_category, args.delay,
                                   listings=args.listings, refresh=args.refresh)
    except (DownloadError, ValueError, OSError) as error:
        parser.exit(1, f"Snapshot failed; fix the issue and resume with the same --snapshot: {error}\n")
    for category, values in report["coverage"].items():
        print(f"{category}: {values['parsed']} records, {values['listing_pages']} listing pages, "
              f"{len(values['row_errors'])} row errors, {len(values['missing'])} missing, "
              f"{values['contract_violations']} contract violations")
    print(f"Snapshot ready ({report['mode']}). Complete coverage: {report['complete']}. "
          f"Structured files: {args.snapshot / 'structured'}")


if __name__ == "__main__":
    main()