"""The caller's region, as told by the edge.

An IP belongs to a country; the application is behind a CDN that already
resolved that, so there is no reason to ship a GeoIP database. This module
reads the header the platform sets and nothing else.

Trust model - read this before enabling ``GEO_ENFORCEMENT``
-----------------------------------------------------------
Only headers the *platform* sets are trustworthy, because only the platform
overwrites what a client sends:

======================  ==========================  =======================
Header                  Set by                      Trustworthy
======================  ==========================  =======================
``x-vercel-ip-country`` Vercel's edge               yes (stripped inbound)
``cf-ipcountry``        Cloudflare (proxied)        yes when orange-cloud
``x-country-code``      many reverse proxies        depends on the proxy
``x-geo-country``       application convention      no - do not enable it
======================  ==========================  =======================

Vercel's header is only present on requests routed through the function; a
request served from the static build never reaches this code, which is fine
because this is only consulted for API calls (registration, payments).

The header is a *hint with a policy attached*, not a security control: a
determined player with a VPN defeats it, which is precisely why real operators
combine it with KYC, address and payment-instrument checks rather than relying
on it. It is off by default (``GEO_ENFORCEMENT=false``).
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from ..config import settings

#: (header, source name) in priority order. Only platform-set headers here.
COUNTRY_HEADERS: tuple[tuple[str, str], ...] = (
    ("x-vercel-ip-country", "vercel"),
    ("cf-ipcountry", "cloudflare"),
    ("x-country-code", "proxy"),
)

#: ISO codes that are not real countries; the CDN uses them for unknowns.
_NON_COUNTRY = {"XX", "T1", "A1", "A2", "O1", "EU", "AP", "T2"}


@dataclass(frozen=True)
class Region:
    country: str
    source: str
    enforced: bool

    @property
    def known(self) -> bool:
        return bool(self.country)

    def as_dict(self) -> dict:
        return {
            "country": self.country or None,
            "source": self.source or None,
            "enforced": self.enforced,
        }


def region_of(request: Request) -> Region:
    """Best available region for this request. Empty country = unknown."""
    for header, source in COUNTRY_HEADERS:
        raw = request.headers.get(header)
        if not raw:
            continue
        code = raw.strip().upper()
        if code in _NON_COUNTRY or len(code) != 2 or not code.isalpha():
            continue
        return Region(code, source, settings.geo_enforcement)
    return Region("", "", settings.geo_enforcement)


def caller_country(request: Request) -> str:
    """Country code, or "" when the edge did not say."""
    return region_of(request).country
