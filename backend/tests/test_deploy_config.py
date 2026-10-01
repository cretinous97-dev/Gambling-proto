"""Guards for the Vercel deployment configuration.

Deployment configs rot silently: someone adds a dependency to the development
requirements and the deploy installs a different set, or a stray ignore rule
drops the application bundle, and nobody finds out until the URL is live and
broken. These tests fail loudly instead.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
import os

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_REQS = ROOT / "backend" / "requirements.txt"
ROOT_REQS = ROOT / "requirements.txt"
VERCEL_JSON = ROOT / "vercel.json"
VERCEL_IGNORE = ROOT / ".vercelignore"
VERCEL_FUNCTIONS = json.loads(VERCEL_JSON.read_text())["functions"]

#: The function file, taken from the config rather than hardcoded so the two
#: cannot drift apart. Deliberately NOT a bracketed catch-all: see
#: test_the_entry_point_is_not_a_bracketed_catch_all.
ENTRY = ROOT / next(iter(VERCEL_FUNCTIONS))

#: Fine to differ between the two lists:
#:   uvicorn - the serverless runtime supplies its own ASGI server
#:   pytest / pglast - test-only dependencies
#:   psycopg - deploy-only (Postgres for a persistent deployment)
#:
#: httpx is deliberately NOT here. It looks test-only - Starlette's
#: TestClient is built on it - but app/payments/bank_transfer.py (imported
#: unconditionally by app/payments/__init__.py, imported unconditionally by
#: app/main.py) also imports it to call out to a payment rail's API. It was
#: in this set once; the root requirements.txt Vercel installs then had no
#: httpx, and every deployment 500'd at import with
#: "ModuleNotFoundError: No module named 'httpx'" before a single request was
#: served. Keeping it out of DEV_ONLY is what makes test_requirements_files_agree
#: below catch that again if it ever regresses.
DEV_ONLY = {"uvicorn", "pytest", "pglast"}
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


# ---------------------------------------------------------------------------
# "every deployment 500s at import" - the general case
# ---------------------------------------------------------------------------
# httpx was one specific instance of a broader mistake: a package imported
# somewhere under backend/app/ (even inside a function, behind a PAYMENT_PROVIDER
# check nobody exercises in CI) but missing from the root requirements.txt that
# Vercel actually installs. The failure mode is always the same -
# ModuleNotFoundError at import, before any application code runs, 500 on every
# request - and it is always invisible locally because a developer's venv has
# backend/requirements.txt (the superset) installed. This walks the real AST of
# every file the application ships, rather than trusting anyone to update a
# by-hand list the next time it happens with a different package.
APP_DIR = ROOT / "backend" / "app"

#: import name -> the name it is pinned under in requirements.txt.
IMPORT_TO_PACKAGE = {
    "jwt": "pyjwt",
    "pydantic_settings": "pydantic-settings",
}

#: Transitive dependencies of something already pinned (e.g. starlette ships
#: with fastapi). Not expected to appear as their own line.
TRANSITIVE_OK = {"starlette", "anyio", "typing_extensions"}


def _thirdparty_imports(py_file: Path) -> set[str]:
    """Every top-level third-party package named anywhere in this file.

    Walks the whole AST, not just module-level statements, because a lazy
    `import httpx` inside a function is exactly as fatal the first time
    PAYMENT_PROVIDER selects that code path - deferring an import delays the
    crash, it does not prevent it on a deployment that installs the wrong set.
    """
    import ast

    tree = ast.parse(py_file.read_text(), filename=str(py_file))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # relative import: part of this application
            if node.module:
                names.add(node.module.split(".")[0])
    return names


def test_every_import_under_app_is_installable_from_the_root_requirements():
    """Reproduces, generally, the bug that shipped `httpx` only to development.

    `python -m pytest` runs with backend/requirements.txt installed - the
    superset - so a missing-from-deploy dependency is invisible to every other
    test and to any developer running the app locally. This is the one check
    that looks at what Vercel actually installs.
    """
    stdlib = set(sys.stdlib_module_names)
    root_packages = {name.lower() for name in parse_requirements(ROOT_REQS)}

    missing: dict[str, list[str]] = {}
    for py_file in APP_DIR.rglob("*.py"):
        for name in _thirdparty_imports(py_file):
            if name in stdlib or name == "app" or name in TRANSITIVE_OK:
                continue
            package = IMPORT_TO_PACKAGE.get(name, name).lower()
            if package not in root_packages:
                missing.setdefault(name, []).append(
                    str(py_file.relative_to(ROOT))
                )

    assert not missing, (
        "these imports under backend/app/ are not installable from the root "
        "requirements.txt Vercel deploys with, so importing them would "
        "500 every request with ModuleNotFoundError: "
        f"{missing}"
    )


def test_serverless_runtime_dependencies_are_declared():
    """The serverless path imports psycopg only in production, but it must be
    installable there."""
    root = parse_requirements(ROOT_REQS)
    assert "psycopg" in root, "a persistent Vercel deployment needs a Postgres driver"


def test_vercel_json_is_valid_and_complete():
    config = json.loads(VERCEL_JSON.read_text())

    # the build itself lives in the shared script; see the build tests below
    assert config["buildCommand"].endswith("deploy-build.sh")
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


def test_payment_adapters_are_imported_lazily_from_the_registry():
    """`bank_transfer` used to be the one adapter imported eagerly.

    `app/payments/__init__.py` is imported unconditionally by `app/main.py`,
    which is imported unconditionally by the entry point - so anything that
    module imports at the top level runs on every single cold start,
    regardless of PAYMENT_PROVIDER. stripe/adyen/cryptopay were already
    deferred inside `_build_provider()`; `bank_transfer` was not, so its
    `import httpx` ran unconditionally too. httpx being missing from the
    deploy-time requirements is what turned that into an outage - but the
    eager import is what made the blast radius "every deployment" instead of
    "deployments that configure bank_transfer".
    """
    source = (ROOT / "backend" / "app" / "payments" / "__init__.py").read_text()
    module_level = source.split("def _build_provider")[0]
    assert "from .bank_transfer import" not in module_level, (
        "bank_transfer is imported at module level again - it, and everything "
        "it imports, now runs on every cold start regardless of which "
        "PAYMENT_PROVIDER is configured"
    )
    assert "from .stripe_provider import" not in module_level
    assert "from .adyen import" not in module_level
    assert "from .cryptopay import" not in module_level


def test_entry_point_exists_and_bootstraps_the_application():
    assert ENTRY.is_file(), f"Vercel needs {ENTRY.name} as the function entry"
    source = ENTRY.read_text()
    assert "from app.main import app" in source
    # it must put backend/ on the path itself rather than relying on cwd
    assert "sys.path.insert" in source
    assert "handler = app" in source, "Vercel looks for an ASGI callable by name"


# ---------------------------------------------------------------------------
# Regression guards for the "every /api/a/b 404'd at the edge" incident
# ---------------------------------------------------------------------------
# Vercel compiles the filename `api/[...path].py` into the route
#
#     ^/api/([^/]+)$
#
# which matches exactly ONE segment after /api. /api/health reached the
# function; /api/auth/register - the endpoint the signup form posts to -
# returned the platform's own 404 without ever invoking Python. No Python-side
# test could have caught it, because the request never got that far.
#
# The fix is two interlocking halves, and BOTH are required:
#   * a plain api/index.py, reached by an explicit rewrite
#   * a rewrite that carries the original path, because a rewrite hands the
#     function its destination path, not the one the browser asked for
# Remove either half and the bug comes back silently.


def test_the_entry_point_is_not_a_bracketed_catch_all():
    """A bracketed filename only ever matches a single path segment."""
    assert "[" not in ENTRY.name and "]" not in ENTRY.name, (
        f"{ENTRY.name} is a bracketed catch-all. On Vercel it compiles to a "
        "route matching one segment after /api, so /api/auth/register and every "
        "other real endpoint 404s at the edge before the function runs."
    )
    assert ENTRY.parent.name == "api", (
        "the function must live directly in api/, which is where Vercel looks"
    )


def test_multi_segment_api_paths_reach_the_function():
    """Without this rewrite the platform answers for us."""
    rewrites = _rewrites()
    assert rewrites, "no rewrites configured"

    for probe in ("/api/auth/register", "/api/auth/login", "/api/wallet/summary",
                  "/api/games/play", "/api/admin/users"):
        matched = [r for r in rewrites if _matches(r["source"], probe)]
        assert matched, (
            f"no rewrite matches {probe}, so Vercel serves its own 404 for it"
        )
        destination = matched[0]["destination"]
        target = "/" + ENTRY.relative_to(ROOT).with_suffix("").as_posix()
        assert destination.startswith(target), (
            f"the rewrite for {probe} points at {destination!r}, not at the "
            f"function {target!r}"
        )
        # a rewrite hands the function its destination, so the real path has to
        # be carried, not inferred
        assert "$1" in destination or ":path" in destination, (
            f"the rewrite for {probe} discards the original path: it would send "
            f"{probe} to {destination}, so the function sees {destination} and "
            "cannot know which endpoint was requested"
        )
        assert "?" in destination, (
            f"the rewrite for {probe} must carry the original path in a query "
            f"parameter, got {destination!r}"
        )


def test_the_api_rewrite_comes_before_any_page_rewrite():
    """The first rule that matches wins, so the API rule must be first."""
    rewrites = _rewrites()
    api_rules = [i for i, r in enumerate(rewrites)
                 if _matches(r["source"], "/api/auth/register")]
    page_rules = [i for i, r in enumerate(rewrites)
                  if _matches(r["source"], "/wallet")]
    assert api_rules and page_rules
    assert api_rules[0] < page_rules[0], (
        "a page rewrite is matched before the API rewrite, so API calls would be "
        "served the single-page app instead of JSON"
    )


def test_the_entry_point_reads_the_path_the_rewrite_carries():
    """The parameter name must agree on both sides."""
    source = ENTRY.read_text()
    carried = re.search(r'^PATH_PARAM = "([^"]+)"', source, re.M)
    assert carried, "the entry point no longer declares the path parameter name"
    param = carried.group(1)

    for rule in _rewrites():
        if "$1" in rule["destination"]:
            assert f"{param}=" in rule["destination"], (
                f"rewrite {rule['source']} carries the path under a different "
                f"name than the entry point reads ({param!r})"
            )

    assert "query_string" in source, "the entry point must read the query string"
    assert "scope[" in source, "the entry point must rewrite the ASGI scope path"
    assert 'scope["path"]' in source, "the rewritten path is never applied"


def test_the_entry_point_does_not_guess_paths():
    """Guessing is how page URLs broke.

    An earlier revision prefixed bare paths that "looked like" API calls, which
    turned /wallet, /crash, /admin and /legal/terms into /api/... and returned a
    JSON 404 for a link click. The rewrite states the path; nothing is inferred.
    """
    source = ENTRY.read_text()
    for collision in ("wallet", "crash", "admin", "promotions", "legal"):
        assert f'"{collision}"' not in source, (
            f"the entry point still special-cases {collision!r}, which collides "
            "with a page URL of the same name"
        )


def test_the_single_page_app_handler_resolves_its_directory_lazily():
    """The built site must be looked up per request, not at import time.

    On a serverless deployment the bundle is at backend/static. A check made
    while the module is imported - before the platform has unpacked anything -
    would leave the SPA handler unregistered and every page URL returning JSON.
    """
    source = (ROOT / "backend" / "app" / "main.py").read_text()
    assert "def frontend_dist()" in source
    module_level_guard = re.search(
        r"^if\s+\w*FRONTEND\w*\.is_dir\(\)", source, re.M
    )
    assert module_level_guard is None, (
        "the SPA handler is registered conditionally at import time; on "
        "serverless it will not be registered at all"
    )
    assert "SPA_ROUTE_PATH" in source, "the single-page-app catch-all is missing"


def _matches(source: str, path: str) -> bool:
    """Does a rewrite source (in Vercel's syntax) match this path?"""
    pattern = re.sub(r":([A-Za-z_]\w*)\*", r"(?P<\1>[^/]*)", source)
    pattern = re.sub(r":([A-Za-z_]\w*)", r"(?P<\1>[^/]+)", pattern)
    pattern = re.sub(r"\((.*?)\)", r"(\1)", pattern)
    return re.fullmatch(pattern, path) is not None


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
    matched = [r for r in _rewrites() if _matches(r["source"], "/")]
    assert matched, (
        "the root URL is not rewritten to the application, so it depends on the "
        "platform finding the static build - which is what broke"
    )


def test_no_rewrite_uses_a_lookahead():
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

    def covered(path: str) -> bool:
        if path == "*":
            return True  # unknown paths fall through to the app's own 404 page
        # a route with parameters (:slug, /*) is covered when a rewrite matches
        # a concrete URL built from it
        sample = re.sub(r"/\*$", "/anything", re.sub(r":[A-Za-z_]\w*", "sample", path))
        return any(_matches(rule["source"], sample) for rule in _rewrites())

    missing = [r for r in routes if not covered(r)]
    assert not missing, (
        f"these routes have no rewrite, so a refresh or deep link 404s: {missing}"
    )


# ---------------------------------------------------------------------------
# Schema shape
# ---------------------------------------------------------------------------
# Vercel validates vercel.json against a JSON schema before building, and a
# schema violation fails the deployment outright. The one that bit us:
#
#     functions.api/[...path].py.includeFiles should be string
#
# The runtime happily accepts an array, but the published schema does not, so
# only the schema matters. These tests encode the shape for every key we use.

FUNCTION_STRING_KEYS = ("includeFiles", "excludeFiles", "runtime")


def test_function_include_files_is_a_single_string():
    """The schema requires a string. An array deploys... nowhere.

    Because one pattern must cover everything the function needs, the build
    copies the site into backend/static, which is already inside backend/**.
    """
    functions = json.loads(VERCEL_JSON.read_text())["functions"]
    entry = next(v for k, v in functions.items() if "api/" in k)

    include = entry["includeFiles"]
    assert isinstance(include, str), (
        f"includeFiles must be a string per the schema, got {type(include).__name__}: "
        f"{include!r}. Vercel rejects the whole deployment with "
        "'should be string'."
    )
    assert len(include) <= 256, "the schema caps includeFiles at 256 characters"
    assert "backend" in include, "the function must be bundled with the application"


BUILD_SCRIPT = ROOT / "frontend" / "scripts" / "deploy-build.sh"


def test_the_build_produces_every_directory_that_may_be_expected():
    """The build must write to every location a consumer looks in.

    Vercel's build source decides the static output with:

        usedOutputDirectory = outputDirectory || "public"

    so an unset output directory means `public/`, and a project setting in the
    dashboard overrides vercel.json. The deployment failed with

        No Output Directory named "public" found after the Build completed

    because the build produced only frontend/dist. Producing all three removes
    the dependency on which setting wins.
    """
    config = json.loads(VERCEL_JSON.read_text())
    script = BUILD_SCRIPT.read_text()

    assert BUILD_SCRIPT.is_file(), "the shared build script is missing"
    assert config["buildCommand"].endswith("deploy-build.sh"), (
        "vercel.json must call the shared build script, so the deploy build and "
        "the local build cannot drift apart"
    )

    configured = config.get("outputDirectory")
    assert configured, (
        "outputDirectory should be named explicitly rather than left to the "
        "platform default"
    )

    # the configured directory, the platform default, and the function bundle
    for needed in {configured, "public", "backend/static"}:
        assert needed in script, (
            f"the build script never creates {needed!r}; a deployment expecting "
            f"that directory would fail after a successful build"
        )
    assert "index.html" in script, (
        "the script should verify the build actually produced index.html"
    )


def test_local_and_deploy_builds_are_the_same_script():
    """Two build definitions drifting apart caused a deployment failure."""
    makefile = (ROOT / "Makefile").read_text()
    assert "deploy-build.sh" in makefile, (
        "make build must call the same script the deployment calls"
    )
    assert "npm run build" not in makefile, (
        "the Makefile builds independently again - it will drift from the deploy"
    )


def test_every_function_key_has_the_type_the_schema_expects():
    """Type-check the keys we set inside a function entry."""
    functions = json.loads(VERCEL_JSON.read_text())["functions"]
    assert functions, "no functions configured"

    for pattern, entry in functions.items():
        assert len(pattern) <= 256, f"function pattern too long: {pattern}"
        assert isinstance(entry, dict), f"{pattern} must be an object"
        for key in FUNCTION_STRING_KEYS:
            if key in entry:
                assert isinstance(entry[key], str), (
                    f"functions.{pattern}.{key} must be a string, got "
                    f"{type(entry[key]).__name__}"
                )
                assert len(entry[key]) <= 256
        if "memory" in entry:
            assert isinstance(entry["memory"], (int, float)) and not isinstance(
                entry["memory"], bool
            ), f"functions.{pattern}.memory must be a number"
        if "maxDuration" in entry:
            assert isinstance(entry["maxDuration"], (int, str)), (
                f"functions.{pattern}.maxDuration must be a number or a string"
            )


def test_top_level_keys_have_the_types_the_schema_expects():
    config = json.loads(VERCEL_JSON.read_text())

    for key in ("buildCommand", "installCommand", "devCommand", "outputDirectory",
                "buildCommand", "cleanUrls", "trailingSlash", "public"):
        if key in config and key not in ("cleanUrls", "trailingSlash", "public"):
            assert isinstance(config[key], str), f"{key} must be a string"

    if "framework" in config:
        assert config["framework"] is None or isinstance(config["framework"], str), (
            "framework must be a string (or null to disable detection)"
        )

    for rule in config.get("rewrites", []):
        assert set(rule) <= {"source", "destination", "has", "missing", "permanent"}, rule
        assert isinstance(rule["source"], str) and isinstance(rule["destination"], str)

    for header in config.get("headers", []):
        assert isinstance(header["source"], str)
        for item in header["headers"]:
            assert isinstance(item["key"], str) and isinstance(item["value"], str)


def test_the_config_is_only_as_large_as_it_needs_to_be():
    """A sanity bound: a runaway save should not quietly bloat the config."""
    raw = VERCEL_JSON.read_text()
    assert len(raw) < 20_000, f"vercel.json is {len(raw)} bytes"


def test_the_site_resolver_finds_the_bundled_location():
    """The function serves the site, so it must look where the build put it."""
    source = (ROOT / "backend" / "app" / "main.py").read_text()
    assert '"static"' in source, (
        "the dist resolver does not look in backend/static, so a deployment "
        "would not find the bundled site"
    )


def test_the_500_page_route_exists_for_the_site():
    """The API must expose the site route the rewrites point at."""
    source = (ROOT / "backend" / "app" / "main.py").read_text()
    assert '"/api/site"' in source and "/api/site/{asset_path:path}" in source
    assert "def frontend_dist()" in source, "the dist resolver is missing"


# ---------------------------------------------------------------------------
# demo mode
# ---------------------------------------------------------------------------
def _settings(monkeypatch, *, serverless, database_url, demo_mode=None, environment=None):
    """Build a Settings for a given deployment shape.

    Constructed directly rather than by reloading `app.config`. Reloading looks
    tidier and is wrong: it replaces the module's `settings` object, so every
    other test module that did `from app.config import settings` at import time
    is left patching a different object from the one the application code reads.
    That is a real failure - it silently un-caps `max_win_usd` in test_flows -
    and it is invisible until something downstream asserts on a number.

    `_env_file=None` skips dotenv, so a developer's own `backend/.env` cannot
    satisfy an assertion about what happens when a value is absent.
    """
    from app.config import Settings

    for key in ("SERVERLESS", "DEMO_MODE", "ENVIRONMENT", "VERCEL_ENV"):
        monkeypatch.delenv(key, raising=False)
    if demo_mode is not None:
        monkeypatch.setenv("DEMO_MODE", str(demo_mode))
    if environment is not None:
        monkeypatch.setenv("ENVIRONMENT", environment)

    return Settings(
        _env_file=None,
        serverless=serverless,
        database_url=database_url,
        secret_key="x" * 48,          # keep the production guard out of the way
    )


def test_a_serverless_deploy_with_a_real_database_is_not_a_demo(monkeypatch):
    """The banner must not tell paying players their money is not real.

    Demo mode used to be set for every serverless deploy, so a Vercel
    production deployment with PostgreSQL configured - the configuration the
    README asks for - still announced itself as a test deployment and put a
    "demo" badge beside the player's balance, with no way to turn it off.
    """
    settings = _settings(
        monkeypatch, serverless=True, database_url="postgresql://u:p@db.example.com/casino",
    )
    assert settings.persistence_is_temporary is False
    assert settings.demo_mode is False, (
        "a serverless deploy whose balances persist was still flagged as a demo, "
        "so the site tells players their money is not real"
    )


def test_a_serverless_deploy_that_loses_balances_is_still_a_demo(monkeypatch):
    """Without DATABASE_URL the balances reset, and the banner says so."""
    settings = _settings(monkeypatch, serverless=True, database_url="")
    assert "/tmp/" in settings.database_url
    assert settings.persistence_is_temporary is True
    assert settings.demo_mode is True


def test_demo_mode_can_be_forced_either_way(monkeypatch):
    """An operator who knows what they want gets it."""
    on = _settings(
        monkeypatch, serverless=True, database_url="postgresql://u:p@db/casino", demo_mode="true",
    )
    assert on.demo_mode is True

    off = _settings(monkeypatch, serverless=True, database_url="", demo_mode="false")
    assert off.demo_mode is False


def test_a_local_development_server_is_not_flagged_as_a_demo(monkeypatch):
    settings = _settings(monkeypatch, serverless=False, database_url="sqlite:///./data/casino.db")
    assert settings.serverless is False
    assert settings.demo_mode is False


# ---------------------------------------------------------------------------
# DATABASE_URL shapes that crashed every request with FUNCTION_INVOCATION_FAILED
# ---------------------------------------------------------------------------
# `create_engine()` imports the DBAPI driver immediately, not on first use, so
# the wrong scheme does not fail a query - it fails the module import. On
# Vercel that import runs on every cold start, so the whole site 500s on
# every request until the environment variable is fixed, with no application
# log to point at because no application code ever ran.
#
# Two shapes are wrong in a way an operator cannot be expected to know in
# advance, because they are exactly what a provider's dashboard hands you:
#
#   postgres://...    - Heroku's scheme. SQLAlchemy 1.4+ dropped the
#                        `postgres` dialect alias: NoSuchModuleError at import.
#   postgresql://...  - Neon/Supabase/`psql`'s scheme. Parses fine, but with
#                        no driver named SQLAlchemy defaults to psycopg2,
#                        which this project does not install (it ships
#                        psycopg 3): ModuleNotFoundError at import.
def test_a_bare_postgres_url_is_upgraded_to_the_installed_driver(monkeypatch):
    settings = _settings(
        monkeypatch, serverless=True, database_url="postgres://u:p@db.example.com/casino",
    )
    assert settings.database_url == "postgresql+psycopg://u:p@db.example.com/casino"


def test_a_driverless_postgresql_url_is_upgraded_to_the_installed_driver(monkeypatch):
    settings = _settings(
        monkeypatch,
        serverless=True,
        database_url="postgresql://u:p@ep-xxx.neon.tech/neondb?sslmode=require",
    )
    assert settings.database_url == (
        "postgresql+psycopg://u:p@ep-xxx.neon.tech/neondb?sslmode=require"
    )


def test_a_url_that_already_names_a_driver_is_left_alone(monkeypatch):
    for url in (
        "postgresql+psycopg://u:p@db.example.com/casino",
        "postgresql+psycopg2://u:p@db.example.com/casino",
        "postgresql+asyncpg://u:p@db.example.com/casino",
    ):
        settings = _settings(monkeypatch, serverless=True, database_url=url)
        assert settings.database_url == url


def test_sqlite_urls_are_not_touched(monkeypatch):
    settings = _settings(
        monkeypatch, serverless=False, database_url="sqlite:///./data/casino.db",
    )
    assert settings.database_url == "sqlite:///./data/casino.db"


def test_the_engine_actually_imports_for_both_wrong_shapes():
    """Prove the fix at the layer that actually broke: create_engine().

    A unit test on the string alone would not have caught the original bug -
    `postgresql://...` parses as a perfectly valid URL. The failure only
    exists once SQLAlchemy tries to import the driver, which is what crashed
    in production.
    """
    from sqlalchemy import create_engine

    from app.config import _normalize_database_url

    for raw in (
        "postgres://u:p@db.example.com/casino",
        "postgresql://u:p@db.example.com/casino",
    ):
        engine = create_engine(_normalize_database_url(raw))
        assert engine.dialect.driver == "psycopg"
