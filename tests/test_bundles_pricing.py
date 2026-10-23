"""Unit tests for skill bundle resolution and model pricing."""

from __future__ import annotations

import importlib
import json
import logging
from decimal import Decimal

import pytest

from agents.pipeline_agent import pricing
from agents.skills import bundles


class TestBundles:
    def test_salesforce_kind_gets_quality_bundle(self) -> None:
        assert bundles.production_bundle_slugs("salesforce") == [
            "salesforce-developer",
            "salesforce-apex-quality",
        ]

    def test_unknown_and_none_kinds_get_base_only(self) -> None:
        assert bundles.production_bundle_slugs(None) == []
        assert bundles.production_bundle_slugs("default") == []
        assert bundles.production_bundle_slugs("nonsense") == []


class TestPricing:
    def test_known_model_prices_from_table(self) -> None:
        _, _, total = pricing.price_usage("gemini-2.5-flash", 1_000_000, 100_000)
        assert total.normalize() == Decimal("0.55")

    def test_unknown_model_is_free_but_warns(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(pricing, "_warned_unknown_models", set())
        with caplog.at_level(logging.WARNING, logger=pricing.__name__):
            input_cost, output_cost, total = pricing.price_usage("mystery-model", 1000, 1000)
        assert (input_cost, output_cost, total) == (Decimal(0), Decimal(0), Decimal(0))
        assert any("mystery-model" in record.message for record in caplog.records)
        # Warn once per process, not per row.
        with caplog.at_level(logging.WARNING, logger=pricing.__name__):
            pricing.price_usage("mystery-model", 5, 5)
        first_warnings = [r for r in caplog.records if "mystery-model" in r.message]
        assert len(first_warnings) == 1

    def test_env_overrides_add_models(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "LLM_PRICING_JSON",
            json.dumps({"custom-llm": {"input": 3.0, "output": 15.0}}),
        )
        reloaded = importlib.reload(pricing)
        try:
            _, _, total = reloaded.price_usage("custom-llm", 1_000_000, 1_000_000)
            assert total == Decimal("18")
            # Existing entries survive.
            _, _, gemini_total = reloaded.price_usage("gemini-2.5-pro", 1000, 1000)
            assert gemini_total > 0
        finally:
            importlib.reload(pricing)  # restore pristine table

    def test_malformed_override_json_does_not_crash_import(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("LLM_PRICING_JSON", "{not json")
        with caplog.at_level(logging.WARNING, logger=pricing.__name__):
            reloaded = importlib.reload(pricing)
        importlib.reload(pricing)
        assert any("LLM_PRICING_JSON" in record.message for record in caplog.records)
        # Built-in table still usable.
        assert reloaded.price_usage("gemini-2.5-flash", 0, 0)[2] == 0
