from __future__ import annotations

from datetime import timedelta

import pytest
from telegram.error import BadRequest, ChatMigrated, Forbidden, RetryAfter, TimedOut

from topicast.delivery.telegram import classify


def test_retry_after_is_retryable_with_a_delay() -> None:
    error = classify(RetryAfter(timedelta(seconds=12)))
    assert error.retryable
    assert error.retry_after == pytest.approx(12.0)


def test_parse_errors_are_flagged() -> None:
    error = classify(BadRequest("Can't parse entities: unexpected end of the string"))
    assert error.parse_error
    assert not error.retryable


def test_other_bad_requests_are_permanent() -> None:
    error = classify(BadRequest("message thread not found"))
    assert not error.parse_error
    assert not error.retryable


def test_missing_message_is_flagged_as_not_found() -> None:
    assert classify(BadRequest("Message to delete not found")).not_found


def test_network_errors_are_retryable() -> None:
    assert classify(TimedOut()).retryable


def test_forbidden_is_permanent() -> None:
    assert not classify(Forbidden("bot was kicked")).retryable


def test_chat_migrated_explains_the_fix() -> None:
    error = classify(ChatMigrated(new_chat_id=-100999))
    assert "-100999" in str(error)
    assert not error.retryable
