"""REST API of the transport service: GET /trips?updated_since=YYYY-MM-DD&page=N&page_size=M.

A stand-in for the dispatch system of the bus contractor. It serves the trips
written by the generator page by page, as such services do, and fails now and
then with 503 so that the extractor has to retry.
"""
from __future__ import annotations

import json
import os
import random
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from bikpi.config import settings

FAILURE_RATE = 0.03


def load_trips() -> list[dict]:
    path = settings().raw_dir / "transport" / "trips.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


class Handler(BaseHTTPRequestHandler):
    trips: list[dict] = []
    mtime: float = 0

    def do_GET(self) -> None:  # noqa: N802 - the name is fixed by http.server
        url = urlparse(self.path)
        if url.path == "/health":
            return self.reply(HTTPStatus.OK, {"status": "ok"})
        if url.path != "/trips":
            return self.reply(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        if random.random() < FAILURE_RATE:
            return self.reply(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "try_again"})
        query = parse_qs(url.query)
        try:
            page = max(1, int(query.get("page", ["1"])[0]))
            size = min(5000, max(1, int(query.get("page_size", ["1000"])[0])))
        except ValueError:
            return self.reply(HTTPStatus.BAD_REQUEST, {"error": "bad_paging"})
        since = query.get("updated_since", [""])[0]
        trips = self.current()
        if since:
            trips = [trip for trip in trips if trip["service_date"] >= since]
        chunk = trips[(page - 1) * size: page * size]
        self.reply(HTTPStatus.OK, {
            "page": page, "page_size": size, "total": len(trips),
            "has_more": page * size < len(trips), "items": chunk,
        })

    @classmethod
    def current(cls) -> list[dict]:
        """Rereads the file when the generator rewrote it."""
        path = settings().raw_dir / "transport" / "trips.json"
        mtime = path.stat().st_mtime if path.exists() else 0
        if mtime != cls.mtime:
            cls.trips, cls.mtime = load_trips(), mtime
        return cls.trips

    def reply(self, status: HTTPStatus, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass


def main() -> None:
    port = int(os.environ.get("API_PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"transport API on :{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
