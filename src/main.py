#!/usr/bin/env python3
"""
letak-worker-terno -- Terno leaflet (letak) scraper.

ProcMon Process Class, spec v1.0, type=stdin.

This module is a SELF-CONTAINED implementation of the ProcMon stdin/stdout
protocol (see ProcessMonitor/docs/process-class-specification-v1.md). The
protocol plumbing below is complete; the actual scraping lives in
LetakWorker.execute() and is the only part you need to fill in.

Contract recap:
  - stdin  : one JSON line with `_action` + params (stays open for `terminate`)
  - stdout : JSON Lines, ONLY {type:progress|result|error} -- never logs
  - logs   : /logs/events.log (Serilog-style), honoring LOG_LEVEL
  - exit   : 0 success (must have emitted result), 1 error, 2 input error,
             4 dependency error
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import terno

# --- Exit codes (spec section 4.7) ---------------------------------------
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INPUT = 2
EXIT_TIMEOUT = 3
EXIT_DEPENDENCY = 4

LOG_DIR = Path(os.environ.get("LOG_DIR", "/logs"))
LOG_FILE = LOG_DIR / "events.log"

_LEVEL_MAP = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}
_LEVEL_ABBREV = {
    logging.DEBUG: "DBG",
    logging.INFO: "INF",
    logging.WARNING: "WRN",
    logging.ERROR: "ERR",
    logging.CRITICAL: "ERR",
}


class _SerilogFormatter(logging.Formatter):
    """Renders `2026-02-02 10:30:00.123 [INF] message` per spec section 4.5.3."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S.")
        ts += f"{int(record.msecs):03d}"
        level = _LEVEL_ABBREV.get(record.levelno, "INF")
        return f"{ts} [{level}] {record.getMessage()}"


class ManagedError(Exception):
    """A handled error: emits {type:error} and exits with `exit_code`."""

    def __init__(self, message: str, *, code: str | None = None,
                 exit_code: int = EXIT_ERROR, data: dict | None = None):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.data = data


def _find_manifest() -> Path | None:
    """manifest.json sits next to main.py in the container (/app), at repo root locally."""
    here = Path(__file__).resolve().parent
    for candidate in (here / "manifest.json", here.parent / "manifest.json"):
        if candidate.is_file():
            return candidate
    return None


def _manifest_version() -> str:
    mf = _find_manifest()
    if mf:
        try:
            return json.loads(mf.read_text(encoding="utf-8")).get("version", "0.0.0")
        except Exception:
            pass
    return "0.0.0"


class ProcessClass:
    """Base ProcMon stdin/stdout protocol handler. Subclass and override execute()."""

    def __init__(self) -> None:
        self.logger = self._setup_logging()
        self._input_data: dict[str, Any] = {}
        self._meta: dict[str, Any] = {}
        # Set by the stdin watcher when a terminate message arrives mid-execution.
        self.terminate_requested = threading.Event()

    # --- stdout protocol (the ONLY things allowed on stdout) ---

    @staticmethod
    def _emit(obj: dict) -> None:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def report(self, percent: int | None, message: str) -> None:
        """Emit a progress line. percent=None means indeterminate (message required)."""
        self._emit({"type": "progress", "percent": percent, "message": message})

    def _emit_result(self, data: dict) -> None:
        self._emit({"type": "result", "data": data})

    def _emit_error(self, message: str, code: str | None, data: dict | None) -> None:
        msg = {"type": "error", "message": message}
        if code:
            msg["code"] = code
        if data:
            msg["data"] = data
        self._emit(msg)

    # --- logging (to file, never stdout) ---

    def _setup_logging(self) -> logging.Logger:
        level = _LEVEL_MAP.get(os.environ.get("LOG_LEVEL", "Info").lower(), logging.INFO)
        logger = logging.getLogger("letak-worker-terno")
        logger.setLevel(level)
        logger.handlers.clear()
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            handler: logging.Handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        except Exception:
            # Fallback to stderr if /logs is not mounted (e.g. local dev).
            handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_SerilogFormatter())
        logger.addHandler(handler)
        logger.propagate = False
        return logger

    # --- stdin terminate watcher (spec section 4.1: stdin stays open) ---

    def _watch_stdin_for_terminate(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("_action") == "terminate":
                self.logger.info("Terminate received via stdin during execution")
                self.terminate_requested.set()
                return

    def _start_terminate_watcher(self) -> None:
        t = threading.Thread(target=self._watch_stdin_for_terminate, daemon=True)
        t.start()

    # --- entrypoint ---

    def run(self, argv: list[str] | None = None) -> int:
        argv = sys.argv[1:] if argv is None else argv
        if argv and argv[0] == "--version":
            print(_manifest_version())
            return EXIT_OK

        first_line = sys.stdin.readline()
        if not first_line.strip():
            self.logger.error("Empty stdin")
            self._emit_error("Empty stdin: expected a JSON message", "EMPTY_INPUT", None)
            return EXIT_INPUT

        try:
            message = json.loads(first_line)
        except json.JSONDecodeError as e:
            self.logger.error("Invalid JSON on stdin: %s", e)
            self._emit_error(f"Invalid JSON on stdin: {e}", "INVALID_JSON", None)
            return EXIT_INPUT

        action = message.get("_action")
        self._meta = message.get("_meta", {}) or {}
        self._input_data = {k: v for k, v in message.items()
                            if not k.startswith("_")}

        try:
            if action == "execute":
                self._start_terminate_watcher()
                return self._handle_execute()
            if action == "terminate":
                return self._handle_terminate()
            raise ManagedError(
                f"Unknown action: {action!r}", code="UNKNOWN_ACTION",
                exit_code=EXIT_INPUT)
        except ManagedError as e:
            self.logger.error("%s%s", e, f" {e.data}" if e.data else "")
            self._emit_error(str(e), e.code, e.data)
            return e.exit_code
        except Exception as e:  # unhandled -> stderr + exit 1
            self.logger.error("Unhandled error: %s", e)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return EXIT_ERROR

    def _handle_execute(self) -> int:
        self.report(0, "Starting...")
        data = self.execute(self._input_data)
        self.report(100, "Done")
        self._emit_result(data)
        return EXIT_OK

    def _handle_terminate(self) -> int:
        self.report(0, "Termination requested...")
        cleaned = self.terminate()
        self._emit_result({"cleaned": cleaned})
        return EXIT_OK

    # --- overridable hooks ---

    def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def terminate(self) -> bool:
        return True


class LetakWorker(ProcessClass):
    """Terno leaflet scraper."""

    def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        output_path = input_data.get("outputPath")
        if not output_path:
            raise ManagedError(
                "Missing required input field: outputPath",
                code="INVALID_INPUT", exit_code=EXIT_INPUT)

        url = input_data.get("pageUrl") or terno.DEFAULT_URL
        known = set(input_data.get("knownLeaflets") or [])
        wanted = {str(t).lower() for t in (input_data.get("types") or [])}
        self.logger.info("Scrape requested -> %s (known=%d)", url, len(known))

        # 1. Fetch the leaflet listing page (the "Aktualny/Buduci letak" tabs).
        self.report(10, f"Fetching {url}")
        try:
            main_doc = terno.fetch(url)
        except Exception as e:  # network / upstream -> dependency error (exit 4)
            raise ManagedError(
                f"Failed to fetch leaflet page: {e}",
                code="FETCH_FAILED", exit_code=EXIT_DEPENDENCY) from e

        if self.terminate_requested.is_set():
            raise ManagedError("Terminated during fetch", code="TERMINATED",
                               exit_code=EXIT_ERROR)

        # 2. Each tab only embeds a Publitas viewer iframe; the leaflet's
        #    slug/validity/PDF link live on that second page, so fetch each
        #    tab's viewer page. A single tab failing to fetch does not fail
        #    the whole run (e.g. Publitas hiccup on one of two tabs).
        tabs = [t for t in terno.parse_tabs(main_doc) if t.get("viewerUrl")]
        self.report(40, f"Fetching {len(tabs)} publication page(s)")
        publication_docs: dict[str, str] = {}
        for tab in tabs:
            if self.terminate_requested.is_set():
                raise ManagedError("Terminated during publication fetch",
                                   code="TERMINATED", exit_code=EXIT_ERROR)
            viewer_url = tab["viewerUrl"]
            try:
                publication_docs[viewer_url] = terno.fetch(viewer_url)
            except Exception as e:
                self.logger.warning("Failed to fetch publication page %s: %s",
                                     viewer_url, e)

        # 3. Parse into {type, from, to, url} PDF entries.
        self.report(70, "Parsing leaflets")
        leaflets = terno.parse_leaflets(main_doc, publication_docs)
        self.logger.info("Found %d leaflet(s)", len(leaflets))
        items = [
            {
                "type": lf.get("type"),
                "from": lf.get("validFrom"),
                "to": lf.get("validTo"),
                "url": lf["pdfUrl"],
            }
            for lf in leaflets if lf.get("pdfUrl")
        ]

        # 4. Keep only leaflets that (a) match the optional type filter AND
        #    (b) are new. Novelty is decided by a STABLE key (tab type +
        #    approximate validity week), not by the PDF URL (which carries a
        #    UUID that changes on re-upload).
        matched = [
            it for it in items
            if (not wanted or (it["type"] or "").lower() in wanted)
            and terno.leaflet_key(it["type"], it["from"], it["to"]) not in known
        ]
        self.logger.info(
            "%d leaflet(s) match (types=%s, known=%d): %s",
            len(matched), sorted(wanted) or "*", len(known),
            [terno.leaflet_key(it["type"], it["from"], it["to"]) for it in matched])

        # 5. Write the result: a small envelope + the matched leaflets.
        self.report(90, "Writing output")
        result_doc = {
            "source": url,
            "types": sorted(wanted) or None,
            "hasNew": bool(matched),
            "count": len(matched),
            "leaflets": matched,
        }
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result_doc, ensure_ascii=False, indent=2),
                       encoding="utf-8")

        return {
            "outputPath": str(out),
            "count": len(matched),
            "hasNew": bool(matched),
            "leaflets": matched,
            "fileSize": out.stat().st_size,
        }

    def terminate(self) -> bool:
        self.terminate_requested.set()
        self.logger.info("Cleanup complete")
        return True


if __name__ == "__main__":
    sys.exit(LetakWorker().run())
