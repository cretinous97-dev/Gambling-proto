"""Guards for the Vercel deployment configuration.

Deployment configs rot silently: someone adds a dependency to the development
requirements and the deploy installs a different set, or a stray ignore rule
drops the application bundle, and nobody finds out until the URL is live and
broken. These tests fail loudly instead.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_REQS = ROOT / "backend" / "requirements.txt"
ROOT_REQS = ROOT / "requirements.txt"
VERCEL_JSON = ROOT / "vercel.json"
VERCEL_IGNORE = ROOT / ".vercelignore"
ENTRY = ROOT / "api" / "[...path].py"

#: Fine to differ between the two lists:
#:   uvicorn - the serverless runtime supplies its own ASGI server
#:   httpx / pytest - test-only dependencies
#:   psycopg - deploy-only (Postgres for a persistent deployment)
DEV_ONLY = {"uvicorn", "httpx", "pytest"}
DEPLOY_ONLY = {"psycopg"}


def parse_requirements(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "==" not in line:
            continue
        name, version = line.split("==", 1)
        name = name.split("[", 1)[0].strip().lower()
        pins[name] = version.strip()
    return pins


def test_requirements_files_agree():
    """The deploy must install what development runs."""
    backend = parse_requirements(BACKEND_REQS)
    root = parse_requirements(ROOT_REQS)

    for name, version in backend.items():
        if name in DEV_ONLY:
            continue
        assert name in root, (
            f"{name} is required by backend/requirements.txt but missing from "
            f"the root requirements.txt that Vercel installs"
        )
        assert root[name] == version, (
            f"{name} is pinned to {version} in development but {root[name]} "
            f"at deploy time - the deployed app would not be the tested one"
        )

    for name in root:
        assert name in backend or name in DEPLOY_ONLY, (
            f"{name} is installed at deploy time but is not a development "
            f"dependency - add it to backend/requirements.txt so it is tested"
        )


def test_serverless_runtime_dependencies_are_declared():
    """The serverless path imports psycopg only in production, but it must be
    installable there."""
    root = parse_requirements(ROOT_REQS)
    assert "psycopg" in root, "a persistent Vercel deployment needs a Postgres driver"


def test_vercel_json_is_valid_and_complete():
    config = json.loads(VERCEL_JSON.read_text())

    assert "npm run build" in config["buildCommand"]
    assert config["installCommand"].startswith("cd frontend")

    # Framework detection is disabled on purpose: the repository is not a
    # single framework project (Python API + a Vite app in a subdirectory), and
    # guessing wrong is how the deployment first broke.
    assert config.get("framework") is None, "framework detection should stay off"

    functions = config["functions"]
    entry = next(v for k, v in functions.items() if "api/" in k)
    assert "backend" in " ".join(
        entry["includeFiles"] if isinstance(entry["includeFiles"], list) else [entry["includeFiles"]]
    ), "the function needs the application code"


def test_entry_point_exists_and_bootstraps_the_application():
    assert ENTRY.is_file(), "Vercel needs api/[...path].py as the function entry"
    source = ENTRY.read_text()
    assert "from app.main import app" in source
    # it must put backend/ on the path itself rather than relying on cwd
    assert "sys.path.insert" in source
    assert "handler = app" in source, "Vercel looks for an ASGI callable by name"


def test_vercelignore_does_not_drop_the_application():
    lines = [
        ln.strip()
        for ln in VERCEL_IGNORE.read_text().splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    for pattern in lines:
        assert pattern not in ("backend", "backend/", "api", "api/"), (
            f".vercelignore rule {pattern!r} would exclude the application from "
            f"the deployment bundle"
        )
    # the frontend source must survive too
    assert not any(re.fullmatch(r"frontend/?", p) for p in lines)


@pytest.mark.parametrize(
    "requirement",
    ["fastapi", "sqlalchemy", "pydantic", "pydantic-settings", "pyjwt", "bcrypt",
     "email-validator", "python-multipart"],
)
def test_core_runtime_dependencies_are_present_at_deploy_time(requirement: str):
    assert requirement in parse_requirements(ROOT_REQS)


# ---------------------------------------------------------------------------
# Regression guards for the "Vercel served its own 404 at /" incident
# ---------------------------------------------------------------------------
def _rewrites() -> list[dict]:
    return json.loads(VERCEL_JSON.read_text()).get("rewrites", [])


def test_the_root_url_is_routed_to_the_application():
    """The root URL must be served by our code, never left to the platform.

    A deployment returned Vercel's 404 page at "/" because the site depended on
    platform-level static hosting: the output directory, the SPA rewrite and the
    project's root directory all had to line up, and one of them did not.
    Routing "/" explicitly removes that dependency.
    """
    sources = {rule["source"] for rule in _rewrites()}
    assert "/" in sources, (
        "the root URL is not rewritten to the application, so it depends on the "
        "platform finding the static build - which is what broke"
    )


def test_no_rewrite_uses_a_regular_expression():
    """Only exact paths and trailing captures - no lookaheads.

    Negative lookaheads are the classic way people exclude /api from an SPA
    rewrite, but their semantics in `source` are subtle and platform-specific.
    Enumerating the routes removes the guesswork.
    """
    offenders = [
        rule["source"]
        for rule in _rewrites()
        if "?!" in rule["source"] or "(?<" in rule["source"]
    ]
    assert not offenders, f"rewrites use a lookahead, which is hard to verify: {offenders}"


def test_every_route_in_the_router_is_reachable_on_a_deep_link():
    """Every client-side route needs a rewrite, or a refresh returns the
    platform's 404. Read the router itself so the list cannot drift."""
    router = (ROOT / "frontend" / "src" / "main.jsx").read_text()
    routes = re.findall(r'<Route\s+path="([^"]+)"', router)
    assert routes, "no routes found in main.jsx - the parser needs updating"

    sources = {rule["source"] for rule in _rewrites()}

    def covered(path: str) -> bool:
        if path == "*":
            return True  # unknown paths fall through to the app's own 404 page
        if ":" in path or path.endswith("/*"):
            base = path.split("/:")[0].split("/*")[0].rstrip("/")
            return f"{base}/(.*)" in sources or base in sources
        return path in sources

    missing = [r for r in routes if not covered(r)]
    assert not missing, (
        f"these routes have no rewrite, so a refresh or deep link 404s: {missing}"
    )


def test_the_built_app_is_bundled_into_the_function():
    """The function serves the site, so the build output must be in its bundle."""
    functions = json.loads(VERCEL_JSON.read_text())["functions"]
    entry = next(v for k, v in functions.items() if "api/" in k)
    includes = entry["includeFiles"]
    if isinstance(includes, str):
        includes = [includes]
    joined = " ".join(includes)
    assert "frontend/dist" in joined, (
        "frontend/dist is not bundled into the function; the site cannot be "
        "served without it"
    )
    assert "backend" in joined


def test_the_config_does_not_force_an_output_directory():
    """An output directory that does not exist fails the build, and declaring
    one is no longer needed now that the function serves the site."""
    config = json.loads(VERCEL_JSON.read_text())
    assert "outputDirectory" not in config, (
        "outputDirectory makes the build fail when the platform's root "
        "directory differs from the repository layout"
    )


def test_the_500_page_route_exists_for_the_site():
    """The API must expose the site route the rewrites point at."""
    source = (ROOT / "backend" / "app" / "main.py").read_text()
    assert '"/api/site"' in source and "/api/site/{asset_path:path}" in source
    assert "def frontend_dist()" in source, "the dist resolver is missing"
