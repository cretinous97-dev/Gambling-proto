"""Vercel entry point.

Vercel imports every module under `api/` and looks for an ASGI callable. The
catch-all filename means one function serves the whole API surface, so the app
behaves exactly as it does under uvicorn: same routes, same paths.

Two things differ from a normal deployment, and both are handled by the app
rather than here:

  * there is no lifespan hook, so the application bootstraps itself on the
    first request (see backend/app/main.py: bootstrap_app)
  * there is no long-running process, so shared games advance from the clock
    on request instead of from a background loop
    (see backend/app/services/crash_loop.py: advance)
"""
from __future__ import annotations

import sys
from pathlib import Path

# The application lives in backend/; put it on the import path.
BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import app  # noqa: E402


#: Headers a proxy may use to preserve the URL it rewrote away. Checked only
#: when the path we were handed does not address a real endpoint.
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


class EnsureApiPrefix:
    """Guarantee the app sees the path it expects, whatever the platform passes.

    The catch-all filename means Vercel should hand us the untouched request
    path. But routing is configuration, and configuration changes: a rewrite
    added later, or a platform that maps the function to `/api/index`, would
    otherwise 404 every endpoint and look like the application is broken.

    So: if the incoming path does not start with /api, try to recover the real
    path from proxy headers, and failing that re-prefix it. Worst case the app
    answers a clear 404 instead of a confusing one.
    """

    def __init__(self, asgi_app):
        self.asgi_app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            path = scope.get("path") or "/"

            if path.startswith(("/api/index", "/api/[...", "/api/[...")):
                # A rewrite collapsed the path down to the function's own name.
                # Recover the original from a header if the proxy left one.
                for header in ORIGINAL_PATH_HEADERS:
                    original = _header(scope, header)
                    if original:
                        original = original.split("?", 1)[0]
                        if original.startswith("/"):
                            path = original
                            break

            if not path.startswith("/api"):
                path = "/api" + ("" if path == "/" else path)

            if path != scope.get("path"):
                scope = dict(scope)
                scope["path"] = path
                scope["raw_path"] = path.encode("latin-1")

        await self.asgi_app(scope, receive, send)


# Vercel serves whatever ASGI callable it finds on this module.
app = EnsureApiPrefix(app)
handler = app
