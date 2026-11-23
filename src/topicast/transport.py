"""How API traffic is protected on the way in.

topicast cannot see how Docker publishes its port: inside the container it always listens on
`0.0.0.0`. The operator tells it instead, through the same `TOPICAST_BIND_ADDRESS` that
`compose.yaml` uses for the port mapping, plus `TOPICAST_BEHIND_PROXY` when a reverse proxy
terminates TLS. `serve` logs the result at startup and `topicast doctor` reports it.
"""

from __future__ import annotations

import ipaddress
import ssl
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import structlog

from topicast.config import Settings

log = structlog.get_logger(__name__)

# Tailscale hands out addresses from the CGNAT range and its own IPv6 prefix. Traffic to them
# rides a WireGuard tunnel, so it is encrypted even when topicast speaks plain HTTP.
TAILSCALE_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)
CERT_EXPIRY_WARNING_DAYS = 14
_CERT_DATE_FORMAT = "%b %d %H:%M:%S %Y %Z"

PLAIN_HTTP_WARNING = (
    "API reachable over plain HTTP; API keys travel in clear. "
    "Set TOPICAST_TLS_CERT_FILE/TOPICAST_TLS_KEY_FILE or put a TLS reverse proxy in front "
    "(TOPICAST_BEHIND_PROXY=true)."
)


class TransportMode(StrEnum):
    TLS = "tls"
    PROXY = "proxy"
    LOOPBACK = "loopback"
    TAILSCALE = "tailscale"
    PLAIN = "plain"


_DESCRIPTIONS = {
    TransportMode.TLS: "TLS enabled",
    TransportMode.PROXY: "TLS terminated by reverse proxy",
    TransportMode.LOOPBACK: "bound to loopback; only this machine can reach the API",
    TransportMode.TAILSCALE: "bound to a Tailscale address; transport encrypted by WireGuard",
    TransportMode.PLAIN: PLAIN_HTTP_WARNING,
}


def _bind_mode(bind_address: str) -> TransportMode:
    try:
        address = ipaddress.ip_address(bind_address.strip().strip("[]"))
    except ValueError:
        return TransportMode.PLAIN  # a hostname or garbage: assume the worst
    if address.is_loopback:
        return TransportMode.LOOPBACK
    if any(address in network for network in TAILSCALE_NETWORKS):
        return TransportMode.TAILSCALE
    return TransportMode.PLAIN


def classify(settings: Settings) -> TransportMode:
    """Strongest protection first: native TLS, then a proxy, then the bind address."""
    if settings.tls_enabled:
        return TransportMode.TLS
    if settings.behind_proxy:
        return TransportMode.PROXY
    return _bind_mode(settings.bind_address)


def describe(mode: TransportMode) -> str:
    return _DESCRIPTIONS[mode]


def is_private(mode: TransportMode) -> bool:
    """Only this machine or the tailnet can reach the port."""
    return mode in {TransportMode.LOOPBACK, TransportMode.TAILSCALE}


def log_transport(settings: Settings) -> TransportMode:
    mode = classify(settings)
    if mode is TransportMode.PLAIN:
        log.warning(
            "transport_insecure",
            mode=mode,
            bind_address=settings.bind_address,
            detail=PLAIN_HTTP_WARNING,
        )
    else:
        log.info("transport", mode=mode, detail=describe(mode))
    return mode


def load_tls_files(cert_file: Path, key_file: Path) -> None:
    """Raise `ssl.SSLError` or `OSError` if the pair cannot serve TLS."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=cert_file, keyfile=key_file)


def cert_expiry(cert_file: Path) -> datetime | None:
    """Return the certificate's notAfter, or None if it cannot be read.

    The standard library has no public X.509 parser; `_test_decode_cert` is private, so any
    failure (a changed signature, a missing attribute) degrades to None instead of crashing.
    """
    try:
        decoded = ssl._ssl._test_decode_cert(str(cert_file))  # type: ignore[attr-defined]
        not_after = decoded["notAfter"]
        return datetime.strptime(not_after, _CERT_DATE_FORMAT).replace(tzinfo=UTC)
    except Exception:  # private API: any failure only means "expiry unknown"
        return None
