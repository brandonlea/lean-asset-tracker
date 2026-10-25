# SPDX-License-Identifier: AGPL-3.0-or-later
"""Small pure helpers for editing devices and printing labels (unit-tested, no DB/HTTP)."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

import segno

# Fields compared when a device is edited, with the label used in the history log
EDIT_FIELDS = {
    "name": "Name", "category": "Category", "serial_no": "Serial", "status": "Status", "notes": "Notes",
    "country": "Country", "region": "Region", "city": "City", "site": "Datacentre", "timezone": "Timezone",
}


def diff_fields(old: dict, new: dict) -> list[str]:
    """['Status: active → retired', 'Serial: (none) → SN1'] for every field that changed."""
    out = []
    for key, label in EDIT_FIELDS.items():
        a, b = (old.get(key) or None), (new.get(key) or None)
        if a != b:
            if key == "notes":                       # notes can be long: don't dump them into history
                out.append("Notes updated" if a and b else ("Notes added" if b else "Notes removed"))
            else:
                out.append(f"{label}: {a or '(none)'} → {b or '(none)'}")
    return out


def safe_next(target: str | None) -> str:
    """Only allow same-site relative paths after login (blocks open redirects like //evil.com)."""
    t = (target or "").strip()
    if not t.startswith("/") or t.startswith("//") or t.startswith("/\\") or "\\" in t:
        return "/"
    parts = urlsplit(t)
    if parts.scheme or parts.netloc:
        return "/"
    return t[:500]


def device_code(asset_id: int) -> str:
    return f"LAT-{asset_id:04d}"


def qr_svg(data: str, scale: int = 4) -> str:
    """Inline SVG QR code (error correction M survives scuffed stickers)."""
    # omitsize -> viewBox instead of fixed width/height, so CSS can scale it to any label size.
    # (A fixed-size SVG gets CROPPED, not scaled, when its box is smaller: small labels didn't scan.)
    svg = segno.make(data, error="m", micro=False).svg_inline(scale=scale, border=2, dark="#000", light="#fff",
                                                              omitsize=True)
    return svg.replace("<svg ", '<svg shape-rendering="crispEdges" preserveAspectRatio="xMidYMid meet" ', 1)


@dataclass(frozen=True)
class Sheet:
    key: str
    label: str
    cols: int
    rows: int
    w_mm: float
    h_mm: float
    top_mm: float
    left_mm: float
    gap_x_mm: float
    gap_y_mm: float = 0.0


# A4 sticker sheets (dimensions from the manufacturer templates)
SHEETS = {
    "medium": Sheet("medium", "Medium: 21 per sheet, 63.5 × 38.1 mm (Avery L7160)", 3, 7, 63.5, 38.1, 15.15, 7.25, 2.54),
    "small": Sheet("small", "Small: 65 per sheet, 38.1 × 21.2 mm (Avery L7651)", 5, 13, 38.1, 21.2, 10.7, 4.65, 2.54),
}


def pages(items: list, sheet: Sheet) -> list[list]:
    """Split labels into A4 pages."""
    per = sheet.cols * sheet.rows
    return [items[i:i + per] for i in range(0, len(items), per)] or [[]]
