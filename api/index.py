"""Vercel entry point.

Vercel imports every module under `api/` and looks for an ASGI callable. This
file is deliberately named `index.py`, NOT `[...path].py`:

Vercel's plain `/api` directory does not treat a bracketed filename as a
multi-segment catch-all. `api/[...path].py` compiles to the route

    ^/api/([^/]+)$

which matches exactly ONE segment after /api - so `/api/health` reached the
function while `/api/auth/register`, `/api/wallet/deposits` and every other
real endpoint returned the platform's own 404 without ever invoking Python.
That is the bug this file exists to avoid.

Instead the function sits at a fixed path and vercel.json rewrites every
request to it, carrying the original URL in a query parameter:

    /api/auth/register  ->  /api/index?__path=/api/auth/register
    /crash              ->  /api/index?__path=/crash

A rewrite hands the function its DESTINATION path, so the original is passed
explicitly rather than inferred. See EnsureApiPrefix below.
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

# The application lives in backend/; put it on the import path.
BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app as _app  # noqa: E402

#: The query parameter vercel.json uses to carry the original request path.
PATH_PARAM = "__path"

#: Headers a proxy may use to preserve a URL it rewrote away.
ORIGINAL_PATH_HEADERS = (
    "x-vercel-original-url",
    "x-original-url",
    "x-original-uri",
    "x-forwarded-uri",
    "x-rewrite-url",
)


def _header(scope, name: str) -> str | None:
    for key, value in scope.get("headers") or ():
        if key.decode("latin-1").lower() == name:
            return value.decode("latin-1")
    return None


def _extract_path(query: bytes) -> tuple[str | None, bytes]:
    """Pull the carried path out of the query string.

    Returns the path (if present) and the query string with that parameter
    removed, so the application sees exactly the query string the browser sent.
    """
    if not query:
        return None, query
    try:
        pairs = parse_qsl(query.decode("latin-1"), keep_blank_values=True)
    except Exception:
        return None, query

    found = None
    kept = []
    for key, value in pairs:
        if key == PATH_PARAM and found is None:
            found = value
            continue
        kept.append((key, value))
    if found is None:
        return None, query
    return found, urlencode(kept).encode("latin-1")


class EnsureApiPrefix:
    """Make the application see the path the browser actually requested.

    Resolution order:

      1. the `__path` query parameter written by the rewrite in vercel.json -
         the normal path on Vercel
      2. an original-URL header, if a proxy left one
      3. the request path as-is

    Step 3 deliberately does NOT guess. An earlier revision prefixed bare paths
    that "looked like" API calls, which corrupted exactly the page URLs whose
    names collide with API resources - /wallet, /crash, /admin and /legal/terms
    became /api/wallet, /api/crash, /api/admin and /api/legal/terms, so a click
    on those links returned a JSON 404 instead of the page. Nothing is inferred
    any more: the rewrite states the path, or the path stands as it arrived.
    """

    def __init__(self, asgi_app):
        self.asgi_app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            path = scope.get("path") or "/"
            query = scope.get("query_string") or b""

            carried, query = _extract_path(query)
            if carried:
                path = carried if carried.startswith("/") else "/" + carried
            else:
                for header in ORIGINAL_PATH_HEADERS:
                    original = _header(scope, header)
                    if original:
                        original = original.split("?", 1)[0]
                        if original.startswith("/"):
                            path = original
                            break

            if path != scope.get("path") or query != (scope.get("query_string") or b""):
                scope = dict(scope)
                scope["path"] = path
                scope["raw_path"] = path.encode("latin-1")
                scope["query_string"] = query

        await self.asgi_app(scope, receive, send)


# Vercel serves whatever ASGI callable it finds on this module.
app = EnsureApiPrefix(_app)
handler = app
