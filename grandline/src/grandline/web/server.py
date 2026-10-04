"""
WHAT THIS FILE DOES
Serves the tracker's page on this machine only: http://127.0.0.1:8765/

WHY IT EXISTS
Stage 5. The PRD asks for "the simplest thing that renders the record --
a local page, not a product surface". So: Python's built-in http.server,
no framework, no dependency. One route, one page, rebuilt from the
database on every request.

WHY 127.0.0.1 AND NOTHING ELSE
Bound to the loopback address, the page is reachable from this computer
only -- not from other devices on the network. There's no login, so it
must not be exposed.

WHY THE PAGE NEVER WRITES
Each request opens the database READ-ONLY (SQLite's mode=ro). The
watcher is the only writer; the page can't change the record even by
accident. The schema is created or upgraded once, at startup.

RUN IT WITH
    python -m grandline.web                  # opens your browser
    python -m grandline.web --no-browser --port 8765
Run the watcher alongside it; the page refreshes every 30 seconds.
"""

import argparse
import logging
import sqlite3
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from grandline.capture.config import load_config_or_exit
from grandline.capture.store import connect
from grandline.web.page import load_page, render_page

log = logging.getLogger("grandline.web")
HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def open_read_only(db_path: Path) -> sqlite3.Connection:
    """WHY THIS FUNCTION EXISTS: the page must never write (see top)."""
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def make_handler(db_path: Path, clock=lambda: datetime.now(timezone.utc)):
    """Build the request handler class bound to one database."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            url = urlsplit(self.path)
            if url.path != "/":
                self._send(404, "text/plain; charset=utf-8", b"Not found")
                return
            leader = parse_qs(url.query).get("leader", [None])[0]
            conn = open_read_only(db_path)
            try:
                html = render_page(load_page(conn, leader), clock())
            finally:
                conn.close()
            self._send(200, "text/html; charset=utf-8", html.encode("utf-8"))

        def _send(self, status: int, ctype: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args) -> None:
            # Default prints every request to stderr; once every 30s is noise.
            log.debug(fmt, *args)

    return Handler


def serve(db_path: Path, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """Create the server (not yet running). Port 0 picks a free one."""
    conn = connect(db_path)  # create / migrate the schema once, as a writer
    conn.close()
    return ThreadingHTTPServer((HOST, port), make_handler(db_path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m grandline.web",
                                     description="Serve the Grand Line Meta page locally.")
    parser.add_argument("--config", default="grandline.toml", type=Path)
    parser.add_argument("--port", default=DEFAULT_PORT, type=int)
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = parser.parse_args(argv)

    config = load_config_or_exit(args.config)
    server = serve(config.db_path, args.port)
    url = f"http://{HOST}:{server.server_port}/"
    print(f"Grand Line Meta: {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
