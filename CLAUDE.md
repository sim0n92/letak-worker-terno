# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

`letak-worker-terno` is a **ProcMon Process Class** (spec v1.0, type `stdin`)
that parses the Terno leaflet page and reports leaflets (under the
"Aktualny letak" / "Buduci letak" tabs) that are not in the caller-supplied
`knownLeaflets` list. Stateless; metadata + URLs only (no downloads).

- **Protocol contract** lives in the `ProcessMonitor` repo:
  `docs/process-class-specification-v1.md` (org `0ics-srls`).
- **Reference implementation** of a sibling class: `0ics-srls/scip-indexer-dotnet`.
- Sibling worker repos (`letak-worker-kaufland`, `-billa`, `-tesco`, `-lidl`,
  `-coopjednota`) live next to this one under `workers/` and follow the same
  contract -- useful to diff against when something in the protocol layer is
  unclear.

## Contract (do not break)

- stdin: one JSON line with `_action`; stays open for a later `terminate`.
- stdout: JSON Lines, **only** `{type:progress|result|error}` -- never logs.
- logs: `/logs/events.log`, Serilog-style, honor `LOG_LEVEL`.
- exit: 0 success (must emit `result`), 1 error, 2 input error, 4 dependency error.
- `--version` must equal `manifest.json#version`.

## Where to work

- Page fetch + parsing lives in `src/terno.py` (pure functions, unit-tested
  against `tests/fixtures/terno.html` + `tests/fixtures/publitas.html`). The
  protocol plumbing is in `src/main.py` (`ProcessClass`); `LetakWorker.execute()`
  wires the two.
- **Two-stage fetch**: the main page only embeds a Publitas viewer iframe per
  tab; the actual leaflet slug/PDF link live on that second page
  (`terno.parse_tabs` -> per-tab `viewerUrl` -> fetch -> `terno.parse_publication`).
  A single tab's publication fetch failing does not fail the whole run (logged
  and skipped) -- mirrors the two-phase pattern in `letak-worker-billa`.
- **No real validity dates on this site.** Unlike every sibling worker,
  Terno exposes no from/to anywhere -- not on the listing page, not in the
  Publitas JSON. `from`/`to` are approximated as the Monday-Sunday span of
  the ISO week embedded in the Publitas `slug` (e.g. `"w27_..."`), using the
  year recovered from `sourceDocumentTitle`. This is flagged prominently in
  `src/terno.py`'s module docstring and in `PLAN.md` -- read those before
  changing the novelty-key logic, and flag any change to this approximation
  in the `letak-worker` chat room since it affects the group's shared
  `type|from|to` key convention.
- **`type` is a constant, NOT the current/future tab.** `terno.LEAFLET_TYPE`
  ("Leaflet") is what goes in the novelty key; `tab` is separate,
  informational metadata tracked per parsed item but excluded from the key
  and the final output. This matters because the SAME leaflet moves from
  the "future" tab to the "current" tab as weeks pass -- if `type` were the
  tab, that transition would change the key and the orchestrator would
  report the same leaflet as "new" twice. Caught in review by
  kaufland-worker-agent; see the regression test
  `test_novelty_key_is_stable_across_future_to_current_transition` in
  `tests/test_parser.py` before changing this.
- Keep `manifest.json` input/output schemas in sync with `execute`.
- If Terno changes markup (either the tab HTML or Publitas' own page), refresh
  the relevant fixture and keep the parser tests green.

## Build / test

```bash
python tests/test_parser.py                         # offline parser tests
python tests/test_protocol.py                        # protocol + execute tests
docker build -t letak-worker-terno .                 # production image
docker build -f Dockerfile.test -t lwt-test .        # in-container tests
```
