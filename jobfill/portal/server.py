"""HTTP server for the setup portal: 127.0.0.1 only, with a random key per database.

The key is in the link the portal opens (``/?t=...``) and the page sends it back on every API call;
requests naming another Host are refused. Other sites open in the same browser can therefore neither
read nor change the profile. The key is kept in the database's meta table, so a tab left open saves
again once the portal is restarted on the same port, while a tab opened for another database is
refused. The server is single-threaded on purpose: the store's SQLite connection belongs to the
thread that serves it.
"""

from __future__ import annotations

import hmac
import json
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .. import db
from ..resolver import dumps
from ..store import ValidationError
from . import form

STATIC = Path(__file__).resolve().parent / "static"
FILES = {"/": "index.html", "/index.html": "index.html", "/portal.css": "portal.css", "/portal.js": "portal.js"}
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8"}
DEFAULT_PORT = 8765


class PortalServer(HTTPServer):
    # On Windows SO_REUSEADDR lets a second server take a port that is in use.
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, store, root: Path, port: int = DEFAULT_PORT, *, database: str = "", keep_open: bool = False,
                 on_event=None):
        super().__init__(("127.0.0.1", port), Handler)
        self.store, self.root, self.database = store, Path(root), database
        self.token = db.meta_value(store.conn, "portal_key", lambda: secrets.token_urlsafe(18))
        self.keep_open = keep_open
        self.on_event = on_event or (lambda event: None)
        self.submitted: dict | None = None

    @property
    def port(self) -> int:
        return self.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?t={self.token}"


class Handler(BaseHTTPRequestHandler):
    server: PortalServer
    server_version = "jobfill-portal"

    def log_message(self, format, *args):   # no access log on the candidate's terminal
        pass

    # ------------------------------------------------------------------ helpers
    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _allowed(self, api: bool) -> bool:
        port = self.server.port
        if self.headers.get("Host", "") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self._json(403, {"error": "this portal answers only on 127.0.0.1"})
            return False
        if api and not hmac.compare_digest(self.headers.get("X-Jobfill-Token", ""), self.server.token):
            self._json(403, {"error": "open the portal with the link `jobfill portal` printed"})
            return False
        return True

    # ------------------------------------------------------------------ routes
    def do_GET(self):
        path = urlsplit(self.path).path
        if path in FILES:
            if not self._allowed(api=False):
                return
            file = STATIC / FILES[path]
            return self._send(200, file.read_bytes(), TYPES[file.suffix])
        if path == "/api/form":
            if self._allowed(api=True):
                srv = self.server
                self._json(200, {"schema": form.schema(srv.store), "state": form.state(srv.store, srv.root),
                                 "database": srv.database, "keep_open": srv.keep_open})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        url = urlsplit(self.path)
        if url.path not in ("/api/save", "/api/upload"):
            return self._json(404, {"error": "not found"})
        if not self._allowed(api=True):
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > form.MAX_UPLOAD + 1024 * 1024:
            return self._json(413, {"error": "too large"})
        body = self.rfile.read(length)
        srv = self.server
        try:
            if url.path == "/api/upload":
                key = parse_qs(url.query).get("key", [""])[0]
                path = form.save_upload(srv.root, key, unquote(self.headers.get("X-Filename", "")), body)
                srv.on_event({"attached": key, "file": path})
                return self._json(200, {"path": path})
            data = json.loads(body or b"{}")
            submit = bool(data.get("submit"))
            result = form.save(srv.store, srv.root, data, submit=submit)
        except (ValidationError, ValueError) as e:
            return self._json(400, {"error": str(e)})
        self._json(200, result)
        if not result["ok"]:
            return
        status = result["state"]["status"]
        event = {"saved": result["changed"], "setup_complete": status["setup_complete"]}
        if submit:
            event = {"submitted": True, **event, "missing_required": status["missing_required"],
                     "missing_recommended": status["missing_recommended"]}
            srv.submitted = event
        if submit or any(result["changed"].values()):
            srv.on_event(event)
        if submit and not srv.keep_open:
            threading.Thread(target=srv.shutdown, daemon=True).start()


def _print(event: dict) -> None:
    print(dumps(event), flush=True)


def serve(store, root: Path, *, database: str, port: int = DEFAULT_PORT, open_browser: bool = True,
          keep_open: bool = False, on_event=_print) -> dict | None:
    """Serve until the candidate submits (or Ctrl+C); returns the submit event, if any.
    ``database``: the file answers are saved to, as the page shows it."""
    opts = {"database": database, "keep_open": keep_open, "on_event": on_event}
    try:
        server = PortalServer(store, root, port, **opts)
    except OSError:     # port taken: any free one
        server = PortalServer(store, root, 0, **opts)
    on_event({"portal": server.url, "database": database,
              "note": "Open the link to fill your profile. It stops when you click Submit"
                      + (" (--keep-open: stop it with Ctrl+C)." if keep_open else "; Ctrl+C stops it earlier.")})
    if open_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        on_event({"stopped": True})
    finally:
        server.server_close()
    return server.submitted
