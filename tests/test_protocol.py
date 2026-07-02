#!/usr/bin/env python3
"""Protocol-contract tests: run src/main.py as a subprocess and check exit
codes, stdout JSON Lines shape, and --version - all offline via file://
fixtures (both the main page and, since Terno needs a second fetch per tab,
the Publitas viewer page are synthetic HTML written to a temp dir so the
iframe src can point back at a local file:// URI instead of the real
internet).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "src" / "main.py"
if not MAIN.is_file():
    MAIN = ROOT / "main.py"  # flat layout inside the Dockerfile.test image


def _main_html(current_pub_url: str | None, future_pub_url: str | None = None) -> str:
    current_iframe = f'<iframe src="{current_pub_url}"></iframe>' if current_pub_url else ""
    future_iframe = f'<iframe src="{future_pub_url}"></iframe>' if future_pub_url else ""
    return f"""<html><body>
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
    <div class="flipbook-viewport"><div class="container">{current_iframe}</div></div>
</div>
<div class="tab-pane text-center mt-5 mb-5" id="page_16">
    <div class="flipbook-viewport"><div class="container">{future_iframe}</div></div>
</div>
</body></html>"""


def _publication_html(slug: str, source_document_title: str, pdf_url: str) -> str:
    data = json.dumps({
        "id": 1,
        "slug": slug,
        "sourceDocumentTitle": source_document_title,
        "config": {"downloadPdfUrl": pdf_url},
    })
    return f"""<html><body><script>
        var data =   {data};
</script></body></html>"""


def _run(stdin_obj, argv=None, env_extra=None):
    env = dict(os.environ)
    env["LOG_LEVEL"] = "Debug"
    if env_extra:
        env.update(env_extra)
    cmd = [sys.executable, str(MAIN)] + (argv or [])
    proc = subprocess.run(
        cmd,
        input=(json.dumps(stdin_obj) + "\n") if stdin_obj is not None else "",
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    return proc


def _stdout_lines(proc):
    return [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]


class VersionFlagTests(unittest.TestCase):
    def test_version_matches_manifest(self):
        manifest_path = ROOT / "manifest.json"
        if not manifest_path.is_file():
            manifest_path = MAIN.parent / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        proc = _run(None, argv=["--version"])
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), manifest["version"])


class HappyPathTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self.tmpdir.name)

        pub_file = tmp / "publitas.html"
        pub_file.write_text(
            _publication_html("w27_letak_terno_test-abc123", "July 01, 2026 13:48",
                              "https://example.com/fake-terno-letak.pdf"),
            encoding="utf-8",
        )
        pub_url = pub_file.as_uri()

        main_file = tmp / "terno.html"
        main_file.write_text(_main_html(current_pub_url=pub_url), encoding="utf-8")
        self.main_url = main_file.as_uri()

        self.output_path = tmp / "result.json"
        self.logs_dir = tmp / "logs"

    def tearDown(self):
        self.tmpdir.cleanup()

    def _execute(self, **overrides):
        payload = {
            "_action": "execute",
            "outputPath": str(self.output_path),
            "pageUrl": self.main_url,
            "_meta": {"executionId": "test-exec-id"},
        }
        payload.update(overrides)
        return _run(payload, env_extra={"LOG_DIR": str(self.logs_dir)})

    def test_exit_code_zero_and_result_emitted(self):
        proc = self._execute()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = _stdout_lines(proc)
        self.assertTrue(lines, "expected at least one stdout line")
        for line in lines:
            self.assertIn(line["type"], ("progress", "result", "error"))
        self.assertEqual(lines[-1]["type"], "result")

    def test_only_json_lines_on_stdout(self):
        proc = self._execute()
        for raw_line in proc.stdout.splitlines():
            if raw_line.strip():
                json.loads(raw_line)  # raises if not valid JSON

    def test_new_leaflet_is_returned(self):
        proc = self._execute()
        result = _stdout_lines(proc)[-1]["data"]
        self.assertEqual(result["count"], 1)
        self.assertTrue(result["hasNew"])
        leaflet = result["leaflets"][0]
        self.assertEqual(leaflet["type"], "current")
        self.assertEqual(leaflet["from"], "2026-06-29")
        self.assertEqual(leaflet["to"], "2026-07-05")
        self.assertEqual(leaflet["url"], "https://example.com/fake-terno-letak.pdf")

    def test_known_leaflet_is_filtered_out(self):
        proc = self._execute(knownLeaflets=["current|2026-06-29|2026-07-05"])
        result = _stdout_lines(proc)[-1]["data"]
        self.assertEqual(result["count"], 0)
        self.assertFalse(result["hasNew"])

    def test_type_filter_excludes_non_matching_types(self):
        proc = self._execute(types=["future"])
        result = _stdout_lines(proc)[-1]["data"]
        self.assertEqual(result["count"], 0)

    def test_type_filter_is_case_insensitive(self):
        proc = self._execute(types=["CURRENT"])
        result = _stdout_lines(proc)[-1]["data"]
        self.assertEqual(result["count"], 1)

    def test_output_file_is_written(self):
        proc = self._execute()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(self.output_path.exists())
        written = json.loads(self.output_path.read_text(encoding="utf-8"))
        self.assertEqual(written["count"], 1)


class NoLeafletsTests(unittest.TestCase):
    def test_current_tab_with_no_iframe_yields_no_leaflets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            main_file = tmp / "terno.html"
            main_file.write_text(_main_html(current_pub_url=None), encoding="utf-8")
            proc = _run(
                {
                    "_action": "execute",
                    "outputPath": str(tmp / "result.json"),
                    "pageUrl": main_file.as_uri(),
                    "_meta": {},
                },
                env_extra={"LOG_DIR": str(tmp / "logs")},
            )
            result = _stdout_lines(proc)[-1]["data"]
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(result["count"], 0)
            self.assertFalse(result["hasNew"])


class ErrorPathTests(unittest.TestCase):
    def test_missing_output_path_is_input_error(self):
        proc = _run({"_action": "execute", "_meta": {}})
        self.assertEqual(proc.returncode, 2)
        lines = _stdout_lines(proc)
        self.assertEqual(lines[-1]["type"], "error")

    def test_invalid_json_on_stdin_is_input_error(self):
        proc = subprocess.run(
            [sys.executable, str(MAIN)],
            input="not json\n",
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 2)

    def test_empty_stdin_is_input_error(self):
        proc = _run(None)
        self.assertEqual(proc.returncode, 2)

    def test_unknown_action_is_input_error(self):
        proc = _run({"_action": "bogus", "_meta": {}})
        self.assertEqual(proc.returncode, 2)

    def test_fetch_failure_is_dependency_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = _run(
                {
                    "_action": "execute",
                    "outputPath": str(Path(tmp) / "result.json"),
                    "pageUrl": (Path(tmp) / "does-not-exist.html").as_uri(),
                    "_meta": {},
                }
            )
            self.assertEqual(proc.returncode, 4)
            lines = _stdout_lines(proc)
            self.assertEqual(lines[-1]["type"], "error")
            self.assertEqual(lines[-1].get("code"), "FETCH_FAILED")


class TerminateActionTests(unittest.TestCase):
    def test_terminate_as_initial_action_returns_cleaned(self):
        proc = _run({"_action": "terminate", "_meta": {}})
        self.assertEqual(proc.returncode, 0)
        lines = _stdout_lines(proc)
        self.assertEqual(lines[-1]["type"], "result")
        self.assertTrue(lines[-1]["data"]["cleaned"])


if __name__ == "__main__":
    unittest.main()
