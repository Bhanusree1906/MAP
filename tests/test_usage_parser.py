"""Unit tests for the sandbox token-usage parser (sandbox/usage.py)."""

from __future__ import annotations

from decimal import Decimal  # noqa: F401 - keeps linters honest about intent

import pytest

from sandbox.usage import TokenUsage, add_usage, parse_usage


class TestParseUsage:
    def test_total_only_line(self) -> None:
        usage = parse_usage("...work...\ntokens used: 12,345\n")
        assert usage == TokenUsage(0, 0, 12345)

    def test_breakdown_line(self) -> None:
        usage = parse_usage("tokens used: 5,432 input, 12,345 output")
        assert usage == TokenUsage(5432, 12345, 17777)

    def test_underscores_accepted(self) -> None:
        assert parse_usage("tokens used: 1_000").total_tokens == 1000

    def test_jsonl_event(self) -> None:
        raw = (
            '{"type":"item.completed","item":{}}\n'
            '{"type":"token_count","info":'
            '{"input_tokens":100,"cached_input_tokens":40,"output_tokens":50,"total_tokens":150}}\n'
        )
        usage = parse_usage(raw)
        assert usage is not None
        assert usage.input_tokens == 140
        assert usage.output_tokens == 50
        assert usage.total_tokens == 150

    def test_none_and_garbage(self) -> None:
        assert parse_usage(None) is None
        assert parse_usage("") is None
        assert parse_usage("no signals at all") is None

    def test_zero_token_events_do_not_shadow_text(self) -> None:
        raw = '{"info":{"input_tokens":0}}\ntokens used: 99\n'
        usage = parse_usage(raw)
        assert usage is not None and usage.total_tokens == 99


class TestAddUsage:
    def test_accumulates_into_plain_dict(self) -> None:
        sink: dict[str, int] = {}
        add_usage(sink, TokenUsage(10, 5, 15))
        add_usage(sink, TokenUsage(1, 1, 2))
        assert sink == {"input_tokens": 11, "output_tokens": 6, "total_tokens": 17}

    def test_none_is_noop(self) -> None:
        sink: dict[str, int] = {"total_tokens": 7}
        add_usage(sink, None)
        assert sink == {"total_tokens": 7}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("tokens used: 42", 42),
        ("Tokens used: 1,234", 1234),
        ("prefix tokens used: 8 input, 2 output suffix", 10),
    ],
)
def test_tolerant_formats(text: str, expected: int) -> None:
    usage = parse_usage(text)
    assert usage is not None
    total = usage.total_tokens or usage.input_tokens + usage.output_tokens
    assert total == expected
