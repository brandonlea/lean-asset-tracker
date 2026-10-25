import io

import pytest

from app import devices as dv


def test_diff_fields_reports_only_changes():
    old = {"name": "NAS", "status": "active", "serial_no": None, "notes": "old", "country": "GB", "timezone": None}
    new = {"name": "NAS", "status": "retired", "serial_no": "SN1", "notes": "new", "country": "GB", "timezone": ""}
    assert dv.diff_fields(old, new) == ["Serial: (none) → SN1", "Status: active → retired", "Notes updated"]
    assert dv.diff_fields(old, dict(old)) == []
    assert dv.diff_fields({"notes": None}, {"notes": "x"}) == ["Notes added"]
    assert dv.diff_fields({"notes": "x"}, {"notes": ""}) == ["Notes removed"]


@pytest.mark.parametrize("raw,want", [
    ("/?asset=14", "/?asset=14"), ("/labels?ids=1&ids=2", "/labels?ids=1&ids=2"),
    ("//evil.com", "/"), ("https://evil.com/x", "/"), ("/\\evil.com", "/"), ("javascript:alert(1)", "/"),
    ("", "/"), (None, "/"), ("evil.com", "/"), ("/ok\\..\\x", "/"),
])
def test_safe_next_blocks_open_redirects(raw, want):
    assert dv.safe_next(raw) == want


def test_device_code():
    assert dv.device_code(14) == "LAT-0014"
    assert dv.device_code(123456) == "LAT-123456"


def test_sheets_fit_on_a4():
    for s in dv.SHEETS.values():
        width = s.left_mm + s.cols * s.w_mm + (s.cols - 1) * s.gap_x_mm
        height = s.top_mm + s.rows * s.h_mm + (s.rows - 1) * s.gap_y_mm
        assert width <= 210 and height <= 297, s.key
    assert len(dv.pages(list(range(22)), dv.SHEETS["medium"])) == 2         # 21 per sheet
    assert dv.pages([], dv.SHEETS["small"]) == [[]]


def test_qr_decodes_to_device_url():
    """Render the QR exactly as printed and decode it with a real reader."""
    zxingcpp = pytest.importorskip("zxingcpp")
    Image = pytest.importorskip("PIL.Image")
    import segno
    url = "https://tracker.example.com/a/14"
    svg = dv.qr_svg(url)
    assert svg.startswith("<svg") and "</svg>" in svg
    # must scale with its box (no fixed size) or small labels get cropped
    assert "viewBox" in svg and 'width="' not in svg.split(">", 1)[0]
    buf = io.BytesIO()
    segno.make(url, error="m", micro=False).save(buf, kind="png", scale=6, border=2)
    buf.seek(0)
    found = zxingcpp.read_barcodes(Image.open(buf))
    assert [r.text for r in found] == [url]
