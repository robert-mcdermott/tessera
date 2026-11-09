"""Admin → Branding and vocabulary presets (PLT-05, PLT-11, PLT-13)."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tessera import brandkit
from tessera.api.app import create_app
from tessera.branding import palette
from tessera.config import ConfigError, example_config_text, load_config

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10"/></svg>'


MINIMAL = """[organization]
name = "Northwind University"
aliases = ["Northwind University"]

[sync]
window_years = 5
"""


def _config_file(tmp_path: Path, extra: str = "", base: str = MINIMAL) -> Path:
    path = tmp_path / "tessera.toml"
    path.write_text(base + extra, encoding="utf-8")
    return path


def _proposal(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "product_name": "Northwind Discovery",
        "theme": "harbor",
        "accent_color": None,
        "show_powered_by": False,
        "day": {"accent": "#0b6e4f", "stripe": "#1d3557"},
        "night": {"accent": "#7fd6b4"},
        "footer_links": [{"label": "Privacy", "url": "https://www.example.edu/privacy"}],
        "images": {},
    }
    body.update(changes)
    return body


# --- the configuration file ------------------------------------------------------------------


def test_saving_rewrites_only_the_branding_section(tmp_path: Path) -> None:
    path = _config_file(
        tmp_path,
        '\n# our colors\n[branding.palette.day]\naccent = "#123456"\n\n[ai]\nenabled = false\n',
        base=example_config_text().replace("window_years = 5", "window_years = 7"),
    )
    before = tomllib.loads(path.read_text())
    config = load_config(path, env={})
    saved = brandkit.save(config, brandkit.proposal_from(_proposal()))
    text = path.read_text()
    after = tomllib.loads(text)
    assert {k: v for k, v in after.items() if k != "branding"} == {
        k: v for k, v in before.items() if k != "branding"
    }
    assert after["sync"]["window_years"] == 7 and "# our colors" in text
    assert after["ai"] == {"enabled": False}
    assert text.count("[branding]") == 1 and text.count("[branding.palette.day]") == 1
    brand = saved.branding
    assert brand.product_name == "Northwind Discovery" and brand.theme == "harbor"
    assert not brand.show_powered_by and brand.footer_links == (
        ("Privacy", "https://www.example.edu/privacy"),
    )
    day, night = palette(saved)
    assert day.accent == "#0b6e4f" and day.stripe == "#1d3557" and night.accent == "#7fd6b4"
    # Saved, then read back, it is the same branding: the editor opens on what was saved.
    again = brandkit.current(load_config(path, env={}))
    assert again == brandkit.current(saved)
    assert again.day == {"accent": "#0b6e4f", "stripe": "#1d3557"}


def test_the_previous_file_is_kept_and_a_file_without_branding_gains_a_section(
    tmp_path: Path,
) -> None:
    path = _config_file(tmp_path)
    original = path.read_text()
    brandkit.save(load_config(path, env={}), brandkit.proposal_from(_proposal()))
    assert (tmp_path / "tessera.toml.bak").read_text() == original
    assert path.read_text().startswith(original) and "[branding]" in path.read_text()


@pytest.mark.parametrize(
    ("changes", "says"),
    [
        ({"day": {"accent": "#ffd400"}}, "links on the page"),  # yellow links on white
        ({"night": {"text": "#333333"}}, "text on the page"),
        ({"day": {"stripe": "#f6f6f6"}}, "header stripe"),
        ({"accent_color": "teal"}, "hex color"),
        ({"product_name": ""}, "1 to 60"),
        ({"product_name": "<b>x</b>"}, "plain text"),
        ({"theme": "neon"}, "palette must be one of"),
        ({"day": {"glow": "#000000"}}, "not one of the palette's colors"),
        ({"footer_links": [{"label": "Help", "url": "http://x"}]}, "https://"),
        ({"images": {"logo_light": "brand/missing.png"}}, "does not exist"),
    ],
)
def test_a_save_is_refused_with_a_reason_and_nothing_written(
    tmp_path: Path, changes: dict[str, Any], says: str
) -> None:
    path = _config_file(tmp_path)
    original = path.read_text()
    with pytest.raises(brandkit.BrandingRefused) as refused:
        brandkit.save(load_config(path, env={}), brandkit.proposal_from(_proposal(**changes)))
    assert any(says in p for p in refused.value.problems), refused.value.problems
    assert path.read_text() == original and not (tmp_path / "tessera.toml.bak").exists()


def test_branding_set_outside_its_section_is_left_for_a_person(tmp_path: Path) -> None:
    path = tmp_path / "tessera.toml"
    path.write_text('branding.product_name = "Inline"\n' + MINIMAL, "utf-8")
    original = path.read_text()
    with pytest.raises(brandkit.BrandingRefused) as refused:
        brandkit.save(load_config(path, env={}), brandkit.proposal_from(_proposal()))
    assert "by hand" in refused.value.problems[0]
    assert path.read_text() == original


def test_the_contrast_table_covers_every_pair_in_both_modes() -> None:
    rows = brandkit.contrast_table(brandkit.proposal_from(_proposal()))
    assert {r["mode"] for r in rows} == {"day", "night"} and len(rows) == 24
    assert all(r["passes"] for r in rows)
    yellow = brandkit.contrast_table(brandkit.proposal_from(_proposal(day={"accent": "#ffd400"})))
    assert any(not r["passes"] and r["foreground"] == "accent" for r in yellow)


def test_the_accent_alone_is_checked_as_the_app_shows_it() -> None:
    """With only an accent, the app lightens it after dark and picks the text on it; the
    editor's checks see the same colors as ``tessera doctor``."""
    proposal = brandkit.proposal_from(_proposal(day={}, night={}, accent_color="#0b6e4f"))
    day, night = brandkit.resolved(proposal)
    assert day.accent == "#0b6e4f" and night.accent != "#0b6e4f"
    assert all(r["passes"] for r in brandkit.contrast_table(proposal))


# --- images --------------------------------------------------------------------------------------


def test_images_are_kept_beside_the_configuration_by_their_content(tmp_path: Path) -> None:
    config = load_config(_config_file(tmp_path), env={})
    path = brandkit.store_image(config, "logo_light", SVG)
    assert path.startswith("brand/logo-light-") and path.endswith(".svg")
    assert (tmp_path / path).read_bytes() == SVG
    assert brandkit.store_image(config, "logo_light", SVG) == path
    png = brandkit.store_image(config, "social_image", PNG)
    assert png.endswith(".png")


@pytest.mark.parametrize(
    ("image", "content", "says"),
    [
        ("logo_light", b"GIF89a....", "must be SVG, PNG, JPEG, WEBP"),
        ("social_image", SVG, "must be PNG, JPEG"),
        ("favicon", b"", "empty"),
        ("logo_light", b"<svg><script>alert(1)</script></svg>", "scripts"),
        ("logo_light", b'<svg onload="alert(1)"></svg>', "scripts"),
        (
            "logo_light",
            b'<svg><image href="https://x.example/a.png"/></svg>',
            "links to other files",
        ),
        ("logo_light", b"<svg><foreignObject><p>x</p></foreignObject></svg>", "embedded documents"),
        ("logo_light", PNG + b"\x00" * (2 * 1024 * 1024), "at most 2 MB"),
        ("banner", PNG, "no image called"),
    ],
)
def test_unsafe_or_wrong_images_are_refused(
    tmp_path: Path, image: str, content: bytes, says: str
) -> None:
    config = load_config(_config_file(tmp_path), env={})
    with pytest.raises(brandkit.BrandingRefused) as refused:
        brandkit.store_image(config, image, content)
    assert says in refused.value.problems[0]
    assert not (tmp_path / "brand").exists() or not any((tmp_path / "brand").iterdir())


# --- the API -------------------------------------------------------------------------------------


@pytest.fixture
def app_client(tmp_path: Path) -> TestClient:
    from tessera.home import DataHome
    from tessera.ledger import migrate
    from tessera.ledger.db import make_engine

    config = load_config(_config_file(tmp_path), env={})
    DataHome(config.data.home).create()
    engine = make_engine(config.ledger_url)
    migrate.upgrade(engine)
    engine.dispose()
    with TestClient(create_app(config), base_url="http://localhost") as client:
        yield client


def test_admin_edits_checks_and_saves_branding_in_the_app(
    app_client: TestClient, tmp_path: Path
) -> None:
    state = app_client.get("/api/v1/admin/branding").json()["data"]
    assert state["file"]["writable"] and state["settings"]["product_name"]
    assert len(state["contrast"]) == 24 and "[branding]" in state["toml"]
    bad = app_client.post("/api/v1/admin/branding/check", json=_proposal(day={"accent": "#ffd400"}))
    checked = bad.json()["data"]
    assert checked["problems"] and checked["colors"]["day"]["accent"] == "#ffd400"
    refused = app_client.post("/api/v1/admin/branding", json=_proposal(day={"accent": "#ffd400"}))
    assert refused.status_code == 422 and refused.json()["problems"]
    uploaded = app_client.post(
        "/api/v1/admin/branding/images/logo_light",
        content=SVG,
        headers={"content-type": "image/svg+xml"},
    )
    logo = uploaded.json()["data"]["path"]
    saved = app_client.post("/api/v1/admin/branding", json=_proposal(images={"logo_light": logo}))
    assert saved.status_code == 200, saved.text
    meta = app_client.get("/api/v1/meta").json()["data"]["product"]
    assert meta["name"] == "Northwind Discovery" and meta["logo"]
    served = app_client.get("/branding/logo-light")
    assert served.status_code == 200 and served.content == SVG
    assert "script-src 'none'" in served.headers["content-security-policy"]
    assert "--brand-accent: light-dark(#0b6e4f, #7fd6b4)" in app_client.get("/theme.css").text
    assert (
        load_config(tmp_path / "tessera.toml", env={}).branding.product_name
        == "Northwind Discovery"
    )


def test_branding_is_for_admins_only() -> None:
    from tessera.api.access import POLICIES, Level

    for method, route in [
        ("GET", "/api/v1/admin/branding"),
        ("POST", "/api/v1/admin/branding"),
        ("POST", "/api/v1/admin/branding/check"),
        ("POST", "/api/v1/admin/branding/images/{image}"),
    ]:
        assert POLICIES[(method, route)] == Level.ADMIN


# --- vocabulary presets ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("preset", "group", "kind", "role"),
    [
        ("university", "Unit", ("school", "School"), ("lead", "Director")),
        (
            "research_institute",
            "Group",
            ("core", "Core facility"),
            ("lead", "Principal investigator"),
        ),
        (
            "academic_medical_center",
            "Group",
            ("clinical_service", "Clinical service"),
            ("lead", "Lead"),
        ),
    ],
)
def test_a_preset_names_groups_kinds_and_roles(
    tmp_path: Path, preset: str, group: str, kind: tuple[str, str], role: tuple[str, str]
) -> None:
    config = load_config(_config_file(tmp_path, f'\n[vocabulary]\npreset = "{preset}"\n'), env={})
    vocabulary = config.vocabulary
    assert vocabulary.group == group and kind in vocabulary.kinds and role in vocabulary.roles
    assert set(vocabulary.leader_roles) <= {k for k, _ in vocabulary.roles}


def test_a_preset_is_a_starting_point_that_settings_adjust(tmp_path: Path) -> None:
    extra = '\n[vocabulary]\npreset = "university"\ngroup = "Department"\ngroups = "Departments"\n'
    vocabulary = load_config(_config_file(tmp_path, extra), env={}).vocabulary
    assert vocabulary.group == "Department" and ("school", "School") in vocabulary.kinds


def test_an_unknown_preset_is_named_in_the_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"vocabulary\.preset"):
        load_config(_config_file(tmp_path, '\n[vocabulary]\npreset = "zoo"\n'), env={})


def test_a_preset_changes_what_the_app_shows(tmp_path: Path) -> None:
    from tessera.home import DataHome
    from tessera.ledger import migrate
    from tessera.ledger.db import make_engine

    config = load_config(_config_file(tmp_path, '\n[vocabulary]\npreset = "university"\n'), env={})
    DataHome(config.data.home).create()
    engine = make_engine(config.ledger_url)
    migrate.upgrade(engine)
    engine.dispose()
    with TestClient(create_app(config), base_url="http://localhost") as client:
        shown = client.get("/api/v1/meta").json()["data"]["vocabulary"]
    assert shown["groups"] == "Units"
    assert {"id": "school", "label": "School"} in shown["kinds"]
    assert any(r["id"] == "lead" and r["label"] == "Director" for r in shown["roles"])
