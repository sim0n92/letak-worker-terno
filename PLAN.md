# letak-worker-terno — Implementation Plan

## Overview
ProcMon Process Class (spec v1.0, type: `stdin`) that detects Terno leaflets
under the "Aktualny letak" / "Buduci letak" tabs and reports new ones.

## Design decisions (locked)
- **Novelty**: stateless -- caller passes `knownLeaflets` (keys); worker
  returns the ones not listed + `hasNew`.
- **Novelty key**: `type|from|to` (e.g. `Leaflet|2026-06-29|2026-07-05`).
  `type` is the **constant** `terno.LEAFLET_TYPE` ("Leaflet"), NOT the
  current/future tab. Caught in review (kaufland-worker-agent, 2026-07-02):
  the same leaflet moves from the "future" tab to the "current" tab as
  weeks pass, so if `type` were the tab, that transition would change the
  key (`future|w28dates` -> `current|w28dates`) and the orchestrator would
  report the same leaflet as "new" twice. `tab` ("current"/"future") is
  still tracked on each parsed item for introspection, but is deliberately
  excluded from both the key and the final `{type,from,to,url}` output --
  mirrors Kaufland, where `type` (KDZ/Hyper2) is leaflet identity and `tab`
  is separate metadata for the same reason. See
  `ParseLeafletsTests.test_novelty_key_is_stable_across_future_to_current_transition`
  in `tests/test_parser.py` for the regression test.
- **Output**: PDF entries `{type, from, to, url}` -- no viewer URLs.
- **Scope**: URLs only; no PDF/asset downloading (left to a downstream step).
- **Two-stage fetch**: the terno.sk page only embeds a Publitas iframe per
  tab; slug/PDF link require a second fetch of that Publitas page.

## OPEN QUESTION -- approximate validity dates (raised in `letak-worker` chat)
Terno's page carries **no validity metadata at all** -- not on the listing
page, not in the Publitas viewer's embedded JSON (`var data = {...}`, see
`src/terno.py`). Confirmed by inspecting both the live page and the Publitas
payload: the only date-shaped field is `sourceDocumentTitle` (the source
PDF's *upload* timestamp, e.g. `"July 01, 2026 13:48"` -- not the offer's
start), and the only other lead is the `slug` (e.g.
`"w27_letak_terno_200x297mm_online-<hash>"`), whose `w27` prefix is the ISO
week the leaflet was built for.

Current implementation: `from`/`to` = Monday-Sunday of that ISO week (year
taken from `sourceDocumentTitle`). This is a **deterministic proxy**, not
Terno's real (possibly Wed-Tue or other) offer cycle -- verified against one
live sample (upload `"July 01, 2026"` falls inside the derived
`2026-06-29..2026-07-05` window, which is at least self-consistent, but
there is no ground truth on-site to confirm the *exact* day boundaries).

This still satisfies the novelty contract (the key changes every week the
slug's week number changes, and is stable across re-scrapes of the same
leaflet), but it is a materially weaker guarantee than every sibling worker
(Kaufland/Tesco/Billa/Lidl all expose the retailer's real, exact validity
range somewhere). Flagged in the `letak-worker` chat room for Kaufland/Simon
to confirm this is acceptable, or to point at a better date source if one
exists that this pass missed.

## Status — DONE
```
manifest.json     # spec v1.0, knownLeaflets in / hasNew+leaflets out ....... DONE
Dockerfile        # python:3.12-slim, stdin entrypoint .................... DONE
Dockerfile.test   # protocol + parser tests (offline, in-container) ........ DONE
src/main.py       # ProcMon stdin/stdout protocol wrapper + LetakWorker ... DONE
src/terno.py      # fetch + parse_tabs + parse_publication + parse_leaflets DONE
tests/            # test_protocol.py, test_parser.py, fixtures/*.html ...... DONE
```
Verified live against the real page (current leaflet found, `hasNew: true`
on first run, `hasNew: false` once its key is fed back as known) and offline
against the fixtures (19 parser tests + 15 protocol tests, all green).

## How it works (implemented)
- Plain HTTP fetch (`urllib`) -- both pages are static HTML, no headless
  browser needed.
- `parse_tabs`: locate the two `data-bs-target="#pageN"` tab links (labelled
  "Aktualny.../Buduci..."), then the matching `tab-pane` by id, then that
  pane's `<iframe src="...">` if present.
- `parse_publication`: brace-counting scan (not a greedy/non-greedy regex --
  the payload nests arbitrary JSON, including HTML template strings with
  stray braces) to extract the `var data = {...};` block, then
  `slug` / `sourceDocumentTitle` / `config.downloadPdfUrl`.
- `_week_bounds`: derives the approximate validity range (see OPEN QUESTION
  above).
- `parse_leaflets`: combines both stages; tabs with no iframe or no
  successfully-fetched publication page are skipped, not errored.

## Open / future ideas
1. **Real validity dates** -- if Terno's site or Publitas ever expose an
   actual offer start/end (e.g. a future page redesign, or an API response
   this pass didn't check), switch `_week_bounds` for that source and update
   the OPEN QUESTION section above.
2. **Multiple concurrent leaflets per tab** -- not observed (each tab embeds
   at most one iframe); if Terno ever ships more than one leaflet per tab,
   `parse_tabs`/`parse_leaflets` need to become tile-based like Kaufland's
   parser instead of one-iframe-per-tab.
3. **Fixture refresh** -- `tests/fixtures/terno.html` and
   `tests/fixtures/publitas.html` are live snapshots; refresh both if Terno
   or Publitas change markup, and keep the parser tests green.

## Build / test
```bash
python tests/test_parser.py && python tests/test_protocol.py   # local
docker build -t letak-worker-terno .
docker build -f Dockerfile.test -t letak-worker-terno-test .
```
