"""Shared fixtures: run the worker as a subprocess against an offline (file://) Terno site."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
MAIN = next(p for p in (ROOT / "src" / "main.py", ROOT / "main.py") if p.is_file())  # repo or container layout
FIXTURES = ROOT / "tests" / "fixtures"

LEAFLET_TYPE = "Leaflet"
CURRENT_KEY = "Leaflet|2026-07-02|2026-07-08"   # week 27 (the current tab)
FUTURE_KEY = "Leaflet|2026-07-09|2026-07-15"    # week 28 (the future tab)
UNREACHABLE = "http://127.0.0.1:9/nope"
# (slug, sourceDocumentTitle, pdf): the current and the future tab of terno.sk
PUBLICATIONS = {
    "page_8": ("w27_letak_terno_test-abc123", "July 01, 2026 13:48", "https://example.com/fake-terno-letak-27.pdf"),
    "page_16": ("w28_letak_terno_test-def456", "July 08, 2026 09:00", "https://example.com/fake-terno-letak-28.pdf"),
}


@dataclass(frozen=True)
class Run:
    """The outcome of one worker invocation: exit code and the parsed stdout JSON lines."""

    returncode: int
    lines: list[dict[str, Any]]
    stdout: str

    @property
    def result(self) -> dict[str, Any]:
        results = [m for m in self.lines if m.get("type") == "result"]
        assert results, f"no result line in {self.stdout!r}"
        return results[-1]["data"]

    @property
    def error(self) -> dict[str, Any]:
        errors = [m for m in self.lines if m.get("type") == "error"]
        assert errors, f"no error line in {self.stdout!r}"
        return errors[-1]


def _main_html(current_url: str | None, future_url: str | None) -> str:
    def iframe(url: str | None) -> str:
        return f'<iframe src="{url}"></iframe>' if url else ""

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
    <div class="flipbook-viewport"><div class="container">{iframe(current_url)}</div></div>
</div>
<div class="tab-pane text-center mt-5 mb-5" id="page_16">
    <div class="flipbook-viewport"><div class="container">{iframe(future_url)}</div></div>
</div>
</body></html>"""


def _publication_html(slug: str, title: str, pdf_url: str) -> str:
    data = json.dumps({"id": 1, "slug": slug, "sourceDocumentTitle": title, "config": {"downloadPdfUrl": pdf_url}})
    return f"<html><body><script>\n        var data =   {data};\n</script></body></html>"


def write_site(directory: Path, tabs: tuple[str, ...] = ("page_8", "page_16"), page: str | None = None) -> str:
    """Writes the main page plus a Publitas page per published tab; returns the main page URI."""
    directory.mkdir(parents=True, exist_ok=True)
    urls: dict[str, str | None] = {"page_8": None, "page_16": None}
    for tab in tabs:
        slug, title, pdf = PUBLICATIONS[tab]
        file = directory / f"{tab}.html"
        file.write_text(_publication_html(slug, title, pdf), encoding="utf-8")
        urls[tab] = file.as_uri()
    main = directory / "terno.html"
    main.write_text(page if page is not None else _main_html(urls["page_8"], urls["page_16"]), encoding="utf-8")
    return main.as_uri()


@pytest.fixture
def fixture_config(tmp_path: Path) -> dict[str, str]:
    """Offline overrides: the main page points at a generated local site (current + future leaflet)."""
    return {"pageUrl": write_site(tmp_path / "site")}


@pytest.fixture
def empty_page(tmp_path: Path) -> str:
    """A main page without any published leaflet (the site layout changed)."""
    return write_site(tmp_path / "empty-site", page="<html><body>redesigned page</body></html>")


@pytest.fixture
def run_action(tmp_path: Path):
    """Run the worker with `_action` + params; returns a Run. Logs go to tmp_path."""

    def _run(action: str, **params: Any) -> Run:
        env = {**os.environ, "LOG_DIR": str(tmp_path / "logs"), "PYTHONIOENCODING": "utf-8"}
        payload = json.dumps({"_action": action, **params}, ensure_ascii=False)
        proc = subprocess.run(
            [sys.executable, str(MAIN)], input=payload.encode("utf-8"),
            capture_output=True, env=env, check=False)
        stdout = proc.stdout.decode("utf-8")
        lines = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        return Run(proc.returncode, lines, stdout)

    return _run
