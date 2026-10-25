# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a server physically is: country (ISO 3166-1 alpha-2), region, city, site/datacentre.

Country names and the country -> timezone map come from the IANA tz database files
shipped inside the `tzdata` package (iso3166.tab, zone.tab), so there is no extra
dependency and the lists stay in step with the timezone list.
"""
from __future__ import annotations

import importlib.resources
from functools import lru_cache

from . import timezones as tzs

# Common ways people write a country in a spreadsheet that aren't the ISO name or code
ALIASES = {
    "uk": "GB", "u.k.": "GB", "great britain": "GB", "britain": "GB", "england": "GB", "scotland": "GB",
    "wales": "GB", "northern ireland": "GB", "usa": "US", "u.s.": "US", "u.s.a.": "US", "america": "US",
    "united states of america": "US", "holland": "NL", "the netherlands": "NL", "south korea": "KR",
    "korea": "KR", "russia": "RU", "czech republic": "CZ", "uae": "AE", "vietnam": "VN", "turkey": "TR",
}
FIELD_MAX = 120


def _tab(name: str) -> list[list[str]]:
    text = (importlib.resources.files("tzdata") / "zoneinfo" / name).read_text(encoding="utf-8")
    return [line.split("\t") for line in text.splitlines() if line and not line.startswith("#")]


@lru_cache(maxsize=1)
def countries() -> dict[str, str]:
    """{'GB': 'Britain (UK)', ...} sorted by name."""
    rows = {r[0]: r[1] for r in _tab("iso3166.tab")}
    rows["GB"] = "United Kingdom"            # tzdata's label is "Britain (UK)"
    rows["US"] = "United States"
    return dict(sorted(rows.items(), key=lambda kv: kv[1]))


@lru_cache(maxsize=1)
def _zones_by_country() -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    for r in _tab("zone.tab"):                # CC, coords, TZ, [comment]
        cc, zone, note = r[0], r[2], (r[3] if len(r) > 3 else "")
        if tzs.valid_tz(zone):
            out.setdefault(cc, []).append((zone, note))
    return out


def zones_for(cc: str | None) -> list[tuple[str, str]]:
    """[(zone, note)] for a country, e.g. US -> [('America/New_York', 'Eastern (most areas)'), ...]."""
    return _zones_by_country().get((cc or "").upper(), [])


def valid_country(value: str | None) -> str | None:
    """Accept an ISO code ('gb'), the name ('United Kingdom') or a common alias ('UK'). Else None."""
    v = (value or "").strip()
    if not v:
        return None
    if v.upper() in countries():
        return v.upper()
    low = v.lower()
    if low in ALIASES:
        return ALIASES[low]
    for code, name in countries().items():
        if name.lower() == low:
            return code
    return None


def name(cc: str | None) -> str:
    return countries().get((cc or "").upper(), "")


def flag(cc: str | None) -> str:
    """'GB' -> 🇬🇧 (regional indicator symbols)."""
    cc = (cc or "").upper()
    if len(cc) != 2 or not cc.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in cc)


def codes_matching(q: str) -> list[str]:
    """Country codes whose name/code/alias matches a search string (for search-by-country)."""
    q = (q or "").strip().lower()
    if len(q) < 2:
        return []
    hits = {c for c, n in countries().items() if q in n.lower() or q == c.lower()}
    hits |= {c for a, c in ALIASES.items() if q == a}
    return sorted(hits)


def clean_text(v: str | None) -> str | None:
    v = " ".join((v or "").split())         # collapse whitespace/newlines
    return v[:FIELD_MAX] or None


def summary(cc: str | None, region: str | None, city: str | None, site: str | None) -> str:
    """'Germany · Saxony · Falkenstein · Hetzner FSN1' (skips blanks)."""
    return " · ".join(p for p in (name(cc), region, city, site) if p)


def suggested_tz(cc: str | None, current: str | None = None) -> str | None:
    """Keep the current zone if it belongs to the country. Otherwise auto-pick the country's main zone
    (listed first in zone.tab) when all its zones keep the same clock all year (e.g. Germany's
    Berlin + Busingen). Countries that really differ (US, Spain + Canaries) return None: ask the user."""
    zones = [z for z, _ in zones_for(cc)]
    if current in zones:
        return current
    if not zones:
        return None
    return zones[0] if _same_clock(zones) else None


def _same_clock(zones: list[str]) -> bool:
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    year = datetime.now(timezone.utc).year
    probes = [datetime(year, m, 15, 12, tzinfo=timezone.utc) for m in (1, 4, 7, 10)]
    offsets = {tuple(p.astimezone(ZoneInfo(z)).utcoffset() for p in probes) for z in zones}
    return len(offsets) == 1
