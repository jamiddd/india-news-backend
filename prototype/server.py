"""Local prototype server. No database, no Redis: serves the prototype pages
with clean URLs, static assets, and fake JSON for the endpoints the site calls.

    python3 prototype/server.py                 # http://127.0.0.1:8765, this computer only
    python3 prototype/server.py 8765 0.0.0.0    # also reachable from other devices on the network
"""
import json, mimetypes, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765

def data():
    return json.loads((ROOT / "data" / "sample.json").read_text())

class H(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="text/html; charset=utf-8"):
        if isinstance(body, str): body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0].rstrip("/") or "/"
        if p == "/ping": return self.send(200, "ok: the prototype server can be reached from " + self.client_address[0] + "\n", "text/plain; charset=utf-8")
        if p == "/api/sample": return self.send(200, json.dumps(data()), "application/json")
        if p == "/api/v1/public/hero": return self.send(200, json.dumps({"stories": data()["stories"]}), "application/json")
        if p.startswith("/static/"):
            f = (ROOT / "static" / p[len("/static/"):]).resolve()
            if ROOT / "static" in f.parents and f.is_file():
                return self.send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
            return self.send(404, "not found")
        if p.startswith("/story/"): p = "/story"
        if p.startswith("/source/") or p.startswith("/topic/"): p = "/list"
        if p.startswith("/timeline/"): p = "/timeline"
        if p.startswith("/explainer/"): p = "/explainer"
        name = "home" if p == "/" else p.lstrip("/").removesuffix(".html")
        f = ROOT / "pages" / f"{name}.html"
        if "/" not in name and f.is_file(): return self.send(200, f.read_text())
        self.send(404, "not found")

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.send(200, json.dumps({"ok": True, "url": "/donation-thanks"}), "application/json")

    def log_message(self, fmt, *args):
        # Show requests from other devices (a phone), so a connection problem is easy to diagnose.
        ip = self.client_address[0]
        if ip not in ("127.0.0.1", "::1"):
            print(f"[{ip}] {self.command} {self.path}", flush=True)

def lan_ip():
    """This machine's address on the local network (the one a phone on the same Wi-Fi can reach)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))       # no packet is sent; this only picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()

if __name__ == "__main__":
    # This computer only by default. Pass 0.0.0.0 as a second argument to let other devices on the
    # same network connect (python3 prototype/server.py 8765 0.0.0.0).
    host = sys.argv[2] if len(sys.argv) > 2 else "127.0.0.1"
    print(f"On this computer:  http://127.0.0.1:{PORT}")
    if host == "0.0.0.0":
        print(f"On your phone:     http://{lan_ip()}:{PORT}   (same Wi-Fi network)")
    ThreadingHTTPServer((host, PORT), H).serve_forever()
