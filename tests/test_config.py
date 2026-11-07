from __future__ import annotations

from pathlib import Path

import pytest

from tessera.config import config_warnings, example_config_text, load_config
from tessera.core.errors import ConfigError

REPO = Path(__file__).resolve().parents[1]


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "tessera.toml"
    path.write_text(text, encoding="utf-8")
    return path


MINIMAL = """
[organization]
name = "Example University"
aliases = ["Example University"]
"""


def test_example_config_loads(config_file: Path) -> None:
    config = load_config(config_file, env={})
    assert config.organization.name == "Example University"
    assert "example.edu" in config.organization.email_domains
    assert config.server.host == "127.0.0.1"
    assert config.sources.pubmed.api_key_env == "NCBI_API_KEY"
    assert config.data.home == (config_file.parent / "var").resolve()
    assert config.ledger_url.startswith("sqlite:///")
    assert config_warnings(config) == []


def test_repo_example_matches_packaged_example() -> None:
    assert (REPO / "tessera.example.toml").read_text("utf-8") == example_config_text()


def test_minimal_config_uses_defaults(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, MINIMAL), env={})
    assert config.branding.product_name == "Tessera"
    assert config.server.port == 8000
    assert config.sync.window_years == 5
    assert config.sources.pubmed.enabled


def test_relative_paths_resolve_against_config_dir(tmp_path: Path) -> None:
    nested = tmp_path / "deploy"
    nested.mkdir()
    config = load_config(write(nested, MINIMAL + '\n[data]\nhome = "../data"\n'), env={})
    assert config.data.home == (tmp_path / "data").resolve()


def test_environment_overrides(tmp_path: Path) -> None:
    env = {
        "TESSERA_SERVER_PORT": "9001",
        "TESSERA_DATA_HOME": str(tmp_path / "elsewhere"),
        "TESSERA_BRANDING_SHOW_POWERED_BY": "false",
        "TESSERA_ORGANIZATION_SHORT_NAME": "EU",
    }
    config = load_config(write(tmp_path, MINIMAL), env=env)
    assert config.server.port == 9001
    assert config.data.home == tmp_path / "elsewhere"
    assert config.branding.show_powered_by is False
    assert config.organization.short_name == "EU"


def test_bad_environment_value_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="TESSERA_SERVER_PORT must be an integer"):
        load_config(write(tmp_path, MINIMAL), env={"TESSERA_SERVER_PORT": "eighty"})


def test_all_problems_reported_together(tmp_path: Path) -> None:
    text = """
[organization]
name = ""
email_domains = ["not a domain"]
[server]
port = 70000
colour = "blue"
[branding]
accent_color = "red; background: url(x)"
[mystery]
"""
    with pytest.raises(ConfigError) as caught:
        load_config(write(tmp_path, text), env={})
    problems = caught.value.problems
    assert "unknown section [mystery]" in problems
    assert "unknown setting server.colour" in problems
    assert "organization.name is required" in problems
    assert any("email_domains" in p for p in problems)
    assert any("server.port" in p for p in problems)
    assert any("accent_color" in p for p in problems)
    assert any("recognize it in public data" in p for p in problems)


def test_type_errors(tmp_path: Path) -> None:
    text = MINIMAL + '\n[server]\nport = "8000"\n[sources.pubmed]\nenabled = "yes"\n'
    with pytest.raises(ConfigError) as caught:
        load_config(write(tmp_path, text), env={})
    assert "server.port must be an integer" in caught.value.problems
    assert "sources.pubmed.enabled must be true or false" in caught.value.problems


def test_missing_file_explains_next_step(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="tessera init"):
        load_config(tmp_path / "absent.toml", env={})


def test_invalid_toml(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(write(tmp_path, "[organization\n"), env={})


def test_ledger_url_must_be_supported(tmp_path: Path) -> None:
    text = MINIMAL + '\n[ledger]\nurl = "mysql://x"\n'
    with pytest.raises(ConfigError, match=r"ledger\.url"):
        load_config(write(tmp_path, text), env={})


def test_warnings_for_public_bind_and_missing_contact(tmp_path: Path) -> None:
    text = MINIMAL + '\n[server]\nhost = "0.0.0.0"\n'
    warnings = config_warnings(load_config(write(tmp_path, text), env={}))
    assert any("contact_email" in w for w in warnings)
    assert any("no sign-in" in w for w in warnings)


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("#4F46E5", True),
        ("#abc", True),
        ("teal", True),
        ("oklch(0.55 0.2 270)", True),
        ("rgb(79, 70, 229)", True),
        ("hsl(243 75% 59% / 0.9)", True),
        ("red; background: url(x)", False),
        ("url(https://example.org/x.png)", False),
        ("var(--other)", False),
        ("#12345g", False),
        ("#12345", False),
        ("", False),
    ],
)
def test_accent_color_must_be_a_color(tmp_path: Path, value: str, ok: bool) -> None:
    text = '[organization]\nname = "Example Institute"\naliases = ["Example Institute"]\n'
    text += f'[branding]\naccent_color = "{value}"\n'
    if ok:
        assert load_config(write(tmp_path, text), env={}).branding.accent_color == value
    else:
        with pytest.raises(ConfigError) as caught:
            load_config(write(tmp_path, text), env={})
        assert any("accent_color" in p for p in caught.value.problems)
