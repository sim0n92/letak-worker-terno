# letak-worker-terno

ProcMon Process Class (spec v1.0, type: `stdin`) that detects Terno
promotional leaflets ("letaky") and reports the ones that are new.

> Protocol contract: `ProcessMonitor/docs/process-class-specification-v1.md`.
> Reference implementation of another class: `0ics-srls/scip-indexer-dotnet`.

## How It Works

1. Receives a JSON message on stdin with `_action: "execute"`.
2. Fetches the leaflet page (`https://terno.sk/sekcia/7-akciovy-letak`) and
   reads the two tabs **Aktualny letak** (current) and **Buduci letak**
   (future). Each tab embeds at most one Publitas flip-book viewer
   (`<iframe src="https://view.publitas.com/...">`); an empty tab means no
   leaflet is published for that slot yet (most weeks there is no future
   leaflet).
3. For each populated tab, fetches that Publitas viewer page and reads its
   embedded `var data = {...};` JSON for the leaflet's `slug`,
   `sourceDocumentTitle` and direct PDF download URL.
4. Terno's pages expose **no explicit validity dates** anywhere (unlike
   Kaufland/Tesco/Billa/Lidl). `from`/`to` are therefore **approximated** as
   the Monday-Sunday span of the ISO week encoded in the slug (e.g.
   `"w27_..."` -> week 27), using the year recovered from
   `sourceDocumentTitle`. This is a deterministic proxy, not Terno's exact
   offer cycle -- see `src/terno.py` module docstring and `PLAN.md` for
   details and the reasoning.
5. Each leaflet has a **stable key** = `type|from|to` (e.g.
   `Leaflet|2026-06-29|2026-07-05`) built from that approximate range. `type`
   is the constant `"Leaflet"`, NOT the current/future tab -- the same
   leaflet moves from "future" to "current" as weeks pass, and the key must
   not change when that happens (the tab is still recorded per-item for
   introspection, just kept out of the key and the final output). Any
   leaflet whose key is not in the `knownLeaflets` input list is reported as
   **new**.
6. Writes the result to `outputPath` and emits a `result` with `hasNew` +
   `leaflets` (each entry `{type, from, to, url}`, where `url` is the
   downloadable PDF).
7. Logs diagnostics to `/logs/events.log` (honoring `LOG_LEVEL`).

The worker is **stateless**: novelty is decided purely from the
`knownLeaflets` list supplied by the caller.

## Build

```bash
docker build -t letak-worker-terno .
```

## Test

```bash
docker build -f Dockerfile.test -t letak-worker-terno-test .
```

## Run

```bash
echo '{"_action":"execute","outputPath":"/out/leaflets.json","knownLeaflets":[]}' \
  | docker run -i -v "$PWD/out:/out" -v "$PWD/logs:/logs" letak-worker-terno
```

## Protocol

### Input (stdin JSON)

| Field           | Type     | Required | Description |
|-----------------|----------|----------|-------------|
| `_action`       | string   | yes      | `execute` or `terminate` (system-injected) |
| `outputPath`    | string   | yes      | Absolute path for the result JSON |
| `knownLeaflets` | string[] | no       | Keys already seen (`type|from|to`); anything not listed is "new" |
| `types`         | string[] | no       | Case-insensitive filter; Terno only ever produces `"Leaflet"` (kept for parity with the shared worker contract) |
| `pageUrl`       | string   | no       | Override leaflet page URL (default `https://terno.sk/sekcia/7-akciovy-letak`) |
| `customData`    | object   | no       | Source-specific hints |
| `_meta`         | object   | no       | Execution metadata (system-injected) |

### Output (stdout JSON Lines)

- `{"type":"progress","percent":N,"message":"..."}` -- progress (percent may be `null`)
- `{"type":"result","data":{...}}` -- final result with:
  `outputPath`, `count`, `hasNew`, `leaflets[]`, `fileSize`
- `{"type":"error","code":"...","message":"...","data":{...}}` -- managed error

Each `leaflets` entry is `{type, from, to, url}`, where `url` is the
**directly downloadable PDF**. The full leaflet list is also written to
`outputPath`. Novelty key = `type|from|to`; feed the keys of leaflets you've
handled back via `knownLeaflets` on the next run.

### Result example

```json
{"type":"result","data":{"count":1,"hasNew":true,
  "leaflets":[{"type":"Leaflet","from":"2026-06-29","to":"2026-07-05",
          "url":"https://view.publitas.com/75539/3197923/pdfs/....pdf?response-content-disposition=attachment%3B+..."}],
  "outputPath":"/out/leaflets.json","fileSize":453}}
```

A downstream program just GETs each `leaflets[].url` (public, no auth):

```python
import json, urllib.request
data = json.loads(result_stdout)["data"]        # or json.load(open(outputPath))
for item in data["leaflets"]:
    url = item["url"]
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as r, open(url.split("?")[0].rsplit("/", 1)[-1], "wb") as f:
        f.write(r.read())
```

### Exit Codes

| Code | Meaning |
|------|---------|
| 0    | Success (a `result` was emitted) |
| 1    | Generic error |
| 2    | Input error |
| 3    | Timeout (killed by wrapper) |
| 4    | Dependency error (upstream/network) |

## Environment

| Variable     | Default | Description |
|--------------|---------|-------------|
| `LOG_LEVEL`  | `Info`  | `Debug` / `Info` / `Warning` / `Error` |
| `EXECUTION_ID` | –     | Execution UUID (system-provided) |
| `LOG_DIR`    | `/logs` | Log directory (override for local dev) |

## Layout

```
letak-worker-terno/
├── manifest.json         # ProcMon manifest (spec v1.0)
├── Dockerfile            # Production image
├── Dockerfile.test       # Protocol + parser test image
├── requirements.txt      # stdlib-only at runtime
├── src/
│   ├── main.py           # stdin/stdout protocol wrapper + LetakWorker
│   └── terno.py          # page fetch + leaflet parsing (pure, testable)
└── tests/
    ├── test_protocol.py  # protocol + execute behaviour (synthetic fixtures)
    ├── test_parser.py    # offline parser tests (real captured fixtures)
    └── fixtures/
        ├── terno.html    # captured https://terno.sk/sekcia/7-akciovy-letak
        └── publitas.html # captured Publitas viewer page for that leaflet
```

## Local development

```bash
python tests/test_parser.py     # offline parser tests (uses real fixtures)
python tests/test_protocol.py   # protocol + execute tests (offline, synthetic fixtures)
```
