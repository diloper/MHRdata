import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from convert_notebooklm import convert, render_record
from download_kiranico import (BASE_URL, DownloadError, SnapshotClient, canonical_url,
                               discover, download_snapshot, identity, listing_category, parse_detail)
from test_kiranico_listing import (ammo_table, armor_row, decoration_row, item_grid, listing_page,
                                   rampage_decoration_row, rampage_skill_row, skill_row, weapon_row)


def page(body, name="Example"):
    return ('<html><head><meta name="description" content="Source description"></head>'
            '<body><nav><h2>Database Ver16.0.0</h2></nav><article>'
            f'<h1>{name}</h1>{body}</article></body></html>')


def armor_overview(slot_html):
    return (f'<table><tr><td>{slot_html}</td><td>32 Defense</td>'
            + '<td>0</td>' * 5 + '<td>Skills</td></tr></table>')


def fake_site(extra=False):
    """Realistic minimal listing pages keyed by URL; ``extra`` adds a Light Bowgun and rampage pages."""
    pages = {
        "/data/items?view=0": ("Items", item_grid(("42", "Example"), ("43", "New item")), "Materials"),
        "/data/skills": ("Skills", skill_row("42", "Example", ["Stun power +20%"]), None),
        "/data/decorations": ("Decorations", decoration_row("42", "Example Jewel 1", ("42", "Example", 1)), None),
        "/data/armors?view=0": ("Armor", armor_row("42", "Example", skills=(("42", "Example", 1),)), "Rarity 1"),
        "/data/weapons?view=0": ("Great Sword", weapon_row("42", "Example"), "Great Sword"),
    }
    if extra:
        pages.update({
            "/data/weapons?view=13": ("Light Bowgun", weapon_row(
                "77", "Sticky Gun", "Light Bowgun",
                detail=ammo_table({"Sti": [3, 2, 1], "Nrm": [5, 5, 5]})), "Light Bowgun"),
            "/data/rampage-skills": ("Rampage Skills", rampage_skill_row("7", "Anti-Aerial Species"), None),
            "/data/rampage-decorations": ("Rampage Decorations",
                                          rampage_decoration_row("Anti-Aerial Jewel 1"), None),
        })
    nav = "".join(f'<a href="{BASE_URL + path}">{title}</a>' for path, (title, _, _) in pages.items())
    return {BASE_URL + path: listing_page(title, rows, path, label, nav).encode()
            for path, (title, rows, label) in pages.items()}


class ParserTests(unittest.TestCase):
    def test_listing_routes(self):
        self.assertEqual(listing_category(BASE_URL + "/data/items?view=material"), "Items")
        self.assertEqual(listing_category(BASE_URL + "/data/weapons?view=0"), "Weapons")
        for path in ("/data/items", "/data/weapons/42", "/data/skills?locale=zh",
                     "/zh-Hant/data/skills", "/data/quests?view=event"):
            self.assertIsNone(listing_category(BASE_URL + path))

    def test_allowed_urls_and_namespaces(self):
        self.assertEqual(canonical_url("/data/items/42#materials"), BASE_URL + "/data/items/42")
        self.assertEqual(identity(BASE_URL + "/data/items/42"), ("Items", "42"))
        self.assertEqual(identity(BASE_URL + "/data/skills/42"), ("Skills", "42"))
        for url in ("http://mhrise.kiranico.com/data/items/42", "https://evil.example/",
                    "https://mhrise.kiranico.com.evil.example/", "javascript:alert(1)"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                canonical_url(url)
        with self.assertRaises(ValueError):
            identity(BASE_URL + "/zh-Hant/data/items/42")

    def test_skill_effects_and_provenance(self):
        category, record = parse_detail(page('<table><tbody><tr><td>Lv 4</td>'
                                            '<td>Attack +5% Bonus: +7</td></tr></tbody></table>'),
                                        BASE_URL + "/data/skills/42")
        self.assertEqual(category, "Skills")
        self.assertEqual(record["lv4"], "Attack +5% Bonus: +7")
        self.assertEqual(record["source_version"], "16.0.0")
        self.assertEqual(record["description"], "Source description")
        self.assertEqual(record["source_url"], BASE_URL + "/data/skills/42")

    def test_materials_and_empty_section(self):
        body = ('<h2>Forging Materials</h2><table><tr><td>x2</td><td>'
                '<a href="/data/items/1665412677">Azure Era Gem</a></td></tr></table>'
                '<h2>Upgrade Materials</h2><table><tbody></tbody></table>')
        _, record = parse_detail(page(body), BASE_URL + "/data/weapons/42")
        self.assertEqual(record["forging_materials"][0]["amount"], "x2")
        self.assertEqual(record["forging_materials"][0]["item"]["id"], "1665412677")
        self.assertEqual(record["upgrade_materials"], [])
        self.assertNotIn("materials", record)

    def test_granted_levels_and_icon_annotations(self):
        body = ('<h2>Skills</h2><table><tr><td><a href="/data/skills/42">Skill</a></td>'
                '<td>Lv 2</td><td><svg><use href="#slot-3"/></svg></td></tr></table>')
        _, record = parse_detail(page(body), BASE_URL + "/data/armors/42")
        self.assertEqual(record["skills"][0]["equipped_level"], 2)
        self.assertEqual(record["tables"][0]["rows"][0][2]["annotations"], ["#slot-3"])

    def test_armor_slots_from_overview_only(self):
        url = BASE_URL + "/data/armors/42"
        other_table = '<h2>Forging Materials</h2>' + armor_overview('<img src="deco3.png">')
        for levels in ([], [1, 1], [2, 4, 3]):
            icons = ' '.join(f'<img src="/images/ui/deco{level}.png">' for level in levels)
            html = page(armor_overview(icons) + other_table)
            with self.subTest(levels=levels):
                category, record = parse_detail(html, url)
                self.assertEqual(record["decoration_slot_levels"], levels)
                warnings = []
                output = render_record(category, record, {}, warnings)
                self.assertIn("- Decoration slot levels: " + (", ".join(f"Lv{n}" for n in levels) or "None")
                              + "\n", output)
                self.assertIn(f"- Total decoration slots: {len(levels)}\n", output)
                self.assertEqual(warnings, [])
        _, weapon = parse_detail(html, BASE_URL + "/data/weapons/42")
        self.assertNotIn("decoration_slot_levels", weapon)

    def test_unrecognized_armor_slot_data_never_means_zero_or_partial(self):
        url = BASE_URL + "/data/armors/42"
        for body in (armor_overview('').replace('<td></td>', ''),
                     armor_overview('') + armor_overview(''),
                     armor_overview('<img src="deco1.png"><img src="deco5.png">'),
                     armor_overview('<svg><use href="#unknown"/></svg>')):
            with self.subTest(body=body):
                category, record = parse_detail(page(body), url)
                self.assertIsNone(record["decoration_slot_levels"])
                warnings = []
                output = render_record(category, record, {}, warnings)
                self.assertIn("- Decoration slot levels: Unknown\n- Total decoration slots: Unknown\n", output)
                self.assertEqual(warnings, [{"category": "Armors", "id": "42", "field": "decoration_slot_levels",
                                             "source_url": url, "reason": record["decoration_slot_note"]}])

    def test_multiple_skills_keep_individual_levels(self):
        body = ('<table><tr><td><div><a href="/data/skills/42">First</a> Lv 2</div>'
                '<div><a href="/data/skills/43">Second</a> Lv 1</div></td></tr></table>')
        _, record = parse_detail(page(body), BASE_URL + "/data/armors/42")
        self.assertEqual([entry["equipped_level"] for entry in record["skills"]], [2, 1])

    def test_required_material_with_embedded_quantity_and_graphics(self):
        body = ('<h2>Required Materials</h2><table><tr><td><img src="https://cdn.kiranico.net/icon.png">'
                '<a href="/data/items/42">Material</a> x3</td><td>Description</td></tr></table>'
                '<svg><rect width="8"/></svg>')
        _, record = parse_detail(page(body), BASE_URL + "/data/decorations/42")
        self.assertEqual(record["materials"][0]["amount"], "x3")
        self.assertIn("graphical values", record["visual_data_note"])
        self.assertIn("https://cdn.kiranico.net/icon.png", record["tables"][0]["rows"][0][0]["annotations"])

    def test_invalid_page_and_missing_quantity(self):
        for html in ("<html>Advertisement</html>", page(""),
                     page('<h2>Materials</h2><table><tr><td>'
                          '<a href="/data/items/42">Material</a></td></tr></table>')):
            with self.subTest(html=html), self.assertRaises(ValueError):
                parse_detail(html, BASE_URL + "/data/weapons/42")

    def test_export_preserves_tables_and_equipped_level(self):
        body = ('<h2>Skills</h2><table><tr><td><a href="/data/skills/42">Skill</a></td>'
                '<td>Lv 2</td></tr></table>')
        category, record = parse_detail(page(body), BASE_URL + "/data/armors/42")
        output = render_record(category, record, {}, [])
        self.assertIn("equipped_level: 2", output)
        self.assertIn("Column 2 (unlabelled by source): Lv 2", output)
        self.assertIn(BASE_URL + "/data/skills/42", output)
        self.assertNotIn("Equipped skill levels: not explicitly provided", output)


def response(url, content=b"", status=200, headers=None):
    result = requests.Response()
    result.status_code = status
    result.url = url
    result._content = content
    result.headers.update(headers or {"Content-Type": "text/html; charset=utf-8"})
    return result


class DownloadTests(unittest.TestCase):
    def test_discovery_resumes_from_checkpoint(self):
        categories = ("items", "skills", "decorations", "armors", "weapons")
        lists = {category: BASE_URL + "/data/" + category +
                 ("?view=0" if category in {"items", "armors", "weapons"} else "")
                 for category in categories}
        nav = '<nav>' + ''.join(f'<a href="{url}">{category}</a>' for category, url in lists.items()) + '</nav>'
        documents = {}
        for category, url in lists.items():
            body = f'<table><tr><td><a href="/data/{category}/42">Example</a></td></tr></table>'
            documents[url] = (nav + page(body)).encode()
        interrupted_url = lists["items"]

        class FakeClient:
            def __init__(self, directory):
                self.checkpoint_directory = Path(directory) / "checkpoint"
                self.checkpoint_directory.mkdir(parents=True, exist_ok=True)
                self.manifest = {}
                self.calls = []
                self.fail = True

            def fetch(self, url, refresh=False):
                self.calls.append(url)
                if url == interrupted_url and self.fail:
                    self.fail = False
                    raise DownloadError("simulated discovery interruption")
                return documents[url]

            def reject(self, url, error):
                raise DownloadError(str(error))

            def save(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            first = FakeClient(directory)
            with self.assertRaisesRegex(DownloadError, "simulated discovery"):
                discover(first)
            completed_before_failure = {lists["skills"], lists["armors"], lists["decorations"]}
            resumed = FakeClient(directory)
            resumed.fail = False
            details = discover(resumed)
            self.assertTrue(all(details[category] for category in ("Items", "Skills", "Decorations", "Armors", "Weapons")))
            self.assertTrue(completed_before_failure.isdisjoint(resumed.calls))

            # An interrupted --refresh resumes from its checkpoint, even without --refresh, and
            # never reuses the stale discovery stored in the manifest.
            stale = FakeClient(directory)
            stale.manifest = {"discovered": {"stale": True}, "listing_urls": ["stale"]}
            with self.assertRaisesRegex(DownloadError, "simulated discovery"):
                discover(stale, refresh=True)
            self.assertNotIn("discovered", stale.manifest)
            again = FakeClient(directory)
            again.fail = False
            again.manifest = {"discovered": {"stale": True}, "listing_urls": ["stale"]}
            refreshed = []
            again.fetch = lambda url, refresh=False: (refreshed.append((url, refresh)), documents[url])[1]
            details = discover(again)
            self.assertNotIn("stale", details)
            self.assertTrue(completed_before_failure.isdisjoint(url for url, _ in refreshed))
            self.assertTrue(refreshed and all(flag for _, flag in refreshed))
            checkpoint = json.loads((again.checkpoint_directory / "discovery.json").read_text(encoding="utf-8"))
            self.assertEqual((checkpoint["complete"], checkpoint["refresh"]), (True, True))
            self.assertEqual(discover(again), details)

    def test_verified_cache_resume_and_corruption(self):
        url = BASE_URL + "/data/items/42"
        with tempfile.TemporaryDirectory() as directory:
            client = SnapshotClient(directory)
            with patch.object(client, "request", side_effect=[
                    response(BASE_URL + "/robots.txt", b"User-agent: *\nDisallow:\n"),
                    response(url, b"<article>data</article>"),
                    response(url, b"<article>repaired</article>")]) as request:
                client.check_robots()
                raw = client.fetch(url)
                self.assertEqual(client.fetch(url), raw)
                self.assertEqual(request.call_count, 2)
                cache = Path(directory) / client.manifest["pages"][url]["cache_file"]
                cache.write_bytes(b"corrupt")
                self.assertIn(b"repaired", client.fetch(url))
                self.assertEqual(request.call_count, 3)
            client.close()
            resumed = SnapshotClient(directory)
            self.assertEqual(resumed.manifest["pages"][url]["sha256"], hashlib.sha256(cache.read_bytes()).hexdigest())
            resumed.close()

    @patch("download_kiranico.time.sleep")
    def test_retries_redirect_and_forbidden(self, sleep):
        url = BASE_URL + "/data/items/42"
        with tempfile.TemporaryDirectory() as directory:
            client = SnapshotClient(directory)
            client.session.get = Mock(side_effect=[response(url, status=429, headers={"Retry-After": "3"}),
                                                   response(url, b"ok")])
            self.assertEqual(client.request(url).content, b"ok")
            sleep.assert_any_call(3.0)
            for result in (response(url, status=403), response(url, status=302,
                           headers={"Location": "https://ads.example/"})):
                client.session.get = Mock(return_value=result)
                with self.assertRaises(DownloadError):
                    client.request(url)
                self.assertEqual(client.session.get.call_count, 1)
            client.close()

    def test_robots_and_failed_run_block_export(self):
        with tempfile.TemporaryDirectory() as directory:
            client = SnapshotClient(directory)
            with patch.object(client, "request", return_value=response(
                    BASE_URL + "/robots.txt", b"User-agent: *\nDisallow: /data/\n")):
                client.check_robots()
                with self.assertRaises(DownloadError):
                    client.fetch(BASE_URL + "/data/items/42")
            client.close()
            source = Path(directory) / "structured"
            source.mkdir()
            with self.assertRaisesRegex(ValueError, "did not finish"):
                convert(source, Path(directory) / "export")

    def test_converter_blocks_incomplete_full_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory)
            source = snapshot / "structured"
            source.mkdir()
            (snapshot / "download-manifest.json").write_text(json.dumps({
                "run_status": "success", "mode": "full", "complete": False,
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "incomplete coverage"):
                convert(source, snapshot / "export")

    def test_invalid_cached_page_is_refetched(self):
        url = BASE_URL + "/data/items/42"
        with tempfile.TemporaryDirectory() as directory:
            client = SnapshotClient(directory)
            with patch.object(client, "request", side_effect=[
                    response(BASE_URL + "/robots.txt", b"User-agent: *\nDisallow:\n"),
                    response(url, b"<html>Error</html>"), response(url, page('<table/>').encode())]) as request:
                client.check_robots()
                client.fetch(url)
                with self.assertRaises(DownloadError):
                    client.reject(url, "Missing article/title")
                self.assertEqual(client.manifest["pages"][url]["status"], "invalid")
                self.assertIn(b"<article>", client.fetch(url))
                self.assertEqual(request.call_count, 3)
            client.close()

    def test_interrupted_run_checkpoints_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(SnapshotClient, "check_robots"), patch(
                    "download_kiranico.discover", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    download_snapshot(directory)
            manifest = json.loads((Path(directory) / "download-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["run_status"], "interrupted")
            self.assertFalse(manifest["complete"])

    def test_pilot_reference_closure_export_and_resume(self):
        categories = ("items", "skills", "decorations", "armors", "weapons")
        documents = fake_site()
        for category in categories:
            body = ('<h2>Forging Materials</h2><table><tr><td><a href="/data/items/99">Extra</a>'
                    '</td><td>x2</td></tr></table>') if category == "weapons" else '<table><tr><td>Value</td></tr></table>'
            if category == "armors":
                body = armor_overview('<img src="deco1.png"> <img src="deco1.png">')
            documents[BASE_URL + f"/data/{category}/42"] = page(body).encode()
        for record_id in ("1665412677", "99", "43"):
            documents[BASE_URL + "/data/items/" + record_id] = page('<table><tr><td>Value</td></tr></table>',
                                                                               name="Extra").encode()
        interrupted_reference = BASE_URL + "/data/items/99"
        failed_once = {"value": False}
        def fake_request(client, url):
            if url.endswith("/robots.txt"):
                return response(url, b"User-agent: *\nDisallow:\n")
            if url == interrupted_reference and not failed_once["value"]:
                failed_once["value"] = True
                raise DownloadError("simulated detail interruption")
            return response(url, documents[url])
        with tempfile.TemporaryDirectory() as directory, patch.object(SnapshotClient, "request", fake_request):
            with patch("download_kiranico.parse_detail", wraps=parse_detail) as parser:
                with self.assertRaisesRegex(DownloadError, "simulated detail"):
                    download_snapshot(directory)
                failed = json.loads((Path(directory) / "download-manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(failed["run_status"], "failed")
                self.assertEqual(failed["failed_stage"], "details")
                self.assertEqual(failed["stage"], "failed")
                parser.reset_mock()
                first = download_snapshot(directory)
                self.assertEqual([call.args[1] for call in parser.call_args_list], [interrupted_reference])
            self.assertFalse(first["complete"])
            self.assertEqual(first["stage"], "complete")
            self.assertNotIn("failed_stage", first)
            self.assertEqual(first["coverage"]["Items"]["parsed"], 3)
            exported = convert(Path(directory) / "structured", Path(directory) / "export")
            self.assertEqual(exported["warnings"], [])
            self.assertIn("Rules-bowgun-001.md", exported["generated_files"])
            self.assertEqual(len(exported["generated_files"]), 7)
            second = download_snapshot(directory)
            self.assertEqual(first["snapshot_id"], second["snapshot_id"])
            self.assertEqual(first["pages"], second["pages"])
            armor_url = BASE_URL + "/data/armors/42"
            armor_key = hashlib.sha256(armor_url.encode("utf-8")).hexdigest()
            armor_path = Path(directory) / "checkpoint" / "records" / (armor_key + ".json")
            legacy = json.loads(armor_path.read_text(encoding="utf-8"))
            del legacy["record"]["decoration_slot_levels"]
            armor_path.write_text(json.dumps(legacy), encoding="utf-8")
            del documents[armor_url]  # Only the verified raw cache is available on resume.
            with patch("download_kiranico.parse_detail", wraps=parse_detail) as parser:
                full = download_snapshot(directory, full=True)
                self.assertEqual([call.args[1] for call in parser.call_args_list],
                                 [armor_url, BASE_URL + "/data/items/43"])
            self.assertEqual(json.loads(armor_path.read_text(encoding="utf-8"))["record"]["decoration_slot_levels"],
                             [1, 1])
            exported = convert(Path(directory) / "structured", Path(directory) / "export")
            armor_file, = (name for name in exported["generated_files"] if name.startswith("Armors-"))
            armor_text = (Path(directory) / "export" / armor_file).read_text(encoding="utf-8")
            self.assertIn("- Decoration slot levels: Lv1, Lv1\n- Total decoration slots: 2\n", armor_text)
            self.assertTrue(full["complete"])
            self.assertEqual(full["mode"], "full")
            self.assertEqual(full["coverage"]["Items"]["parsed"], 4)
            promoted_url = BASE_URL + "/data/items/43"
            checkpoint_name = hashlib.sha256(promoted_url.encode("utf-8")).hexdigest() + ".json"
            checkpoint_path = Path(directory) / "checkpoint" / "records" / checkpoint_name
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            checkpoint["source_sha256"] = "stale-source-hash"
            checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
            with patch("download_kiranico.parse_detail", wraps=parse_detail) as parser:
                download_snapshot(directory, full=True)
                self.assertEqual([call.args[1] for call in parser.call_args_list], [promoted_url])

    def test_listings_mode_migration_typed_output_and_supplement(self):
        documents = fake_site(extra=True)
        requested = []

        def fake_request(client, url):
            requested.append(url)
            if url.endswith("/robots.txt"):
                return response(url, b"User-agent: *\nDisallow:\n")
            return response(url, documents[url])

        supplement = {"weapons": [{"game8_name": "Sticky Gun", "page_url": "https://game8.co/x", "ammo": [
            {"name": "Sticky Ammo 1", "capacity": 4, "recoil": "Low", "reload": "Fast",
             "shot_types": ["RF"], "rapid_fire": True}]}], "warnings": []}
        with tempfile.TemporaryDirectory() as directory, patch.object(SnapshotClient, "request", fake_request):
            snapshot = Path(directory)
            (snapshot / "download-manifest.json").write_text(json.dumps({
                "schema_version": 1, "origin": BASE_URL, "language": "en", "snapshot_id": "v1-snapshot",
                "pages": {}, "complete": True, "discovered": {"Items": {}}, "listing_urls": []}),
                encoding="utf-8")
            (snapshot / "supplemental").mkdir()
            (snapshot / "supplemental" / "game8-lbg.json").write_text(json.dumps(supplement), encoding="utf-8")
            manifest = download_snapshot(directory, listings=True)
            structured = snapshot / "structured"
            weapons = json.loads((structured / "Weapons.json").read_text(encoding="utf-8"))
            exported = convert(structured, snapshot / "export")
            light_bowgun_file = (snapshot / "export" / "Weapons-Light-Bowgun-001.md").read_text(encoding="utf-8")
            rules_file = (snapshot / "export" / "Rules-bowgun-001.md").read_text(encoding="utf-8")

        self.assertTrue(manifest["complete"])
        self.assertEqual((manifest["schema_version"], manifest["migrated_from_schema_version"]), (2, 1))
        self.assertEqual(manifest["snapshot_id"], "v1-snapshot")
        self.assertFalse([url for url in requested if re.search(r"/data/[a-z-]+/\d+$", url)],
                         "listings mode must not fetch detail pages")
        self.assertEqual(manifest["coverage"]["RampageDecorations"]["parsed"], 1)
        self.assertEqual(manifest["supplements"]["game8_lbg"]["matched"], 1)
        self.assertEqual(manifest["supplements"]["game8_lbg"]["capacity_mismatches"], 1)
        gun = next(record for record in weapons["data"] if record["id"] == "77")
        self.assertEqual(gun["detail_status"], "not_fetched")
        self.assertEqual(gun["source_group"], "view-13")
        self.assertEqual(gun["fields"]["rapid_fire_ammo"]["value"], ["Sticky Ammo 1"])
        self.assertIn("Game8 capacity 4 for Sticky Ammo 1 differs from Kiranico 3", gun["warnings"][0])
        self.assertEqual(weapons["field_statistics"]["rapid_fire_ammo"]["counts"], {"supplemental": 1})
        self.assertEqual(sum(value["unexpected"] for value in weapons["field_statistics"].values()), 0)
        self.assertIn("RampageSkills-all-001.md", exported["generated_files"])
        self.assertIn("- Rapid Fire ammo: Sticky Ammo 1 (supplemental: game8)", light_bowgun_file)
        self.assertIn("- Loadable ammo (capacity): Normal Ammo 1 x5", light_bowgun_file)
        self.assertIn("- Recoil Down needed (v16 table): Low: Recoil Down Lv1 reaches", light_bowgun_file)
        self.assertIn("- Rampage skills: Not fetched in this snapshot mode", light_bowgun_file)
        self.assertIn("Base Very High: not reachable", rules_file)


if __name__ == "__main__":
    unittest.main()