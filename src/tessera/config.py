"""Configuration: ``tessera.toml`` plus ``TESSERA_*`` environment overrides.

Lookup order for the file: an explicit path, then ``$TESSERA_CONFIG``, then
``./tessera.toml``. Relative paths inside the file are resolved against the file's
own directory, so an installation can be moved as a unit.

Scalar settings can be overridden from the environment as
``TESSERA_<SECTION>_<KEY>``, for example ``TESSERA_SERVER_PORT=9000`` or
``TESSERA_DATA_HOME=/srv/tessera``. Lists are only set in the file.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field, fields, replace
from importlib import resources
from pathlib import Path
from typing import Any

from tessera.core.errors import ConfigError

DEFAULT_CONFIG_NAME = "tessera.toml"
ENV_CONFIG_PATH = "TESSERA_CONFIG"
ENV_PREFIX = "TESSERA_"

# Characters that could escape a CSS declaration or an HTML attribute. Branding values
# are injected into generated CSS, so they are validated strictly.
_UNSAFE_CSS = re.compile(r"[;{}<>\\\"'`\n\r]")
# The accent color becomes a CSS custom property in /theme.css, so it must be a color and
# nothing else: a hex value, a color keyword, or a color function of plain numbers.
_CSS_COLOR = re.compile(
    r"^(#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})|[a-zA-Z]{3,30}"
    r"|(rgba?|hsla?|hwb|lab|lch|oklab|oklch)\([0-9a-zA-Z.,%/ +-]{1,80}\))$"
)
# Logo files the web app can show, by extension.
LOGO_TYPES = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
# Color palettes the web app ships (web/static/css/palettes.css); "tessera" is the default.
PALETTES = ("tessera", "harbor", "cassette")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,63}$")


@dataclass(frozen=True)
class OrganizationConfig:
    """How the organization appears in public data. See spec/data-sources.md."""

    name: str = ""
    short_name: str = ""
    aliases: tuple[str, ...] = ()
    affiliation_patterns: tuple[str, ...] = ()
    reporter_org_names: tuple[str, ...] = ()
    ctgov_org_names: tuple[str, ...] = ()
    email_domains: tuple[str, ...] = ()
    # Look-alike names that contain an alias but are a different organization.
    exclusions: tuple[str, ...] = ()


@dataclass(frozen=True)
class BrandingConfig:
    product_name: str = "Tessera"
    theme: str = "tessera"  # the default palette for everyone (see PALETTES)
    accent_color: str | None = None  # replaces the palette's accent when set
    logo_light: Path | None = None
    logo_dark: Path | None = None
    show_powered_by: bool = True
    favicon: Path | None = None  # the browser tab's icon (SVG, PNG, or ICO)
    social_image: Path | None = None  # link previews of the public site (1200 x 630)
    # ("day" or "night", color name, "#rrggbb") from [branding.palette.day] and .night
    palette: tuple[tuple[str, str, str], ...] = ()
    footer_links: tuple[tuple[str, str], ...] = ()  # (label, address)


@dataclass(frozen=True)
class DataConfig:
    home: Path = Path("var")


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    operator: str = "Operator"  # the actor recorded in single-operator mode (no accounts)
    accounts: bool = False  # require sign-in with local accounts
    session_idle_hours: int = 12  # a session ends this long after it was last used
    session_max_days: int = 7  # and never lasts longer than this from sign-in


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True)
class LedgerConfig:
    url: str = ""  # empty means SQLite at <data home>/ledger.db


@dataclass(frozen=True)
class SourceConfig:
    enabled: bool = True
    api_key_env: str = ""  # name of the environment variable holding an optional API key


@dataclass(frozen=True)
class GrantsGovConfig(SourceConfig):
    """Grants.gov: every forecast and posted opportunity, or only some (wave 5)."""

    categories: tuple[str, ...] = ()  # funding categories, e.g. "HL" (health), "ST" (science)
    agencies: tuple[str, ...] = ()  # agency codes or their beginnings, e.g. "HHS-NIH", "NSF"


@dataclass(frozen=True)
class SourcesConfig:
    contact_email: str = ""
    pubmed: SourceConfig = field(default_factory=lambda: SourceConfig(api_key_env="NCBI_API_KEY"))
    icite: SourceConfig = field(default_factory=SourceConfig)
    reporter: SourceConfig = field(default_factory=SourceConfig)
    ctgov: SourceConfig = field(default_factory=SourceConfig)
    orcid: SourceConfig = field(default_factory=SourceConfig)
    mesh: SourceConfig = field(default_factory=SourceConfig)
    grants_gov: GrantsGovConfig = field(default_factory=GrantsGovConfig)


@dataclass(frozen=True)
class SyncConfig:
    window_years: int = 5


@dataclass(frozen=True)
class EvidenceConfig:
    """Thresholds for suggested links. See spec/evidence-engine.md."""

    strong_threshold: float = 0.95  # "strong" band
    review_threshold: float = 0.60  # below this, suggestions are "unlikely" and hidden
    store_threshold: float = 0.05  # candidates scoring lower are not recorded at all
    discover_min_papers: int = 2  # affiliated papers needed to propose a discovered person


@dataclass(frozen=True)
class DiscoveryConfig:
    """Concept profiles and similarity. See spec/discovery.md."""

    half_life_years: float = 5.0  # a paper's weight in a profile halves every this many years
    likely_weight: float = (
        0.5  # strong suggestions not yet confirmed count this much (0: not at all)
    )
    similarity_floor: float = 0.10  # pairs less similar than this are not kept
    similar_per_person: int = 25  # at most this many similar people kept for each person


@dataclass(frozen=True)
class PublishConfig:
    """The static public site (tessera publish)."""

    out: Path | None = None  # where the site is written; <data home>/public when unset
    base_url: str = ""  # where it is served, for the sitemap, canonical links, and previews
    title: str = ""  # the site's title; the organization's name when empty
    intro: str = ""  # a sentence or two for the home page


@dataclass(frozen=True)
class InsightConfig:
    """Portfolio figures (wave 4). See docs/insight.md."""

    small_number: int = 5  # counts above zero and below this are marked as small numbers


@dataclass(frozen=True)
class AlertsConfig:
    """Who hears when something goes wrong (PLT-12), by email through [mail]."""

    email: tuple[str, ...] = ()  # addresses to alert; none: alerts show in the app only
    backup_days: int = 7  # warn when the last backup is older than this (0: never warn)


@dataclass(frozen=True)
class MailConfig:
    """An SMTP server for digests (CUR-08). Without a host, digests stay in the app."""

    host: str = ""
    port: int = 587
    username: str = ""
    password_env: str = ""  # the environment variable holding the password
    sender: str = ""  # the From address, such as "Tessera <tessera@example.edu>"
    starttls: bool = True
    ssl: bool = False  # connect with TLS from the start (port 465) instead of STARTTLS
    base_url: str = ""  # the app's address, for links in messages


# Group kinds and membership roles: (key, label) pairs, in the order the app offers them. Keys
# are stored with each group and membership, so rename a label freely but keep its key.
DEFAULT_KINDS: tuple[tuple[str, str], ...] = (
    ("division", "Division"),
    ("department", "Department"),
    ("program", "Program"),
    ("center", "Center"),
    ("institute", "Institute"),
    ("core", "Shared resource"),
    ("lab", "Lab"),
    ("team", "Team"),
)
DEFAULT_ROLES: tuple[tuple[str, str], ...] = (
    ("lead", "Lead"),
    ("co_lead", "Co-lead"),
    ("member", "Member"),
    ("staff", "Staff"),
    ("trainee", "Trainee"),
    ("affiliate", "Affiliate"),
)
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


@dataclass(frozen=True)
class VocabularyConfig:
    """What the app calls groups, their kinds, and membership roles (PLT-11)."""

    group: str = "Group"
    groups: str = "Groups"
    kinds: tuple[tuple[str, str], ...] = DEFAULT_KINDS
    roles: tuple[tuple[str, str], ...] = DEFAULT_ROLES
    leader_roles: tuple[str, ...] = ("lead", "co_lead")  # shown as the group's leaders
    default_role: str = "member"  # for memberships that name no role
    # Kinds whose papers are suggested as the group's own (divisions would claim everything).
    attributable_kinds: tuple[str, ...] = ("program", "center", "core", "lab", "team")

    def kind_label(self, key: str) -> str:
        return dict(self.kinds).get(key) or key.replace("_", " ").capitalize()

    def role_label(self, key: str) -> str:
        return dict(self.roles).get(key) or key.replace("_", " ").capitalize()

    @property
    def default_kind(self) -> str:
        keys = [k for k, _ in self.kinds]
        return "department" if "department" in keys else keys[0]

    def to_json(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "groups": self.groups,
            "kinds": [{"id": k, "label": v} for k, v in self.kinds],
            "roles": [
                {"id": k, "label": v, "leader": k in self.leader_roles} for k, v in self.roles
            ],
            "default_role": self.default_role,
            "attributable_kinds": list(self.attributable_kinds),
        }


@dataclass(frozen=True)
class Config:
    organization: OrganizationConfig = field(default_factory=OrganizationConfig)
    branding: BrandingConfig = field(default_factory=BrandingConfig)
    data: DataConfig = field(default_factory=DataConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    ledger: LedgerConfig = field(default_factory=LedgerConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    evidence: EvidenceConfig = field(default_factory=EvidenceConfig)
    discovery: DiscoveryConfig = field(default_factory=DiscoveryConfig)
    vocabulary: VocabularyConfig = field(default_factory=VocabularyConfig)
    mail: MailConfig = field(default_factory=MailConfig)
    publish: PublishConfig = field(default_factory=PublishConfig)
    insight: InsightConfig = field(default_factory=InsightConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    path: Path | None = None  # the file this configuration was loaded from

    @property
    def ledger_url(self) -> str:
        if self.ledger.url:
            return self.ledger.url
        return f"sqlite:///{self.data.home / 'ledger.db'}"


_SECTIONS: dict[str, type] = {
    "organization": OrganizationConfig,
    "branding": BrandingConfig,
    "data": DataConfig,
    "server": ServerConfig,
    "ledger": LedgerConfig,
    "sources": SourcesConfig,
    "sync": SyncConfig,
    "evidence": EvidenceConfig,
    "discovery": DiscoveryConfig,
    "vocabulary": VocabularyConfig,
    "mail": MailConfig,
    "publish": PublishConfig,
    "insight": InsightConfig,
    "alerts": AlertsConfig,
}
_SOURCE_NAMES = ("pubmed", "icite", "reporter", "ctgov", "orcid", "mesh", "grants_gov")
_PATH_FIELDS = {
    ("data", "home"),
    ("branding", "logo_light"),
    ("branding", "logo_dark"),
    ("branding", "favicon"),
    ("branding", "social_image"),
    ("publish", "out"),
}


def example_config_text() -> str:
    """The documented example configuration shipped with the package."""
    return resources.files("tessera.resources").joinpath("tessera.example.toml").read_text("utf-8")


def find_config_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(ENV_CONFIG_PATH)
    if env:
        return Path(env).expanduser()
    return Path.cwd() / DEFAULT_CONFIG_NAME


def load_config(
    path: str | os.PathLike[str] | None = None,
    *,
    env: dict[str, str] | None = None,
) -> Config:
    """Load, override from the environment, resolve paths, and validate."""
    config_path = find_config_path(path)
    if not config_path.is_file():
        raise ConfigError(
            f"No configuration file at {config_path}. "
            "Run `tessera init` to create one, or pass --config."
        )
    try:
        raw = tomllib.loads(config_path.read_text("utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Cannot read {config_path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{config_path} is not valid TOML: {exc}") from exc
    return parse_config(raw, base_dir=config_path.parent.resolve(), path=config_path, env=env)


def parse_config(
    raw: dict[str, Any],
    *,
    base_dir: Path,
    path: Path | None = None,
    env: dict[str, str] | None = None,
) -> Config:
    problems: list[str] = []
    environ = os.environ if env is None else env

    unknown = sorted(set(raw) - set(_SECTIONS))
    problems += [f"unknown section [{name}]" for name in unknown]

    sections: dict[str, Any] = {}
    for name, cls in _SECTIONS.items():
        table = raw.get(name, {})
        if not isinstance(table, dict):
            problems.append(f"[{name}] must be a table")
            table = {}
        table = _apply_env(name, cls, table, environ, problems)
        if cls is SourcesConfig:
            sections[name] = _build_sources(table, problems)
        elif cls is VocabularyConfig:
            sections[name] = _build_vocabulary(table, problems)
        elif cls is BrandingConfig:
            sections[name] = _build_branding(table, problems)
        else:
            sections[name] = _build(cls, name, table, problems)

    config = Config(**sections, path=path)
    config = _resolve_paths(config, base_dir)
    problems += validate(config)
    if problems:
        where = f" in {path}" if path else ""
        raise ConfigError(f"Invalid configuration{where}:", problems)
    return config


def _apply_env(
    section: str, cls: type, table: dict[str, Any], environ: Any, problems: list[str]
) -> dict[str, Any]:
    table = dict(table)
    for f in fields(cls):
        var = f"{ENV_PREFIX}{section.upper()}_{f.name.upper()}"
        if var in environ and _scalar_type(f.type) is not None:
            table[f.name] = _coerce_env(environ[var], _scalar_type(f.type), var, problems)
    return table


def _scalar_type(annotation: Any) -> type | None:
    text = str(annotation)
    for kind in (bool, int, float, str):
        if text == kind.__name__:
            return kind
    if text.startswith("Path"):
        return Path
    return None


def _coerce_env(value: str, kind: type, var: str, problems: list[str]) -> Any:
    if kind is bool:
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        problems.append(f"{var} must be true or false, got {value!r}")
        return None
    if kind is int:
        try:
            return int(value)
        except ValueError:
            problems.append(f"{var} must be an integer, got {value!r}")
            return None
    if kind is float:
        try:
            return float(value)
        except ValueError:
            problems.append(f"{var} must be a number, got {value!r}")
            return None
    return value


def _build(cls: type, section: str, table: dict[str, Any], problems: list[str]) -> Any:
    known = {f.name: f for f in fields(cls)}
    kwargs: dict[str, Any] = {}
    for key, value in table.items():
        if key not in known:
            problems.append(f"unknown setting {section}.{key}")
            continue
        if value is None:
            continue
        expected = str(known[key].type)
        converted = _convert(value, expected, f"{section}.{key}", problems)
        if converted is not None:
            kwargs[key] = converted
    return cls(**kwargs)


def _convert(value: Any, expected: str, label: str, problems: list[str]) -> Any:
    if expected.startswith("tuple"):
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            problems.append(f"{label} must be a list of strings")
            return None
        return tuple(v.strip() for v in value if v.strip())
    if expected.startswith("Path"):
        if not isinstance(value, str):
            problems.append(f"{label} must be a path string")
            return None
        return Path(value).expanduser() if value else None
    if expected == "bool":
        if not isinstance(value, bool):
            problems.append(f"{label} must be true or false")
            return None
        return value
    if expected == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            problems.append(f"{label} must be an integer")
            return None
        return value
    if expected == "float":
        if isinstance(value, bool) or not isinstance(value, int | float):
            problems.append(f"{label} must be a number")
            return None
        return float(value)
    if expected == "str":
        if not isinstance(value, str):
            problems.append(f"{label} must be a string")
            return None
        return value.strip()
    return value  # pragma: no cover - every field type is handled above


def _build_sources(table: dict[str, Any], problems: list[str]) -> SourcesConfig:
    kwargs: dict[str, Any] = {}
    defaults = SourcesConfig()
    for key, value in table.items():
        if key == "contact_email":
            converted = _convert(value, "str", "sources.contact_email", problems)
            if converted is not None:
                kwargs[key] = converted
        elif key in _SOURCE_NAMES:
            if not isinstance(value, dict):
                problems.append(f"[sources.{key}] must be a table")
                continue
            base = getattr(defaults, key)
            kind = type(base)
            built = _build(kind, f"sources.{key}", value, problems)
            merged = {f.name: getattr(base, f.name) for f in fields(kind)}
            merged.update({k: getattr(built, k) for k in value if k in merged})
            kwargs[key] = kind(**merged)
        else:
            problems.append(f"unknown setting sources.{key}")
    return SourcesConfig(**kwargs)


PALETTE_COLORS = (
    "bg", "surface", "text", "muted", "border", "accent", "on_accent", "stripe",
    "person", "publication", "grant", "study",
)  # fmt: skip
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _build_branding(table: dict[str, Any], problems: list[str]) -> BrandingConfig:
    """[branding], with its palette table and footer links."""
    table = dict(table)
    palette: list[tuple[str, str, str]] = []
    raw = table.pop("palette", None)
    if raw is not None:
        if not isinstance(raw, dict):
            problems.append("[branding.palette] must be a table with day and night tables")
            raw = {}
        for mode, colors in raw.items():
            if mode not in ("day", "night") or not isinstance(colors, dict):
                problems.append(f"branding.palette.{mode}: use [branding.palette.day] or .night")
                continue
            for name, value in colors.items():
                if name not in PALETTE_COLORS:
                    problems.append(
                        f"branding.palette.{mode}.{name}: not a palette color "
                        f"({', '.join(PALETTE_COLORS)})"
                    )
                elif not isinstance(value, str) or not _HEX.match(value):
                    problems.append(
                        f"branding.palette.{mode}.{name} must be a hex color like #4f46e5"
                    )
                else:
                    palette.append((mode, name, value.lower()))
    links: list[tuple[str, str]] = []
    raw_links = table.pop("footer_links", None)
    if raw_links is not None:
        if not isinstance(raw_links, list):
            problems.append("branding.footer_links must be a list of {label, url} tables")
            raw_links = []
        for i, item in enumerate(raw_links, 1):
            label = item.get("label") if isinstance(item, dict) else None
            target = item.get("url") if isinstance(item, dict) else None
            if not (isinstance(label, str) and label.strip() and len(label) <= 60):
                problems.append(f"branding.footer_links[{i}] needs a label of 1 to 60 characters")
            elif not (isinstance(target, str) and re.match(r"^(https?://|mailto:)\S+$", target)):
                problems.append(
                    f"branding.footer_links[{i}].url must start with https:// or mailto:"
                )
            else:
                links.append((label.strip(), target))
    built = _build(BrandingConfig, "branding", table, problems)
    return replace(built, palette=tuple(palette), footer_links=tuple(links))


def _pairs(value: Any, label: str, problems: list[str]) -> tuple[tuple[str, str], ...] | None:
    """A ``key = "Label"`` table, in order."""
    if not isinstance(value, dict) or not value:
        problems.append(f'[{label}] must be a table of key = "Label" entries')
        return None
    pairs = []
    for key, text in value.items():
        if not _KEY.match(key):
            problems.append(f"{label}.{key}: keys are lowercase letters, digits, and underscores")
        elif not isinstance(text, str) or not text.strip() or len(text) > 60:
            problems.append(f"{label}.{key} must be a label of 1 to 60 characters")
        else:
            pairs.append((key, text.strip()))
    return tuple(pairs) or None


def _build_vocabulary(table: dict[str, Any], problems: list[str]) -> VocabularyConfig:
    kwargs: dict[str, Any] = {}
    for key, value in table.items():
        if key in ("kinds", "roles"):
            pairs = _pairs(value, f"vocabulary.{key}", problems)
            if pairs:
                kwargs[key] = pairs
        elif key in ("group", "groups", "default_role"):
            converted = _convert(value, "str", f"vocabulary.{key}", problems)
            if converted:
                kwargs[key] = converted.strip()
        elif key in ("leader_roles", "attributable_kinds"):
            converted = _convert(value, "tuple[str, ...]", f"vocabulary.{key}", problems)
            if converted is not None:
                kwargs[key] = converted
        else:
            problems.append(f"unknown setting vocabulary.{key}")
    vocabulary = VocabularyConfig(**kwargs)
    roles = {k for k, _ in vocabulary.roles}
    for role in vocabulary.leader_roles:
        if role not in roles:
            problems.append(f"vocabulary.leader_roles: {role!r} is not in [vocabulary.roles]")
    kinds = {k for k, _ in vocabulary.kinds}
    if "kinds" in table and "attributable_kinds" not in table:
        # Custom kinds: keep the default attributable kinds that are still configured.
        vocabulary = replace(
            vocabulary,
            attributable_kinds=tuple(k for k in vocabulary.attributable_kinds if k in kinds),
        )
    for kind in vocabulary.attributable_kinds if "attributable_kinds" in table else ():
        if kind not in kinds:
            problems.append(f"vocabulary.attributable_kinds: {kind!r} is not in [vocabulary.kinds]")
    if vocabulary.default_role not in roles:
        problems.append(
            f"vocabulary.default_role: {vocabulary.default_role!r} is not in [vocabulary.roles]"
        )
    return vocabulary


def _resolve_paths(config: Config, base_dir: Path) -> Config:
    def resolve(p: Path | None) -> Path | None:
        if p is None:
            return None
        return p if p.is_absolute() else (base_dir / p).resolve()

    data = replace(config.data, home=resolve(config.data.home) or base_dir / "var")
    branding = replace(
        config.branding,
        logo_light=resolve(config.branding.logo_light),
        logo_dark=resolve(config.branding.logo_dark),
        favicon=resolve(config.branding.favicon),
        social_image=resolve(config.branding.social_image),
    )
    publish = replace(config.publish, out=resolve(config.publish.out))
    return replace(config, data=data, branding=branding, publish=publish)


def validate(config: Config) -> list[str]:
    """Semantic checks beyond types. Returns human-readable problems (empty if valid)."""
    problems: list[str] = []
    if not 1 <= config.insight.small_number <= 100:
        problems.append("insight.small_number must be between 1 and 100")
    if not 0 <= config.alerts.backup_days <= 365:
        problems.append("alerts.backup_days must be between 0 and 365")
    for address in config.alerts.email:
        if "@" not in address or " " in address:
            problems.append(f"alerts.email: {address!r} is not an email address")
    org = config.organization
    if not org.name:
        problems.append("organization.name is required")
    if not (org.aliases or org.affiliation_patterns or org.reporter_org_names):
        problems.append(
            "organization needs at least one of aliases, affiliation_patterns, "
            "or reporter_org_names so Tessera can recognize it in public data"
        )
    for domain in org.email_domains:
        if not _DOMAIN.match(domain.lower()):
            problems.append(f"organization.email_domains: {domain!r} is not a domain name")

    brand = config.branding
    if not brand.product_name or _UNSAFE_CSS.search(brand.product_name):
        problems.append("branding.product_name must be non-empty plain text")
    if brand.accent_color is not None and not _CSS_COLOR.match(brand.accent_color):
        problems.append("branding.accent_color must be a CSS color such as #4F46E5")
    if brand.theme not in PALETTES:
        problems.append(f"branding.theme must be one of {', '.join(PALETTES)}")
    for label, logo in (("logo_light", brand.logo_light), ("logo_dark", brand.logo_dark)):
        if logo is not None and not logo.is_file():
            problems.append(f"branding.{label}: file not found: {logo}")
        elif logo is not None and logo.suffix.lower() not in LOGO_TYPES:
            problems.append(f"branding.{label}: use an SVG, PNG, JPEG, or WebP image")
    if brand.favicon is not None:
        if not brand.favicon.is_file():
            problems.append(f"branding.favicon: file not found: {brand.favicon}")
        elif brand.favicon.suffix.lower() not in (".svg", ".png", ".ico"):
            problems.append("branding.favicon: use an SVG, PNG, or ICO file")
    if brand.social_image is not None:
        if not brand.social_image.is_file():
            problems.append(f"branding.social_image: file not found: {brand.social_image}")
        elif brand.social_image.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            problems.append("branding.social_image: use a PNG or JPEG image (1200 x 630)")

    server = config.server
    if not 1 <= server.port <= 65535:
        problems.append("server.port must be between 1 and 65535")
    if not server.host:
        problems.append("server.host is required")
    if not server.operator:
        problems.append("server.operator is required (it is recorded on curation actions)")
    if not server.accounts and server.host not in LOOPBACK_HOSTS:
        problems.append(
            f"server.host is {server.host!r}, which other machines can reach, but accounts "
            "are off. Set server.accounts = true (and create an admin with `tessera users "
            "add`), or listen on 127.0.0.1"
        )
    if not 1 <= server.session_idle_hours <= 24 * 30:
        problems.append("server.session_idle_hours must be between 1 and 720")
    if not 1 <= server.session_max_days <= 90:
        problems.append("server.session_max_days must be between 1 and 90")

    url = config.ledger.url
    if url and not (url.startswith("sqlite") or url.startswith("postgresql")):
        problems.append("ledger.url must be a sqlite:// or postgresql:// URL")

    email = config.sources.contact_email
    if email and not _EMAIL.match(email):
        problems.append(f"sources.contact_email: {email!r} is not an email address")
    grants = config.sources.grants_gov
    for code in grants.categories:
        if not re.fullmatch(r"[A-Z]{1,4}", code):
            problems.append(f"sources.grants_gov.categories: {code!r} is not a funding category "
                            "code such as HL or ST")  # fmt: skip
    for code in grants.agencies:
        if not re.fullmatch(r"[A-Z][A-Z0-9-]*", code):
            problems.append(f"sources.grants_gov.agencies: {code!r} is not an agency code such "
                            "as HHS-NIH or NSF")  # fmt: skip

    if not 1 <= config.sync.window_years <= 50:
        problems.append("sync.window_years must be between 1 and 50")

    ev = config.evidence
    if not 0 <= ev.store_threshold <= ev.review_threshold <= ev.strong_threshold <= 1:
        problems.append(
            "evidence thresholds must satisfy 0 <= store_threshold <= review_threshold "
            "<= strong_threshold <= 1"
        )
    if ev.discover_min_papers < 1:
        problems.append("evidence.discover_min_papers must be at least 1")
    dis = config.discovery
    if not 0.5 <= dis.half_life_years <= 50:
        problems.append("discovery.half_life_years must be between 0.5 and 50")
    if not 0 <= dis.likely_weight <= 1:
        problems.append("discovery.likely_weight must be between 0 and 1")
    if not 0 <= dis.similarity_floor < 1:
        problems.append("discovery.similarity_floor must be at least 0 and below 1")
    if not 1 <= dis.similar_per_person <= 200:
        problems.append("discovery.similar_per_person must be between 1 and 200")
    mail = config.mail
    if mail.host:
        if not 1 <= mail.port <= 65535:
            problems.append("mail.port must be between 1 and 65535")
        address = mail.sender.rsplit("<", 1)[-1].rstrip(">").strip()
        if not _EMAIL.match(address):
            problems.append("mail.sender must be an email address when mail.host is set")
        if mail.ssl and mail.starttls:
            problems.append("mail.ssl and mail.starttls are alternatives; set one")
    if mail.base_url and not re.match(r"^https?://", mail.base_url):
        problems.append("mail.base_url must start with http:// or https://")
    if config.publish.base_url and not re.match(r"^https?://", config.publish.base_url):
        problems.append("publish.base_url must start with http:// or https://")
    if len(config.publish.intro) > 1000:
        problems.append("publish.intro is at most 1,000 characters")
    return problems


def config_warnings(config: Config) -> list[str]:
    """Advice that does not stop Tessera from running."""
    warnings: list[str] = []
    if not config.sources.contact_email:
        warnings.append(
            "sources.contact_email is empty. Public APIs ask for a contact address; "
            "NCBI requires one for sustained use of E-utilities."
        )
    if config.server.accounts and config.server.host not in LOOPBACK_HOSTS:
        warnings.append(
            f"server.host is {config.server.host!r}. Serve Tessera over HTTPS (for example "
            "behind a reverse proxy) so that passwords and session cookies are encrypted."
        )
    return warnings
