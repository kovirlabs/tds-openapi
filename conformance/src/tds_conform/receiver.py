"""A webhook receiver the kit runs locally to capture event deliveries."""

import json
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class Delivery:
    headers: dict[str, str]
    body: bytes
    received_at: float

    @property
    def event(self):
        try:
            return json.loads(self.body)
        except ValueError:
            return None


class Receiver:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, public_url: str | None = None):
        self.deliveries: list[Delivery] = []
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                receiver.deliveries.append(Delivery(dict(self.headers.items()), body, time.time()))
                self.send_response(204)
                self.end_headers()

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer((host, port), Handler)
        self.url = public_url or f"http://{host}:{self._server.server_address[1]}/events"

    def __enter__(self):
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()

    def wait_for(self, predicate, timeout_s: float, interval_s: float = 0.2) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if predicate([d.event for d in self.deliveries]):
                return True
            time.sleep(interval_s)
        return predicate([d.event for d in self.deliveries])
