"""Per-model pricing for CostLog rows.

Google does not expose a live per-model pricing API for Gemini - rates are
published on a page, not served over a billing endpoint. So "from the provider"
here means: resolve the rate for the *actual model that ran* (env override ->
provider table), never a hardcoded guess, and never block a run on a missing
price. Unknown models price at 0 rather than raising - the cost log for that
run just reads as free.

Rates are $ per 1K tokens. Override any model without a code change via
PIPELINE_AGENT_PRICE_<MODEL>="in,out" (model uppercased, non-alnum -> "_"),
e.g. PIPELINE_AGENT_PRICE_GEMINI_2_5_FLASH="0.00030,0.00250".
"""

from __future__ import annotations

import os
import re
from decimal import Decimal, InvalidOperation

# (input $ / 1K tokens, output $ / 1K tokens) - provider list prices for the
# models this pipeline can run. Keyed by normalized model id.
_PRICING_PER_1K: dict[str, tuple[Decimal, Decimal]] = {
    "gemini-2.5-flash": (Decimal("0.00030"), Decimal("0.00250")),
    "gemini-2.5-pro": (Decimal("0.00125"), Decimal("0.01000")),
}


def _normalize_model(model: str) -> str:
    """Map a provider model string to its pricing key.

    Handles versioned/prefixed ids ("models/gemini-2.5-flash",
    "gemini-2.5-flash-001") so the rate follows the model that actually ran.
    """
    m = (model or "").strip().lower()
    m = m.split("/")[-1]  # strip "models/" or provider prefix
    for known in _PRICING_PER_1K:
        if m == known or m.startswith(known):
            return known
    return m


def _env_override(model: str) -> tuple[Decimal, Decimal] | None:
    key = "PIPELINE_AGENT_PRICE_" + re.sub(r"[^A-Z0-9]+", "_", model.upper()).strip("_")
    raw = os.getenv(key, "").strip()
    if not raw:
        return None
    try:
        in_rate, out_rate = (part.strip() for part in raw.split(",", 1))
        return Decimal(in_rate), Decimal(out_rate)
    except (ValueError, InvalidOperation):
        return None


def rates_for_model(model: str) -> tuple[Decimal, Decimal]:
    """Resolve (input, output) $/1K for the model that ran.

    Order: env override -> provider table (normalized). 0/0 if unknown."""
    override = _env_override(model)
    if override is not None:
        return override
    return _PRICING_PER_1K.get(_normalize_model(model), (Decimal(0), Decimal(0)))


def price_usage(model: str, input_tokens: int, output_tokens: int) -> tuple[Decimal, Decimal, Decimal]:
    """Return (input_cost, output_cost, total_cost) in USD for the given token counts."""
    input_rate, output_rate = rates_for_model(model)
    input_cost = (Decimal(input_tokens) / Decimal(1000)) * input_rate
    output_cost = (Decimal(output_tokens) / Decimal(1000)) * output_rate
    return input_cost, output_cost, input_cost + output_cost
