"""Local dashboard server: static files from web/ plus the two API functions.

  GET  /api/monitor?days=7|30   Earth Engine data + rules for the season
  GET  /api/evaluate            list of test scenarios
  POST /api/evaluate            rules on a scenario or posted context

Run from anywhere:  python3 web/local_server.py [--port 8765]
"""
import argparse
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

WEB_DIR = os.path.dirname(os.path.abspath(__file__))
REPOSITORY_ROOT = os.path.dirname(WEB_DIR)
sys.path.insert(0, REPOSITORY_ROOT)

from api.evaluate import handle_post, load_scenarios, write_json  # noqa: E402
from api.monitor import handle_request  # noqa: E402


class LocalHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def _route(self):
        return self.path.split("?", 1)[0]

    def do_GET(self):
        route = self._route()
        if route == "/api/monitor":
            status, payload = handle_request(self.path)
            write_json(self, status, payload)
        elif route == "/api/evaluate":
            write_json(self, 200, {"scenarios": sorted(load_scenarios())})
        else:
            # App routes (/farm/..., /product, /qr/...) have no file: serve the single page.
            last_segment = route.rstrip("/").rsplit("/", 1)[-1]
            if route != "/" and "." not in last_segment:
                self.path = "/index.html"
            super().do_GET()

    def do_POST(self):
        if self._route() == "/api/evaluate":
            handle_post(self)
        else:
            self.send_error(404)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve the dashboard and its API locally")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8765")))
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), LocalHandler)
    print(f"Dashboard: http://localhost:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
