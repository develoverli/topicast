from __future__ import annotations

import pytest

from topicast.config import Level
from topicast.delivery import formatting
from topicast.delivery.formatting import CAPTION_LIMIT, TEXT_LIMIT, Overflow, TextTooLongError

PREFIXES = {Level.WARNING: "⚠️", Level.INFO: "ℹ️"}


def test_short_text_is_untouched() -> None:
    assert formatting.fit("hello", Overflow.SPLIT) == ["hello"]


def test_split_respects_paragraphs() -> None:
    paragraph = "a" * 2000
    text = f"{paragraph}\n\n{paragraph}\n\n{paragraph}"
    chunks = formatting.split_text(text)
    assert len(chunks) == 2
    assert all(len(chunk) <= TEXT_LIMIT for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_split_hard_cuts_when_there_is_no_boundary() -> None:
    chunks = formatting.split_text("b" * (TEXT_LIMIT * 2 + 10))
    assert len(chunks) == 3
    assert len(chunks[0]) == TEXT_LIMIT


def test_truncate_adds_ellipsis() -> None:
    result = formatting.truncate("c" * (TEXT_LIMIT + 50))
    assert len(result) == TEXT_LIMIT
    assert result.endswith("…")


def test_reject_raises() -> None:
    with pytest.raises(TextTooLongError):
        formatting.fit("d" * (TEXT_LIMIT + 1), Overflow.REJECT)


def test_caption_overflow_moves_the_rest_to_follow_ups() -> None:
    caption, extra = formatting.fit_caption("e" * (CAPTION_LIMIT + 500), Overflow.SPLIT)
    assert len(caption) <= CAPTION_LIMIT
    assert len(extra) == 1
    assert len(caption) + len(extra[0]) == CAPTION_LIMIT + 500


@pytest.mark.parametrize(
    ("mode", "raw", "expected"),
    [
        ("HTML", "a < b & c", "a &lt; b &amp; c"),
        ("MarkdownV2", "1.5 (x)", "1\\.5 \\(x\\)"),
        (None, "1.5 (x)", "1.5 (x)"),
    ],
)
def test_escape(mode: str | None, raw: str, expected: str) -> None:
    assert formatting.escape(raw, mode) == expected  # type: ignore[arg-type]


def test_level_prefix_is_escaped_for_markdown() -> None:
    assert formatting.apply_level("hi", Level.WARNING, PREFIXES, None) == "⚠️ hi"
    assert formatting.apply_level("hi", None, PREFIXES, None) == "hi"
