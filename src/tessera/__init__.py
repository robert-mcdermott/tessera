"""Tessera: open research intelligence. Every contribution, connected."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("tessera")
except PackageNotFoundError:  # pragma: no cover - running from a source tree without install
    __version__ = "0.0.0+unknown"

PRODUCT_NAME = "Tessera"
TAGLINE = "Every contribution, connected."

__all__ = ["PRODUCT_NAME", "TAGLINE", "__version__"]
