#!/usr/bin/env python3
"""Offline parser tests for letak-worker-terno against saved page fixtures.

    python tests/test_parser.py
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# src/ layout locally; flat /app in the test container (Dockerfile.test copies
# the modules next to main.py). Put both on the path so `import terno` works.
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import terno  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

REAL_VIEWER_URL = "https://view.publitas.com/terno-letak/w27_letak_terno_200x297mm_online-mklyw6pm7glx/"
REAL_SLUG = "w27_letak_terno_200x297mm_online-mklyw6pm7glx"


def _read_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


class ParseTabsTests(unittest.TestCase):
    def setUp(self):
        self.doc = _read_fixture("terno.html")
        self.tabs = terno.parse_tabs(self.doc)

    def test_finds_both_tabs(self):
        self.assertEqual({t["tab"] for t in self.tabs}, {"current", "future"})

    def test_current_tab_has_viewer_url(self):
        current = next(t for t in self.tabs if t["tab"] == "current")
        self.assertEqual(current["viewerUrl"], REAL_VIEWER_URL)

    def test_future_tab_has_no_viewer_url_yet(self):
        future = next(t for t in self.tabs if t["tab"] == "future")
        self.assertIsNone(future["viewerUrl"])

    def test_no_tabs_found_in_empty_html(self):
        self.assertEqual(terno.parse_tabs("<html><body>nothing here</body></html>"), [])

    def test_both_tabs_populated(self):
        # Synthetic: exercises the branch where "future" also has an iframe,
        # which the real fixture (captured when no future leaflet existed)
        # cannot cover.
        doc = """<html><body>
<ul class="letak-tabs nav nav-tabs">
    <li class="nav-item active">
        <a data-bs-target="#page_8" class="letak-tab active" data-bs-toggle="tab" alt="Aktualny letak" title="Aktualny letak">
            <h5>Aktualny letak</h5>
        </a>
    </li>
    <li class="nav-item">
        <a data-bs-target="#page_16" class="letak-tab" data-bs-toggle="tab" alt="Buduci letak" title="Buduci letak">
            <h5>Buduci letak</h5>
        </a>
    </li>
</ul>
<div class="tab-pane text-center mt-5 mb-5 active" id="page_8">
    <iframe src="https://view.publitas.com/terno-letak/current-slug/"></iframe>
</div>
<div class="tab-pane text-center mt-5 mb-5" id="page_16">
    <iframe src="https://view.publitas.com/terno-letak/future-slug/"></iframe>
</div>
</body></html>"""
        tabs = terno.parse_tabs(doc)
        by_tab = {t["tab"]: t["viewerUrl"] for t in tabs}
        self.assertEqual(by_tab["current"], "https://view.publitas.com/terno-letak/current-slug/")
        self.assertEqual(by_tab["future"], "https://view.publitas.com/terno-letak/future-slug/")


class ParsePublicationTests(unittest.TestCase):
    def setUp(self):
        self.pub = terno.parse_publication(_read_fixture("publitas.html"))

    def test_slug_extracted(self):
        self.assertEqual(self.pub["slug"], REAL_SLUG)

    def test_source_document_title_extracted(self):
        self.assertEqual(self.pub["sourceDocumentTitle"], "July 01, 2026 13:48")

    def test_pdf_url_extracted(self):
        self.assertTrue(self.pub["pdfUrl"].startswith("https://view.publitas.com/"))
        self.assertIn(".pdf", self.pub["pdfUrl"])

    def test_raises_when_data_block_missing(self):
        with self.assertRaises(ValueError):
            terno.parse_publication("<html><body>no data here</body></html>")


class WeekBoundsTests(unittest.TestCase):
    def test_iso_week_monday_to_sunday(self):
        valid_from, valid_to = terno._week_bounds("w27_letak_terno_x", "July 01, 2026 13:48")
        self.assertEqual(valid_from, "2026-06-29")
        self.assertEqual(valid_to, "2026-07-05")

    def test_none_when_slug_missing_week_prefix(self):
        self.assertEqual(terno._week_bounds("letak_terno_no_week", "July 01, 2026 13:48"), (None, None))

    def test_none_when_title_unparseable(self):
        self.assertEqual(terno._week_bounds("w27_letak_terno_x", "not a date"), (None, None))

    def test_none_when_either_input_missing(self):
        self.assertEqual(terno._week_bounds(None, "July 01, 2026 13:48"), (None, None))
        self.assertEqual(terno._week_bounds("w27_letak_terno_x", None), (None, None))


class LeafletKeyTests(unittest.TestCase):
    def test_key_format(self):
        self.assertEqual(
            terno.leaflet_key("current", "2026-06-29", "2026-07-05"),
            "current|2026-06-29|2026-07-05",
        )

    def test_different_types_same_dates_produce_different_keys(self):
        k1 = terno.leaflet_key("current", "2026-06-29", "2026-07-05")
        k2 = terno.leaflet_key("future", "2026-06-29", "2026-07-05")
        self.assertNotEqual(k1, k2)


class ParseLeafletsTests(unittest.TestCase):
    def test_combines_main_page_and_publication_page(self):
        main_doc = _read_fixture("terno.html")
        pub_doc = _read_fixture("publitas.html")

        leaflets = terno.parse_leaflets(main_doc, {REAL_VIEWER_URL: pub_doc})

        self.assertEqual(len(leaflets), 1)
        leaflet = leaflets[0]
        self.assertEqual(leaflet["type"], "current")
        self.assertEqual(leaflet["validFrom"], "2026-06-29")
        self.assertEqual(leaflet["validTo"], "2026-07-05")
        self.assertEqual(leaflet["viewerUrl"], REAL_VIEWER_URL)
        self.assertTrue(leaflet["pdfUrl"].endswith(
            "filename%2A%3DUTF-8%27%27Terno%2520-%2520w27_Letak_TERNO_200x297mm_online.pdf"))

    def test_tab_without_fetched_publication_is_skipped(self):
        main_doc = _read_fixture("terno.html")
        # No publication_docs supplied at all -> current tab has a viewerUrl
        # but no fetched page, so it must be skipped rather than crash.
        self.assertEqual(terno.parse_leaflets(main_doc, {}), [])

    def test_tab_without_viewer_url_is_skipped(self):
        # future tab has no iframe in the real fixture -> never looked up.
        main_doc = _read_fixture("terno.html")
        pub_doc = _read_fixture("publitas.html")
        leaflets = terno.parse_leaflets(main_doc, {REAL_VIEWER_URL: pub_doc})
        self.assertTrue(all(lf["type"] != "future" for lf in leaflets))


if __name__ == "__main__":
    unittest.main()
