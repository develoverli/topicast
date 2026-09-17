"""topicast — self-hosted notification hub for Telegram forum topics."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("topicast")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0.6.1"

__all__ = ["__version__"]
