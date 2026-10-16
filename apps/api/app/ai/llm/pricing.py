"""Estimated cost at list price (CLAUDE.md decision 8).

We run on free tiers, so nobody is billed. The number we store answers "what would this have
cost on the paid tier", which is what the daily cap and the analytics need. It is always
labelled an ESTIMATE, and it is only as current as the table below.

Prices are USD per 1 million tokens, checked against the providers' pricing pages on
2026-10-17:
  * Groq:   https://console.groq.com/docs/models (production models table)
  * Gemini: https://ai.google.dev/gemini-api/docs/pricing ("Last updated 2026-10-15")
Output prices cover reasoning/thinking tokens too (both providers bill them as output).

A model can have several prices with `effective_from` dates, because providers announce
changes in advance (Gemini 3.8 Flash doubles on 2027-01-01). The price applied is the latest
one whose date is not after the day of the call.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

PRICES_CHECKED_ON = date(2026, 10, 17)
_ALWAYS = date.min
_MILLION = Decimal(1_000_000)
_SIX_DECIMALS = Decimal("0.000001")  # review_runs.cost_usd is NUMERIC(12, 6)


@dataclass(frozen=True)
class Price:
    effective_from: date
    input_per_million: Decimal
    output_per_million: Decimal


def _p(effective_from: date, input_usd: str, output_usd: str) -> Price:
    return Price(effective_from, Decimal(input_usd), Decimal(output_usd))


PRICING: dict[str, tuple[Price, ...]] = {
    # Groq
    "openai/gpt-oss-120b": (_p(_ALWAYS, "0.15", "0.60"),),
    "openai/gpt-oss-20b": (_p(_ALWAYS, "0.075", "0.30"),),
    # Gemini
    "gemini-3.8-flash": (
        _p(_ALWAYS, "0.75", "3.75"),
        _p(date(2027, 1, 1), "1.50", "7.50"),
    ),
    "gemini-3.6-flash": (
        _p(_ALWAYS, "0.75", "3.75"),
        _p(date(2027, 1, 1), "1.50", "7.50"),
    ),
    "gemini-3.5-flash-lite": (_p(_ALWAYS, "0.30", "2.50"),),
    # Tests and local development: free by definition.
    "fake-llm": (_p(_ALWAYS, "0", "0"),),
}


class UnknownModelPrice(ValueError):
    """The model is not in the table. Raised at startup so a run never records cost 0."""


def price_for(model: str, on: date) -> Price:
    prices = PRICING.get(model)
    if not prices:
        raise UnknownModelPrice(
            f"no list price for model '{model}': add it to app/ai/llm/pricing.py "
            "(from the provider's pricing page) before using it"
        )
    applicable = [p for p in prices if p.effective_from <= on]
    return max(applicable, key=lambda p: p.effective_from)


def estimate_cost(model: str, tokens_in: int, tokens_out: int, *, on: date) -> Decimal:
    """Estimated USD for one call, rounded to the column's 6 decimal places."""
    price = price_for(model, on)
    input_cost = Decimal(tokens_in) * price.input_per_million
    output_cost = Decimal(tokens_out) * price.output_per_million
    cost = (input_cost + output_cost) / _MILLION
    return cost.quantize(_SIX_DECIMALS)
