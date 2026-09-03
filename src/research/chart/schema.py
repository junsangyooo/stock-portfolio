"""IndicatorFact schema — the OBJECTIVE output of every indicator.

The quantitative pipeline only EXTRACTS facts. It does NOT judge (no buy/sell
vote, no aggregate score, no label). Reading them — "RSI oversold + resistance
break" — is src/research/analyze's job, with chart_rules.md as the dictionary
for what each field means.

So an indicator reports a measured `value` and an OBJECTIVE `state` (e.g.
"oversold", "golden_cross", "above_cloud", "squeeze_on") plus `evidence`. These
are facts, true by definition of the indicator — never a recommendation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

Category = Literal["trend", "momentum", "volatility", "volume", "structure"]
Timeframe = Literal["1d", "1w", "1mo"]

# State sentinel for "indicator could not be computed (insufficient data)".
STATE_NO_DATA = "no_data"


@dataclass
class IndicatorFact:
    """One indicator's objective reading on one timeframe.

    Attributes:
        indicator: unique name, e.g. "rsi" / "macd" / "ema_alignment".
        category:  trend | momentum | volatility | volume | structure.
        timeframe: the bar timeframe this fact was computed on.
        state:     OBJECTIVE state label (no buy/sell judgment). Examples:
                   rsi -> oversold|overbought|neutral
                   macd -> golden_cross|dead_cross|above_signal|below_signal
                   ema_alignment -> bull_stack|bear_stack|mixed
                   ichimoku -> above_cloud|below_cloud|in_cloud
                   "no_data" when insufficient bars.
        value:     primary numeric reading, if scalar.
        evidence:  supporting raw numbers (for the judgment agent / UI).
        asof:      date (YYYY-MM-DD) of the closed bar used.
    """

    indicator: str
    category: Category
    timeframe: Timeframe
    state: str
    value: Optional[float] = None
    evidence: dict = field(default_factory=dict)
    asof: Optional[str] = None

    @property
    def has_data(self) -> bool:
        return self.state != STATE_NO_DATA

    def to_dict(self) -> dict:
        return {
            "indicator": self.indicator,
            "category": self.category,
            "timeframe": self.timeframe,
            "state": self.state,
            "value": self.value,
            "evidence": self.evidence,
            "asof": self.asof,
        }
