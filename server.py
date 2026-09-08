#!/usr/bin/env python3
"""Loopback-only GUI. No dependencies and no credential persistence."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import webbrowser

from botctl import settings

ROOT = Path(__file__).resolve().parent
NONCE = secrets.token_urlsafe(32)
BUSY = threading.Lock()
CFG = (json.loads((ROOT / "settings.example.json").read_text())
       if "--demo" in sys.argv else settings())
PORT = CFG["port"]
ORIGIN = "http://127.0.0.1:" + str(PORT)
DEMO = "--demo" in sys.argv
CREATED = {}
CREATE_ATTEMPTED = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass  # Never log incoming headers, URLs, or credential bodies.

    def send(self, code, body, mime="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def trusted(self):
        return self.headers.get("Host") == "127.0.0.1:" + str(PORT)

    def do_GET(self):
        if not self.trusted() or self.headers.get("Sec-Fetch-Site") == "cross-site":
            return self.send(403, {"error": "Open this tool directly on 127.0.0.1."})
        if self.path == "/session":
            return self.send(200, {"nonce": NONCE, "demo": DEMO,
                                   "company": CFG["company_name"], "target": CFG["ssh_target"], "workspace_host": CFG["workspace_host"]})
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8")}
        if self.path not in assets:
            return self.send(404, {"error": "Not found"})
        name, mime = assets[self.path]
        self.send(200, (ROOT / "public" / name).read_bytes(), mime)

    def do_POST(self):
        global CREATE_ATTEMPTED
        if (not self.trusted() or self.headers.get("Origin") != ORIGIN or
                not secrets.compare_digest(self.headers.get("X-Builder-Token", ""), NONCE)):
            return self.send(403, {"error": "Reload the local tool to establish a valid session."})
        if self.path not in ("/api/inspect", "/api/create", "/api/deploy"):
            return self.send(404, {"error": "Not found"})
        if self.headers.get("Content-Type") != "application/json":
            return self.send(415, {"error": "JSON required"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 16000:
                return self.send(413, {"error": "Request too large or empty"})
            self.connection.settimeout(10)
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                return self.send(400, {"error": "Invalid request"})
        except Exception:
            return self.send(400, {"error": "Invalid request"})
        if not BUSY.acquire(blocking=False):
            return self.send(409, {"error": "Another operation is running. Wait for it to finish."})
        try:
            action = self.path.rsplit("/", 1)[1]
            if not DEMO and action == "create":
                if CREATE_ATTEMPTED:
                    return self.send(409, {"ok": False, "error": "App creation was already attempted in this session. Check Slack before starting another session."})
                if data.get("confirm_create") is not True:
                    return self.send(400, {"ok": False, "error": "Confirm app creation first."})
                CREATE_ATTEMPTED = True
            if not DEMO and action == "deploy":
                original = CREATED.get(data.get("app_id"))
                if not original or any(data.get(k) != original.get(k) for k in ("name", "agent_id", "channel", "private")):
                    return self.send(409, {"ok": False, "error": "Deploy only the new app created in this session, with its original agent and channel."})
            if DEMO:
                result = ({"ok": True, "agents": [{"id": "11111111-1111-4111-8111-111111111111", "name": "Example GM (demo)"}],
                           "allowlist": ["DEMO USER"], "bots": []} if action == "inspect" else
                          {"ok": False, "error": "Preview mode: Slack creation and VPS deployment are disabled."})
            else:
                proc = subprocess.run([sys.executable, str(ROOT / "botctl.py"), action],
                                      input=json.dumps(data), capture_output=True, text=True,
                                      cwd=ROOT, timeout=240)
                result = json.loads(proc.stdout) if proc.returncode == 0 else {"ok": False, "error": "Helper failed; inspect before retrying."}
                if action == "create" and result.get("ok"):
                    CREATED[result["app_id"]] = {k: data.get(k) for k in ("name", "agent_id", "channel", "private")}
            self.send(200, result)
        except Exception:
            self.send(500, {"ok": False, "error": "Operation outcome is uncertain. Check app and VPS status before retrying."})
        finally:
            data.clear()
            BUSY.release()


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("Paperclip Bot Builder: " + ORIGIN + (" (preview; deployment disabled)" if DEMO else ""), flush=True)
    if "--open" in sys.argv:
        webbrowser.open(ORIGIN)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
