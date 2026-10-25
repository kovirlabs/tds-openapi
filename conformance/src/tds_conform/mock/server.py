"""HTTP front end for the reference providers (stdlib only).

URLs:
  /p/<provider_id>/...      the provider API of one provider (its base URL)
  /...                      the first (default) provider, for single-provider use
  /_sim/...                 simulation controls: drive legs through milestones, delays,
                            no-shows and failures; change answer policies (incl. rate_limit);
                            answer 503 for a while (POST /_sim/<id>/unavailable {"for_s": 30});
                            read the event log
  /mailbox/...              a requester-side relay mailbox for CSV profile files (SPEC.md §9.4):
                            GET outbox, GET outbox/<name>, POST ack/<name>, PUT and GET inbox/<name>;
                            the bearer token is the provider id whose mailbox it is
  /_sim/mailbox/<id>        GET: what the agent did (last poll, acks, uploads with their text);
                            POST .../outbox {"content"?, "name"?} queues a request file;
                            POST .../unavailable {"for_s": 30} answers 503 for a while

Authentication is deliberately trivial: the bearer token *is* the requester id
(`Authorization: Bearer throughride-demo`), so a requester can act as several
clients by switching tokens. /_sim needs no token.
"""

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from tds_conform.backpressure import BACKPRESSURE_CODES, retry_after_header
from tds_conform.mailbox import AGENT_HEADER, SHA256_HEADER, sha256_hex
from tds_conform.mock.network import Network
from tds_conform.mock.provider import problem

TRIP = r"/trips/(?P<trip_id>[^/]+)"
SUB = r"/subscriptions/(?P<subscription_id>[^/]+)"
# (method, pattern, provider method, needs auth, takes Idempotency-Key, takes body)
API_ROUTES = [
    ("GET", r"/health", "health", False, False, False),
    ("POST", r"/trips", "request_trip", True, True, True),
    ("GET", TRIP, "get_trip", True, False, False),
    ("PATCH", TRIP, "modify_trip", True, True, True),
    ("POST", TRIP + r"/confirm", "confirm_trip", True, True, False),
    ("POST", TRIP + r"/cancel", "cancel_trip", True, True, True),
    ("POST", TRIP + r"/hold", "request_hold", True, True, True),
    ("GET", TRIP + r"/holds/(?P<hold_id>[^/]+)", "get_hold", True, False, False),
    ("POST", r"/tenders", "create_tender", True, True, True),
    ("GET", r"/tenders/(?P<tender_id>[^/]+)", "get_tender", True, False, False),
    ("GET", r"/subscriptions", "list_subscriptions", True, False, False),
    ("POST", r"/subscriptions", "subscribe", True, True, True),
    ("GET", SUB, "get_subscription", True, False, False),
    ("PATCH", SUB, "update_subscription", True, True, True),
    ("DELETE", SUB, "delete_subscription", True, True, False),
    ("POST", SUB + r"/secrets", "rotate_secret", True, True, True),
    ("GET", SUB + r"/secrets", "list_secrets", True, False, False),
    ("DELETE", SUB + r"/secrets/(?P<secret_id>[^/]+)", "retire_secret", True, True, False),
]
SIM_TRIP = r"/_sim/(?P<pid>[^/]+)/trips/(?P<trip_id>[^/]+)"


def _route(method: str, path: str):
    for route_method, pattern, name, needs_auth, keyed, with_body in API_ROUTES:
        if route_method == method and (match := re.fullmatch(pattern, path)):
            return name, needs_auth, keyed, with_body, match
    return None


def make_handler(network: Network) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "tds-mock"

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def do_PUT(self):
            self._dispatch("PUT")

        def _dispatch(self, method: str) -> None:
            url = urlsplit(self.path)
            path, query = url.path.rstrip("/") or "/", parse_qs(url.query)
            if path.startswith("/_sim/mailbox/"):
                return self._send(*self._sim_mailbox(method, path))
            if path.startswith("/_sim"):
                return self._send(*self._sim(method, path, query))
            if path == "/mailbox" or path.startswith("/mailbox/"):
                return self._mailbox(method, path.removeprefix("/mailbox"))
            provider = network.default
            if match := re.match(r"/p/(?P<pid>[^/]+)(?P<rest>/.*)?$", path):
                provider = network.providers.get(match["pid"])
                if provider is None:
                    return self._send(*problem(404, "NOT_FOUND", f"No provider {match['pid']!r} here."))
                path = match["rest"] or "/"
            route = _route(method, path)
            if route is None:
                return self._send(*problem(404, "NOT_FOUND", f"No route {method} {path}."))
            name, needs_auth, keyed, with_body, match = route

            args, requester = [], None
            if needs_auth:
                auth = self.headers.get("Authorization", "")
                if not auth.startswith("Bearer ") or not auth[7:].strip():
                    return self._send(*problem(401, "UNAUTHORIZED", "Send Authorization: Bearer <requester_id>."))
                requester = auth[7:].strip()
                args.append(requester)
            if refused := provider.admit(requester):  # back-pressure, before anything is processed
                return self._send(*refused)
            if keyed:
                args.append(self.headers.get("Idempotency-Key"))
            args.extend(match.groupdict().values())
            if with_body:
                body, error = self._body()
                if error:
                    return self._send(*error)
                args.append(body)
            self._send(*getattr(provider, name)(*args))

        def _mailbox(self, method: str, path: str) -> None:
            """The relay mailbox (SPEC.md §9.4); the token names the provider's mailbox."""
            auth = self.headers.get("Authorization", "")
            mailbox = network.mailboxes.get(auth[7:].strip()) if auth.startswith("Bearer ") else None
            route = re.fullmatch(r"/(?P<box>outbox|ack|inbox)(?:/(?P<name>[^/]+))?", path)
            known = route and (route["box"], method, bool(route["name"])) in {
                ("outbox", "GET", False),
                ("outbox", "GET", True),
                ("ack", "POST", True),
                ("inbox", "PUT", True),
                ("inbox", "GET", True),
            }
            if mailbox is None:
                self._discard_body()
                return self._send(*problem(401, "UNAUTHORIZED", "Send Authorization: Bearer <mailbox token>."))
            if not known:
                self._discard_body()
                return self._send(*problem(404, "NOT_FOUND", f"No mailbox route {method} {path or '/'}."))
            if refused := mailbox.admit(self.headers.get(AGENT_HEADER, "")):
                self._discard_body()
                return self._send(*refused)
            name = unquote(route["name"] or "")
            if method == "GET" and not name:
                return self._send(*mailbox.listing())
            if method == "GET" and route["box"] == "inbox":
                return self._send(*mailbox.report(name))
            if method == "GET":
                status, body = mailbox.download(name)
                if isinstance(body, bytes):
                    return self._send_bytes(status, body, "text/csv; charset=utf-8", {SHA256_HEADER: sha256_hex(body)})
                return self._send(status, body)
            if method == "POST":
                self._discard_body()
                return self._send(*mailbox.ack(name))
            length = int(self.headers.get("Content-Length") or 0)
            if length > mailbox.max_file_bytes:
                self.close_connection = True  # the body is not read
                return self._send(*mailbox.too_large())
            return self._send(*mailbox.put(name, self.rfile.read(length)))

        def _sim_mailbox(self, method: str, path: str):
            match = re.fullmatch(r"/_sim/mailbox/(?P<pid>[^/]+)(?:/(?P<rest>[a-z]+))?", path)
            mailbox = match and network.mailboxes.get(match["pid"])
            if not mailbox:
                return problem(404, "NOT_FOUND", f"No simulation route {method} {path}.")
            body, error = self._body() if method == "POST" else (None, None)
            if error:
                return error
            if method == "GET" and match["rest"] is None:
                return mailbox.state()
            if method == "POST" and match["rest"] == "outbox":
                body = body or {}
                content = body.get("content")
                if content is not None and not isinstance(content, str):
                    return problem(400, "INVALID_REQUEST", "content is the file's text.")
                return mailbox.queue(content.encode() if content is not None else None, body.get("name"))
            if method == "POST" and match["rest"] == "unavailable":
                return mailbox.sim_unavailable(body)
            return problem(404, "NOT_FOUND", f"No simulation route {method} {path}.")

        def _discard_body(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if 0 < length <= 1 << 20:
                self.rfile.read(length)
            elif length:
                self.close_connection = True

        def _send_bytes(self, status: int, data: bytes, media: str, headers: dict[str, str]) -> None:
            self.send_response(status)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(data)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def _sim(self, method: str, path: str, query: dict):
            if method == "GET" and path == "/_sim/providers":
                return 200, {"providers": [{"provider_id": pid, "base_path": f"/p/{pid}"} for pid in network.providers]}
            if method == "GET" and path == "/_sim/deliveries":
                return 200, {"deliveries": list(network.dispatcher.log)}
            match = re.fullmatch(r"/_sim/(?P<pid>[^/]+)/(?P<rest>.+)", path)
            provider = match and network.providers.get(match["pid"])
            if not provider:
                return problem(404, "NOT_FOUND", f"No simulation route {method} {path}.")
            body, error = self._body() if method == "POST" else (None, None)
            if error:
                return error
            rest = match["rest"]
            if method == "GET" and rest == "events":
                return provider.sim_events(query.get("trip_id", [None])[0])
            if method == "POST" and rest == "policy":
                return provider.sim_policy(body)
            if method == "POST" and rest == "unavailable":
                return provider.sim_unavailable(body)
            if method == "POST" and (m := re.fullmatch(SIM_TRIP + r"/(?P<action>[a-z-]+)", path)):
                trip_id, action = m["trip_id"], m["action"]
                if action == "advance":
                    return provider.sim_advance(trip_id, body)
                if action == "delay":
                    return provider.sim_delay(trip_id, body)
                if action in ("no-show", "fail"):
                    return provider.sim_end(trip_id, action, body)
                if action == "cancel":
                    return provider.sim_cancel(trip_id, body)
            return problem(404, "NOT_FOUND", f"No simulation route {method} {path}.")

        def _body(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(length) or b"null"), None
            except ValueError:  # bad length, bad encoding (UnicodeDecodeError) or bad JSON
                return None, problem(400, "INVALID_REQUEST", "Body is not valid UTF-8 JSON.")

        def _send(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/problem+json" if status >= 400 else "application/json")
            self.send_header("Content-Length", str(len(data)))
            if status in BACKPRESSURE_CODES and isinstance(body.get("retry_after_s"), int):
                for name, value in retry_after_header(body["retry_after_s"]).items():
                    self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            if not getattr(self.server, "quiet", False):
                super().log_message(format, *args)

    return Handler


def make_server(network: Network, host: str = "127.0.0.1", port: int = 4010, quiet: bool = False):
    server = ThreadingHTTPServer((host, port), make_handler(network))
    server.quiet = quiet
    return server
