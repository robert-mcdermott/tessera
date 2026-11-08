"""The brand kit (PLT-13): palette by day and night, favicon, social image, footer links, and
the WCAG AA checks that guard them."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tessera.api.app import create_app
from tessera.branding import contrast, contrast_problems, palette
from tessera.config import ConfigError, example_config_text, parse_config

SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"><rect width="8" height="8"/></svg>'
)


def configure(tmp_path: Path, branding: dict) -> object:  # type: ignore[type-arg]
    raw = tomllib.loads(example_config_text())
    raw["branding"] = {**raw.get("branding", {}), **branding}
    raw["data"] = {"home": str(tmp_path / "var")}
    return parse_config(raw, base_dir=tmp_path)


def test_contrast_is_measured_as_wcag_does() -> None:
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)  # just below AA


def test_the_palette_overrides_defaults_by_mode(tmp_path: Path) -> None:
    config = configure(tmp_path, {"palette": {"day": {"accent": "#0B6E4F", "person": "#2b2d6e"},
                                              "night": {"bg": "#0b1220"}}})  # fmt: skip
    day, night = palette(config)
    assert (day.accent, day.person, night.bg) == ("#0b6e4f", "#2b2d6e", "#0b1220")
    assert night.accent == "#a5a3f7"  # unset colors keep the defaults
    assert contrast_problems(config) == []


def test_an_accent_alone_gets_a_night_version_and_readable_text(tmp_path: Path) -> None:
    config = configure(tmp_path, {"accent_color": "#FFD84D"})
    day, night = palette(config)
    assert day.on_accent == "#15122e"  # dark text on a light accent
    assert night.accent != day.accent and night.accent.startswith("#")


def test_failing_pairs_are_named(tmp_path: Path) -> None:
    config = configure(tmp_path, {"palette": {"day": {"muted": "#aaaaaa"}}})
    problem, on_cards = contrast_problems(config)
    assert problem.startswith("day: secondary text on the page (muted #aaaaaa on bg #f8f9fb)")
    assert on_cards.startswith("day: secondary text on cards")
    assert "below 4.5:1" in problem


def test_bad_brand_settings_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as caught:
        configure(tmp_path, {
            "palette": {"day": {"accent": "teal", "glow": "#ffffff"}, "dusk": {}},
            "footer_links": [{"label": "Privacy", "url": "javascript:alert(1)"}, {"url": "x"}],
            "favicon": "missing.svg",
            "social_image": "missing.gif",
        })  # fmt: skip
    text = "\n".join(caught.value.problems)
    for expected in (
        "branding.palette.day.accent must be a hex color",
        "branding.palette.day.glow: not a palette color",
        "branding.palette.dusk",
        "footer_links[1].url must start with https:// or mailto:",
        "footer_links[2] needs a label",
        "branding.favicon: file not found",
        "branding.social_image: file not found",
    ):
        assert expected in text, expected


def test_doctor_fails_colors_below_aa(tmp_path: Path) -> None:
    from tessera.cli.doctor import FAIL, OK, _check_brand

    good = _check_brand(configure(tmp_path, {}))
    assert [c.state for c in good] == [OK] and "24 color pairs" in good[0].detail
    bad = _check_brand(configure(tmp_path, {"palette": {"night": {"text": "#333333"}}}))
    assert {c.state for c in bad} == {FAIL} and len(bad) == 2  # on the page and on cards


def test_the_app_and_the_public_site_wear_the_brand(tmp_path: Path) -> None:
    import dataclasses
    from datetime import date

    from tessera.home import DataHome
    from tessera.ledger import migrate
    from tessera.ledger.db import make_engine
    from tessera.publish import publish

    (tmp_path / "icon.svg").write_bytes(SVG)
    (tmp_path / "card.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    config = configure(tmp_path, {
        "favicon": "icon.svg",
        "social_image": "card.png",
        "palette": {"day": {"accent": "#0B6E4F", "stripe": "#1D3557"}},
        "footer_links": [{"label": "Privacy", "url": "https://example.edu/privacy"}],
    })  # fmt: skip
    home = DataHome(config.data.home)
    home.create()
    engine = make_engine(config.ledger_url)
    migrate.upgrade(engine)
    try:
        with TestClient(create_app(config), base_url="http://localhost") as client:
            css = client.get("/theme.css").text
            assert "--brand-accent: light-dark(#0b6e4f, #a5a3f7);" in css
            assert "--brand-stripe: light-dark(#1d3557, #a5a3f7);" in css
            icon = client.get("/branding/favicon")
            assert icon.status_code == 200 and icon.content == SVG
            meta = client.get("/api/v1/meta").json()["data"]["product"]
            assert meta["footer_links"] == [
                {"label": "Privacy", "url": "https://example.edu/privacy"}
            ]
        site = dataclasses.replace(config, publish=dataclasses.replace(
            config.publish, base_url="https://research.example.edu"))  # fmt: skip
        result = publish(site, home, engine, out=tmp_path / "site", today=date(2026, 11, 9))
        assert result.written, result.problems
        index = (tmp_path / "site" / "index.html").read_text()
        assert '<link rel="icon" href="assets/favicon.svg">' in index
        assert (tmp_path / "site" / "assets" / "favicon.svg").read_bytes() == SVG
        assert 'content="https://research.example.edu/assets/social.png"' in index
        assert 'href="https://example.edu/privacy"' in index
        assert "--accent:#0b6e4f" in (tmp_path / "site" / "assets" / "site.css").read_text()
    finally:
        engine.dispose()


def test_without_a_favicon_the_app_shows_its_mark(client: TestClient) -> None:
    response = client.get("/branding/favicon")
    assert response.status_code == 200 and b"<svg" in response.content
