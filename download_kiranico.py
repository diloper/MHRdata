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


BASE_URL = "https://mhrise.kiranico.com"
CATEGORIES = {
    "items": "Items", "skills": "Skills", "decorations": "Decorations",
    "armors": "Armors", "weapons": "Weapons",
}
USER_AGENT = "KiranicoSnapshot/1.0 (personal research; sequential requests)"


def canonical_url(value, base=BASE_URL):
    parts = urlsplit(urljoin(base, value))
    if parts.scheme != "https" or parts.netloc != "mhrise.kiranico.com":
        raise ValueError(f"Outside the allowed HTTPS origin: {value}")
    if parts.username or parts.password:
        raise ValueError("Credentials in URLs are not supported")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/") or "/",
                       urlencode(sorted(parse_qsl(parts.query))), ""))


def identity(url):
    match = re.fullmatch(r"/data/(items|skills|decorations|armors|weapons)/(\d+)",
                         urlsplit(canonical_url(url)).path)
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
    for element in article.find_all(["h1", "h2", "h3", "h4", "table"]):
        if element.name != "table":
            section = "Overview" if element.name == "h1" else element.get_text(" ", strip=True)
            continue
        table = {"section": section, "headers": [], "rows": []}
        for row in element.find_all("tr"):
            if row.find_parent("table") is not element:
                continue
            cells = []
            for cell in row.find_all(["td", "th"], recursive=False):
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
                         {"schema_version": 1, "origin": BASE_URL, "language": "en",
                          "snapshot_id": utc_now(), "pages": {}, "complete": False})
        if self.manifest.get("origin") != BASE_URL or self.manifest.get("schema_version") != 1:
            raise ValueError("Incompatible snapshot manifest")
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

    def fetch(self, url):
        url = canonical_url(url)
        if self.robots is None:
            raise DownloadError("Check robots.txt before downloading pages")
        if not self.robots.can_fetch(USER_AGENT, url):
            raise DownloadError(f"robots.txt disallows {url}")
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        path = self.directory / "raw" / (key + ".html")
        cached = self.cached_raw(url)
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
    match = re.fullmatch(r"/data/(items|skills|decorations|armors|weapons)", parts.path)
    if not match or any(key not in {"view", "page"} for key, _ in parse_qsl(parts.query)):
        return None
    category = CATEGORIES[match[1]]
    if category in {"Items", "Armors", "Weapons"} and not dict(parse_qsl(parts.query)).get("view"):
        return None
    return category


def discover(client):
    checkpoint_path = client.checkpoint_directory / "discovery.json"
    existing = client.manifest.get("discovered")
    listing_urls = client.manifest.get("listing_urls")
    if (isinstance(existing, dict) and isinstance(listing_urls, list)
            and all(isinstance(existing.get(category), dict) for category in CATEGORIES.values())):
        return existing
    try:
        state = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if state.get("schema_version") != 1:
            raise ValueError("Unsupported discovery checkpoint")
        pending = set(state["pending"])
        visited = set(state["visited"])
        details = state["details"]
        if not all(isinstance(details.get(category), dict) for category in CATEGORIES.values()):
            raise ValueError("Invalid discovery checkpoint")
    except FileNotFoundError:
        pending = {BASE_URL + "/data/skills"}
        visited = set()
        details = {category: {} for category in CATEGORIES.values()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pending = {BASE_URL + "/data/skills"}
        visited = set()
        details = {category: {} for category in CATEGORIES.values()}

    def save_checkpoint(complete=False):
        write_json(checkpoint_path, {
            "schema_version": 1, "pending": sorted(pending), "visited": sorted(visited),
            "details": details, "complete": complete,
        })

    if not checkpoint_path.exists():
        save_checkpoint()
    while pending:
        url = min(pending)
        html = client.fetch(url)
        soup = BeautifulSoup(html, "html.parser")
        article = soup.find("article")
        if article is None or article.find("h1") is None:
            client.reject(url, f"Missing listing article/title: {url}")
        visited.add(url)
        for link in page_links(soup, url):
            if listing_category(link) and link not in visited:
                pending.add(link)
        count = 0
        for link, name in page_links(article, url).items():
            try:
                category, _ = identity(link)
            except ValueError:
                continue
            if category != listing_category(url):
                continue
            item = details[category].setdefault(link, {"name": name, "listing_urls": []})
            item["listing_urls"].append(url)
            count += 1
        if count == 0:
            client.reject(url, f"Listing contains no supported detail links: {url}")
        print(f"Discovered {count} records: {url}", flush=True)
        pending.remove(url)
        save_checkpoint()
        if len(visited) + len(pending) > 200:
            raise DownloadError("Listing discovery exceeded 200 pages; inspect pagination")
    if any(not records for records in details.values()):
        raise DownloadError("Discovery did not find all five categories")
    client.manifest["listing_urls"] = sorted(visited)
    client.manifest["discovered"] = details
    save_checkpoint(complete=True)
    client.save()
    return details


def record_checkpoint_path(client, url):
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return client.record_directory / (key + ".json")


def load_record_checkpoint(client, url, raw):
    path = record_checkpoint_path(client, url)
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
        if (checkpoint.get("source_url") != url
                or checkpoint.get("source_sha256") != hashlib.sha256(raw).hexdigest()):
            return None
        category, record_id = identity(url)
        record = checkpoint["record"]
        if record.get("id") != record_id or record.get("source_url") != url:
            return None
        return category, record
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def save_record_checkpoint(client, url, raw, record):
    write_json(record_checkpoint_path(client, url), {
        "source_url": url, "source_sha256": hashlib.sha256(raw).hexdigest(),
        "record": record,
    })


def download_snapshot(directory, full=False, limit=1, delay=2.0):
    if limit < 1:
        raise ValueError("Pilot limit must be positive")
    client = SnapshotClient(directory, delay)
    try:
        client.manifest["complete"] = False
        client.manifest["run_status"] = "running"
        client.manifest["mode"] = "full" if full else "pilot"
        client.manifest["stage"] = "discovery"
        client.save()
        client.check_robots()
        details = discover(client)
        client.manifest["stage"] = "details"
        client.save()
        records = {category: {} for category in CATEGORIES.values()}
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
            record = dict(record)
            record["source_groups"] = details[category].get(url, {}).get("listing_urls", [])
            views = {dict(parse_qsl(urlsplit(group).query)).get("view") for group in record["source_groups"]}
            if category in {"Armors", "Weapons"} and len(views) == 1 and None not in views:
                record["source_group"] = "view-" + next(iter(views))
            records[category][record["id"]] = record
            processed.add(url)
            for field, nested in (("forging_materials", "item"), ("upgrade_materials", "item"),
                                  ("materials", "item"), ("skills", "skill")):
                for entry in record.get(field, []):
                    reference = entry[nested]["link"]
                    if reference not in processed:
                        queue.add(reference)
            if len(processed | queue) > 20000:
                raise DownloadError("Reference discovery exceeded 20000 pages")
            print(f"Parsed {category}/{record['id']} ({len(processed)} done, {len(queue)} queued)", flush=True)
        client.manifest["stage"] = "finalizing"
        client.save()
        output = client.directory / "structured"
        output.mkdir(exist_ok=True)
        coverage = {}
        for category, entries in records.items():
            versions = {record["source_version"] for record in entries.values() if record["source_version"]}
            coverage[category] = {
                "discovered": len(details[category]), "parsed": len(entries),
                "missing": sorted(set(details[category]) - {record["source_url"] for record in entries.values()}),
            }
            write_json(output / (category + ".json"), {
                "schema_version": 1, "version": next(iter(versions)) if len(versions) == 1 else None,
                "date": client.manifest["snapshot_id"], "snapshot_id": client.manifest["snapshot_id"],
                "snapshot_mode": client.manifest["mode"], "coverage": coverage[category],
                "data": [entries[key] for key in sorted(entries)],
            })
        client.merge_page_states()
        client.manifest["coverage"] = coverage
        client.manifest["complete"] = all(not values["missing"] for values in coverage.values())
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
    parser.add_argument("--full", action="store_true", help="Download all discovered records; may take hours")
    parser.add_argument("--limit-per-category", type=int, default=1, help="Pilot sample size; ignored with --full")
    parser.add_argument("--delay", type=float, default=2.0, help="Minimum seconds between requests (at least 2)")
    args = parser.parse_args()
    try:
        report = download_snapshot(args.snapshot, args.full, args.limit_per_category, args.delay)
    except (DownloadError, ValueError, OSError) as error:
        parser.exit(1, f"Snapshot failed; fix the issue and resume with the same --snapshot: {error}\n")
    print(f"Snapshot ready. Complete coverage: {report['complete']}. "
          f"Structured files: {args.snapshot / 'structured'}")


if __name__ == "__main__":
    main()