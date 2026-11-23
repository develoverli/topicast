from __future__ import annotations

import shutil
import ssl
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tests.conftest import SECRET_KEY
from topicast.config import Settings
from topicast.transport import TransportMode, cert_expiry, classify, load_tls_files


@dataclass(frozen=True)
class TlsPair:
    cert: Path
    key: Path


def make_pair(directory: Path, name: str = "server") -> TlsPair:
    """Self-signed pair generated on the fly, so no private key is ever committed."""
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("openssl is not installed")
    directory.mkdir(parents=True, exist_ok=True)
    pair = TlsPair(cert=directory / f"{name}.crt", key=directory / f"{name}.key")
    command = [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "30"]
    command += ["-keyout", str(pair.key), "-out", str(pair.cert), "-subj", "/CN=localhost"]
    result = subprocess.run(  # noqa: S603 - fixed arguments, test-only
        command,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"openssl could not create a certificate: {result.stderr.decode()[:200]}")
    return pair


@pytest.fixture
def tls_pair(tmp_path: Path) -> TlsPair:
    return make_pair(tmp_path / "tls")


def settings_for(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(secret_key=SECRET_KEY, data_dir=tmp_path / "data", **overrides)


# --- settings validation ---


def test_tls_is_off_by_default(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    assert not settings.tls_enabled
    assert settings.tls_cert_file is None
    assert settings.tls_key_file is None


def test_tls_needs_both_files(tmp_path: Path, tls_pair: TlsPair) -> None:
    with pytest.raises(ValidationError, match="TOPICAST_TLS_KEY_FILE is not set"):
        settings_for(tmp_path, tls_cert_file=tls_pair.cert)
    with pytest.raises(ValidationError, match="TOPICAST_TLS_CERT_FILE is not set"):
        settings_for(tmp_path, tls_key_file=tls_pair.key)


def test_tls_rejects_a_missing_file(tmp_path: Path, tls_pair: TlsPair) -> None:
    with pytest.raises(ValidationError, match="not a readable file"):
        settings_for(tmp_path, tls_cert_file=tmp_path / "nope.crt", tls_key_file=tls_pair.key)


def test_tls_rejects_a_directory(tmp_path: Path, tls_pair: TlsPair) -> None:
    with pytest.raises(ValidationError, match="not a readable file"):
        settings_for(tmp_path, tls_cert_file=tls_pair.cert, tls_key_file=tmp_path)


def test_tls_pair_enables_tls(tmp_path: Path, tls_pair: TlsPair) -> None:
    settings = settings_for(tmp_path, tls_cert_file=tls_pair.cert, tls_key_file=tls_pair.key)
    assert settings.tls_enabled


def test_tls_variables_come_from_the_environment(
    tmp_path: Path, tls_pair: TlsPair, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TOPICAST_TLS_CERT_FILE", str(tls_pair.cert))
    monkeypatch.setenv("TOPICAST_TLS_KEY_FILE", str(tls_pair.key))
    monkeypatch.setenv("TOPICAST_BEHIND_PROXY", "true")
    monkeypatch.setenv("TOPICAST_BIND_ADDRESS", "0.0.0.0")
    settings = settings_for(tmp_path)
    assert settings.tls_cert_file == tls_pair.cert
    assert settings.behind_proxy is True
    assert settings.bind_address == "0.0.0.0"


# --- classification ---


@pytest.mark.parametrize(
    ("bind_address", "expected"),
    [
        ("127.0.0.1", TransportMode.LOOPBACK),
        ("127.0.0.53", TransportMode.LOOPBACK),
        ("::1", TransportMode.LOOPBACK),
        ("[::1]", TransportMode.LOOPBACK),
        ("100.122.171.40", TransportMode.TAILSCALE),
        ("100.64.0.1", TransportMode.TAILSCALE),
        ("fd7a:115c:a1e0::1", TransportMode.TAILSCALE),
        ("100.128.0.1", TransportMode.PLAIN),  # just outside 100.64.0.0/10
        ("0.0.0.0", TransportMode.PLAIN),
        ("::", TransportMode.PLAIN),
        ("192.168.1.10", TransportMode.PLAIN),
        ("203.0.113.7", TransportMode.PLAIN),
        ("myhost.local", TransportMode.PLAIN),
    ],
)
def test_bind_address_decides_without_tls(
    tmp_path: Path, bind_address: str, expected: TransportMode
) -> None:
    assert classify(settings_for(tmp_path, bind_address=bind_address)) is expected


def test_proxy_beats_an_exposed_bind(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, bind_address="0.0.0.0", behind_proxy=True)
    assert classify(settings) is TransportMode.PROXY


def test_native_tls_beats_everything(tmp_path: Path, tls_pair: TlsPair) -> None:
    settings = settings_for(
        tmp_path,
        bind_address="0.0.0.0",
        behind_proxy=True,
        tls_cert_file=tls_pair.cert,
        tls_key_file=tls_pair.key,
    )
    assert classify(settings) is TransportMode.TLS


# --- certificate files ---


def test_matching_pair_loads(tls_pair: TlsPair) -> None:
    load_tls_files(tls_pair.cert, tls_pair.key)


def test_mismatched_key_is_rejected(tmp_path: Path, tls_pair: TlsPair) -> None:
    other = make_pair(tmp_path / "other", name="other")
    with pytest.raises(ssl.SSLError):
        load_tls_files(tls_pair.cert, other.key)


def test_expiry_is_read(tls_pair: TlsPair) -> None:
    expiry = cert_expiry(tls_pair.cert)
    assert expiry is not None
    assert expiry.tzinfo is not None


def test_unreadable_expiry_is_none(tmp_path: Path) -> None:
    garbage = tmp_path / "garbage.pem"
    garbage.write_text("not a certificate", encoding="utf-8")
    assert cert_expiry(garbage) is None
