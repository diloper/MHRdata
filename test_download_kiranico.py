import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from convert_notebooklm import convert, render_record
from download_kiranico import (BASE_URL, DownloadError, SnapshotClient, canonical_url,
                               discover, download_snapshot, identity, listing_category, parse_detail)


def page(body, name="Example"):
    return ('<html><head><meta name="description" content="Source description"></head>'
            '<body><nav><h2>Database Ver16.0.0</h2></nav><article>'
            f'<h1>{name}</h1>{body}</article></body></html>')


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

            def fetch(self, url):
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
        lists = {category: BASE_URL + "/data/" + category +
                 ("?view=0" if category in {"items", "armors", "weapons"} else "")
                 for category in categories}
        nav = '<nav>' + ''.join(f'<a href="{url}">{category}</a>' for category, url in lists.items()) + '</nav>'
        documents = {}
        for category, url in lists.items():
            link = f'<a href="/data/{category}/42">Example</a>'
            if category == "items":
                link += '<a href="/data/items/43">New item</a>'
            body = f'<div class="grid">{link}</div>' if category == "items" else f'<table><tr><td>{link}</td></tr></table>'
            documents[url] = (nav + page(body)).encode()
        for category in categories:
            body = ('<h2>Forging Materials</h2><table><tr><td><a href="/data/items/99">Extra</a>'
                    '</td><td>x2</td></tr></table>') if category == "weapons" else '<table><tr><td>Value</td></tr></table>'
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
            self.assertEqual(len(exported["generated_files"]), 5)
            second = download_snapshot(directory)
            self.assertEqual(first["snapshot_id"], second["snapshot_id"])
            self.assertEqual(first["pages"], second["pages"])
            with patch("download_kiranico.parse_detail", wraps=parse_detail) as parser:
                full = download_snapshot(directory, full=True)
                self.assertEqual([call.args[1] for call in parser.call_args_list],
                                 [BASE_URL + "/data/items/43"])
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


if __name__ == "__main__":
    unittest.main()