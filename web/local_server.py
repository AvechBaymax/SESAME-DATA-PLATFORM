import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

WEB_DIR = os.path.dirname(os.path.abspath(__file__))
REPOSITORY_ROOT = os.path.dirname(WEB_DIR)
sys.path.insert(0, REPOSITORY_ROOT)
from api.monitor import handle_request


class LocalHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def do_GET(self):
        if self.path.split("?", 1)[0] != "/api/monitor":
            return super().do_GET()

        import json

        status, payload = handle_request(self.path)
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8765), LocalHandler)
    print("Dashboard: http://localhost:8765")
    server.serve_forever()
