from __future__ import annotations

from topicast import security

PEPPER = "pepper" * 8


def test_generated_key_round_trips() -> None:
    generated = security.generate_key()
    assert generated.token.startswith("tc_")
    assert security.parse_key_id(generated.token) == generated.key_id
    digest = security.hash_key(generated.token, PEPPER)
    assert security.verify_key(generated.token, digest, PEPPER)


def test_wrong_pepper_does_not_verify() -> None:
    generated = security.generate_key()
    digest = security.hash_key(generated.token, PEPPER)
    assert not security.verify_key(generated.token, digest, "another-pepper")


def test_malformed_tokens_are_rejected() -> None:
    assert security.parse_key_id("nope") is None
    assert security.parse_key_id("tc_zzzz_1234") is None


def test_redact_keeps_only_the_public_id() -> None:
    token = security.generate_key().token
    redacted = security.redact(token)
    assert redacted.endswith("_***")
    assert token.split("_")[2] not in redacted
