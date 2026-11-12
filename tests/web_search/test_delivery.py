"""Public evidence remains exact when many results share a hosted case file."""
import json
import gzip
import base64
import tempfile
import unittest
import pytest
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup

from harness.record_io import bytes_sha256
from harness.web_search.delivery import export, verify, transform_page
from harness.web_search.example import example
from harness.web_search.messages import zh_text
from harness.web_search.report import build, main
from harness.web_search.comparison import render_comparison


class DeliveryScenarios(unittest.TestCase):
    def test_paginated_cards_link_to_registered_language_specific_shards(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            example(root)
            build(root / "report", source=root / "fixture", include_english=True)
            profile = json.loads((root / "fixture/profile.json").read_text(encoding="utf-8"))
            rows = [json.loads(line) for line in (root / "fixture/results.jsonl").read_text(encoding="utf-8").splitlines()]
            for prefix, zh in (("", True), ("en/", False)):
                path = root / "report" / prefix / "results.html"
                text = path.read_text(encoding="utf-8").replace("</main>", render_comparison(
                    profile, rows, zh,
                    lambda row, prefix=prefix: prefix + "details/" + row["id"] + ".html"
                ) + "</main>")
                path.write_text(text, encoding="utf-8")
            export(root / "report", root / "delivery")
            index = json.loads((root / "delivery/delivery-index.json").read_text(encoding="utf-8"))
            for prefix in ("", "en/"):
                doc = BeautifulSoup((root / "delivery" / prefix / "results.html").read_text(encoding="utf-8"), "html.parser")
                data = json.loads(doc.select_one("[data-comparison-data]").string)
                self.assertEqual(len(data["cases"]), len(profile["cases"]))
                for case in data["cases"]:
                    self.assertEqual(set(case["outputs"]), {m["id"] for m in profile["methods"]})
                    for result in case["outputs"].values():
                        target = unquote(result["detail"].split("#", 1)[1])
                        self.assertTrue(target.startswith(prefix + "details/"))
                        shard = json.loads(gzip.decompress((root / "delivery" / index["files"][target]).read_bytes()))
                        self.assertEqual(base64.b64decode(shard[target]["base64"]), (root / "report" / target).read_bytes())

    def test_dynamic_comparison_links_retain_language_and_safe_payloads(self):
        for prefix in ("", "en/", "zh/"):
            with self.subTest(prefix=prefix):
                target = prefix + "details/case-provider-r0.html"
                data = {"cases": [{"query": "</script><img src=x>", "outputs": {
                    "a": {"detail": target, "items": []}, "missing": None}}]}
                text = '<html><head></head><body><script type="application/json" data-comparison-data>' + json.dumps(data).replace("<", "\\u003c") + '</script></body></html>'
                doc = BeautifulSoup(transform_page(text), "html.parser")
                actual = json.loads(doc.select_one("[data-comparison-data]").string)
                href = actual["cases"][0]["outputs"]["a"]["detail"]
                self.assertTrue(href.startswith("detail.html#"))
                self.assertEqual(unquote(href.split("#", 1)[1]), target)
                self.assertEqual(actual["cases"][0]["query"], data["cases"][0]["query"])
                self.assertIsNone(actual["cases"][0]["outputs"]["missing"])
                self.assertFalse(doc.find("img"))

    def test_public_export_command_requires_valid_existing_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            example(root)
            main(["--output", str(root / "report"), "--export-delivery", str(root / "delivery")])
            self.assertTrue(verify(root / "report", root / "delivery"))
            proof = root / "incomplete-visual.json"
            proof.write_text(json.dumps({"manifest_sha256": bytes_sha256((root / "report/manifest.json").read_bytes()), "captures": []}), encoding="utf-8")
            with self.assertRaises(ValueError):
                main(["--output", str(root / "report"), "--release-check", str(proof), "--export-delivery", str(root / "unreviewed")])
            self.assertFalse((root / "unreviewed").exists())
            evidence = next((root / "report/calls").glob("*.json"))
            evidence.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                main(["--output", str(root / "report"), "--export-delivery", str(root / "invalid")])
            self.assertFalse((root / "invalid").exists())

    def test_pages_and_error_responses_roundtrip_and_tampering_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            example(root)
            build(root / "report", source=root / "fixture", include_english=True)
            export(root / "report", root / "delivery")
            self.assertTrue(verify(root / "report", root / "delivery"))
            index = json.loads((root / "delivery/delivery-index.json").read_text(encoding="utf-8"))
            self.assertTrue(any(p.startswith("calls/") for p in index["files"]))
            self.assertTrue(any("en/details/" in p for p in index["files"]))
            self.assertIn("detail.html#", (root / "delivery/results.html").read_text(encoding="utf-8"))
            self.assertIn("const nativeFetch=", (root / "delivery/results.html").read_text(encoding="utf-8"))
            detail = (root / "delivery/detail.html").read_text(encoding="utf-8")
            self.assertIn(zh_text("Download original response"), detail)
            self.assertNotIn("__DOWNLOAD_LABEL_ZH__", detail)
            shard = root / "delivery" / next(iter(index["files"].values()))
            values = json.loads(gzip.decompress(shard.read_bytes()))
            values[next(iter(values))]["base64"] = base64.b64encode(b"changed").decode()
            shard.write_bytes(gzip.compress(json.dumps(values).encode(), mtime=0))
            with self.assertRaises(ValueError):
                verify(root / "report", root / "delivery")


def test_large_comparison_stays_browsable_under_host_html_limit(tmp_path):
    from playwright.sync_api import sync_playwright
    from harness.reporting.visual import browser_executable, local_report_server

    example(tmp_path)
    source, destination = tmp_path / 'report', tmp_path / 'delivery'
    profile = json.loads((tmp_path / 'fixture/profile.json').read_bytes())
    rows = [json.loads(line) for line in (tmp_path / 'fixture/results.jsonl').read_text(encoding='utf-8').splitlines()]
    next(row for row in rows if row['results'])['results'][0]['summary'] = 'Long native summary. ' * 500000
    path = source / 'results.html'
    path.write_text(path.read_text(encoding='utf-8').replace('</main>', render_comparison(profile, rows, True) + '</main>'), encoding='utf-8')
    assert path.stat().st_size > 8 * 1024 * 1024
    export(source, destination)
    assert verify(source, destination)
    assert (destination / 'results.html').stat().st_size < 8 * 1024 * 1024
    doc = BeautifulSoup((destination / 'results.html').read_bytes(), 'html.parser')
    data_node = doc.select_one('[data-comparison-src]')
    data_file = destination / data_node['data-comparison-src']
    assert len(gzip.decompress(data_file.read_bytes())) > 8 * 1024 * 1024
    assert not data_node.get_text()
    original = data_file.read_bytes()
    with local_report_server(destination) as origin, sync_playwright() as engine:
        browser = engine.chromium.launch(executable_path=browser_executable(), headless=True)
        try:
            page = browser.new_page()
            page.goto(origin + '/results.html')
            page.locator('.compare-task').wait_for()
            assert page.locator('.compare-method').count() == 2
            assert page.locator('.compare-method a[href^="detail.html#"]').count() == 2
            data_file.unlink()
            page.reload()
            assert page.get_by_role('alert').inner_text() == '逐题数据加载失败，请刷新页面重试。'
        finally:
            browser.close()
    with pytest.raises(ValueError, match='Comparison data changed'):
        verify(source, destination)
    data_file.write_bytes(original + b' ')
    with pytest.raises(ValueError, match='Comparison data changed'):
        verify(source, destination)


def test_crlf_and_non_utf8_provider_bodies_are_lossless():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        example(root)
        source = root / "report"
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        evidence = source / manifest["rows"][0]["evidence"]["path"]
        for index, raw in enumerate((b'{"message":"busy"}\r\n', b'upstream error: \xff\xfe\r\n')):
            evidence.write_bytes(raw)
            destination = root / ("packed-" + str(index))
            export(source, destination)
            index = json.loads((destination / "delivery-index.json").read_text(encoding="utf-8"))
            virtual = evidence.relative_to(source).as_posix()
            shard = json.loads(gzip.decompress((destination / index["files"][virtual]).read_bytes()))
            assert base64.b64decode(shard[virtual]["base64"]) == raw
            assert verify(source, destination)
