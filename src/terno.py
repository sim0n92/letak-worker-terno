"""
Terno leaflet page + Publitas viewer parsing.

Pure, network-light helpers so the parser can be unit-tested against saved
HTML fixtures. Only the standard library is used.

Page model (https://terno.sk/sekcia/7-akciovy-letak):
  - Two tabs: "Aktualny letak" (#page_8, current) and "Buduci letak"
    (#page_16, future). Each tab-pane holds at most one
    <iframe src="https://view.publitas.com/..."> embedding a Publitas
    flip-book viewer; an empty tab-pane (no iframe) means no leaflet is
    published for that slot yet (e.g. no future leaflet most weeks).
  - Terno's own page carries NO validity metadata at all -- no from/to
    dates anywhere in the HTML, unlike Kaufland/Tesco/Billa/Lidl. The
    Publitas viewer page has structured data, but only embedded as a
    `var data = {...};` script block (see fetch_publication / parse_publication)
    -- a second fetch, not present in the listing page.
  - Even that embedded JSON has no explicit validFrom/validTo. The only
    stable signals are `slug` (e.g. "w27_letak_terno_200x297mm_online-xxx" --
    "w27" is the ISO week number the leaflet was built for) and
    `sourceDocumentTitle` (e.g. "July 01, 2026 13:48" -- the source PDF's
    upload timestamp, used here only to recover the year).
    validFrom/validTo are therefore APPROXIMATED as the Monday-Sunday span
    of that ISO week -- a deterministic proxy, not Terno's real (possibly
    Wed-Tue or other) offer cycle. Good enough for the novelty key (it still
    changes every week the slug's week number changes); flagged clearly
    here because it differs from sibling parsers where from/to are the
    retailer's exact, real validity.
  - `type` in the novelty key is a CONSTANT (`LEAFLET_TYPE`), not the tab.
    Terno shows exactly one leaflet per tab, and the SAME leaflet moves from
    "future" to "current" as weeks pass (this week's w28-future is next
    week's w28-current). If `type` were the tab, that transition would
    change the novelty key (`future|...` -> `current|...`) and the
    orchestrator would report the same leaflet as "new" twice. `tab` is
    still tracked on each parsed item for introspection/debugging, but is
    deliberately NOT part of the key or the final {type,from,to,url} output
    -- mirrors Kaufland, where `type` identifies the leaflet slot (KDZ,
    Hyper2) and `tab` (current/future) is separate metadata for the same
    reason.
"""
from __future__ import annotations

import html as _html
import json
import re
import urllib.request
from datetime import date, datetime

DEFAULT_URL = "https://terno.sk/sekcia/7-akciovy-letak"
# Terno shows exactly one leaflet format -- `type` is a constant identity,
# NOT the tab (see module docstring for why the tab must stay out of the key).
LEAFLET_TYPE = "Leaflet"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_TAB_LINK_RE = re.compile(
    r'<a data-bs-target="#(?P<pid>[^"]+)"[^>]*alt="(?P<label>[^"]*)"')
_TAB_PANE_RE = re.compile(r'<div class="tab-pane[^"]*"\s+id="([^"]+)"')
_IFRAME_SRC_RE = re.compile(r'<iframe[^>]*\ssrc="([^"]+)"')
_SLUG_WEEK_RE = re.compile(r'^w(\d{1,2})_')


def fetch(url: str = DEFAULT_URL, timeout: int = 30) -> str:
    """Fetch a page (leaflet listing or Publitas viewer) as a UTF-8 string."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def _tab_type(label: str) -> str | None:
    label = label.strip().lower()
    if label.startswith("aktu"):
        return "current"
    if label.startswith("bud"):
        return "future"
    return None


def parse_tabs(doc: str) -> list[dict]:
    """Parse the two leaflet tabs into [{tab, viewerUrl}].

    viewerUrl is None when that slot has no leaflet published yet.
    """
    tabs = []
    for m in _TAB_LINK_RE.finditer(doc):
        tab_type = _tab_type(_html.unescape(m.group("label")))
        if tab_type:
            tabs.append({"tab": tab_type, "pid": m.group("pid")})

    panes = [(m.start(), m.group(1)) for m in _TAB_PANE_RE.finditer(doc)]
    panes.append((len(doc), None))

    result = []
    for tab in tabs:
        block = ""
        for (start, pid), (end, _) in zip(panes, panes[1:]):
            if pid == tab["pid"]:
                block = doc[start:end]
                break
        viewer = _IFRAME_SRC_RE.search(block)
        result.append({
            "tab": tab["tab"],
            "viewerUrl": _html.unescape(viewer.group(1)) if viewer else None,
        })
    return result


def _extract_json_after(doc: str, marker: str) -> dict:
    """Extract the first balanced {...} object following `marker` in `doc`.

    A brace-counting scan (respecting quoted strings) is used instead of a
    regex because the Publitas payload embeds arbitrary nested JSON
    (including HTML/mustache template strings that themselves contain stray
    braces), which a non-greedy regex would truncate.
    """
    idx = doc.find(marker)
    if idx == -1:
        raise ValueError(f"{marker!r} not found")
    start = doc.index("{", idx)
    depth = 0
    in_string = False
    escape = False
    end = None
    for i in range(start, len(doc)):
        c = doc[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end is None:
        raise ValueError("Unbalanced JSON block")
    return json.loads(doc[start:end])


def parse_publication(doc: str) -> dict:
    """Parse a Publitas viewer page's embedded `var data = {...};` JSON.

    Returns only the fields the worker needs: slug, sourceDocumentTitle and
    the direct PDF download URL (nested under config.downloadPdfUrl).
    """
    data = _extract_json_after(doc, "var data")
    config = data.get("config") or {}
    return {
        "slug": data.get("slug"),
        "sourceDocumentTitle": data.get("sourceDocumentTitle"),
        "pdfUrl": config.get("downloadPdfUrl"),
    }


def _week_bounds(slug: str | None,
                 source_document_title: str | None) -> tuple[str | None, str | None]:
    """Best-effort validity range: Monday-Sunday of the ISO week encoded in
    the slug (e.g. "w27_..." -> week 27), using the year recovered from
    `sourceDocumentTitle` (e.g. "July 01, 2026 13:48" -> 2026).

    See the module docstring: Terno exposes no real from/to anywhere, so
    this is a deterministic proxy, not the retailer's exact offer cycle.
    """
    if not slug or not source_document_title:
        return None, None
    week_match = _SLUG_WEEK_RE.match(slug)
    if not week_match:
        return None, None
    week = int(week_match.group(1))
    try:
        year = datetime.strptime(source_document_title, "%B %d, %Y %H:%M").year
    except ValueError:
        return None, None
    try:
        monday = date.fromisocalendar(year, week, 1)
        sunday = date.fromisocalendar(year, week, 7)
    except ValueError:
        return None, None
    return monday.isoformat(), sunday.isoformat()


def leaflet_key(type_: str | None, valid_from: str | None,
                valid_to: str | None) -> str:
    """Stable novelty key: leaflet identity (LEAFLET_TYPE) + validity range.

    NOT the tab (current/future) -- see module docstring: the same leaflet
    moves from "future" to "current" as weeks pass, and the key must not
    change when that happens.

        leaflet_key("Leaflet", "2026-06-29", "2026-07-05")
            -> "Leaflet|2026-06-29|2026-07-05"
    """
    return f"{type_}|{valid_from}|{valid_to}"


def parse_leaflets(main_doc: str, publication_docs: dict[str, str]) -> list[dict]:
    """Combine the main page's tabs with already-fetched Publitas pages.

    `publication_docs` maps each tab's viewerUrl to that page's HTML (fetched
    by the caller -- this module only parses, it does not orchestrate I/O).
    Tabs with no viewerUrl (nothing published) or a missing/failed fetch are
    skipped.

    Each item: {type, tab, title, validFrom, validTo, viewerUrl, pdfUrl}.
    `type` is the constant LEAFLET_TYPE (leaflet identity, part of the
    novelty key); `tab` ("current"/"future") is separate, informational
    metadata -- see module docstring for why it must stay out of the key.
    """
    leaflets = []
    for tab in parse_tabs(main_doc):
        viewer_url = tab["viewerUrl"]
        if not viewer_url:
            continue
        pub_doc = publication_docs.get(viewer_url)
        if not pub_doc:
            continue
        pub = parse_publication(pub_doc)
        valid_from, valid_to = _week_bounds(pub["slug"], pub["sourceDocumentTitle"])
        leaflets.append({
            "type": LEAFLET_TYPE,
            "tab": tab["tab"],
            "title": pub["slug"],
            "validFrom": valid_from,
            "validTo": valid_to,
            "viewerUrl": viewer_url,
            "pdfUrl": pub["pdfUrl"],
        })
    return leaflets
