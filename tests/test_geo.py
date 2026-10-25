from app import geo


def test_country_list_is_iso():
    c = geo.countries()
    assert len(c) >= 240 and c["GB"] == "United Kingdom" and c["DE"] == "Germany" and c["US"] == "United States"
    assert all(len(k) == 2 and k.isupper() for k in c)


def test_valid_country_accepts_codes_names_aliases():
    for raw, want in [("gb", "GB"), ("United Kingdom", "GB"), ("UK", "GB"), ("england", "GB"), ("USA", "US"),
                      ("germany", "DE"), (" FR ", "FR"), ("Holland", "NL")]:
        assert geo.valid_country(raw) == want, raw
    for bad in ("", None, "Narnia", "XX", "<script>", "G"):
        assert geo.valid_country(bad) is None, bad


def test_country_timezones_and_suggestion():
    assert [z for z, _ in geo.zones_for("GB")] == ["Europe/London"]
    us = dict(geo.zones_for("US"))
    assert "America/New_York" in us and "America/Los_Angeles" in us and len(us) > 10
    assert us["America/New_York"]                                     # carries a human note
    assert geo.suggested_tz("GB") == "Europe/London"                  # single zone: auto-pick
    assert geo.suggested_tz("DE") == "Europe/Berlin"                  # Berlin + Busingen share a clock
    assert geo.suggested_tz("ES") is None                             # Canary Islands are an hour behind
    assert geo.suggested_tz("FR") == "Europe/Paris"
    assert geo.suggested_tz("US") is None                             # many zones: make the user choose
    assert geo.suggested_tz("US", "America/Chicago") == "America/Chicago"   # keep a valid current pick
    assert geo.suggested_tz("GB", "America/Chicago") == "Europe/London"     # current doesn't fit country
    assert geo.suggested_tz(None) is None


def test_flag_and_summary():
    assert geo.flag("gb") == "\U0001F1EC\U0001F1E7"
    assert geo.flag("") == geo.flag("1x") == geo.flag("GBR") == ""
    assert geo.summary("DE", "Saxony", "Falkenstein", "Hetzner FSN1") == "Germany · Saxony · Falkenstein · Hetzner FSN1"
    assert geo.summary(None, None, "Leeds", None) == "Leeds"
    assert geo.summary(None, None, None, None) == ""


def test_search_codes():
    assert geo.codes_matching("germany") == ["DE"]
    assert "GB" in geo.codes_matching("uk") and "GB" in geo.codes_matching("united king")
    assert geo.codes_matching("u") == []                              # too short, would match everything


def test_clean_text():
    assert geo.clean_text("  Hetzner \n FSN1  ") == "Hetzner FSN1"
    assert geo.clean_text("") is None and geo.clean_text(None) is None
    assert len(geo.clean_text("x" * 500)) == geo.FIELD_MAX
