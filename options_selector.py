import math
import logging
from dataclasses import dataclass
from datetime import datetime, date, time, timedelta
from typing import Optional

import pandas as pd
from scipy.stats import norm

from config import load_config

logger = logging.getLogger(__name__)


@dataclass
class OptionContract:
    symbol: str
    strike: float
    expiry: date
    option_type: str  # CE/PE
    delta: float
    ltp: float


@dataclass
class SelectionResult:
    contract: OptionContract
    reason: str


class OptionSelector:
    def __init__(self):
        self.cfg = load_config()

    def _weekday_bucket(self, dt: datetime) -> tuple[float, float]:
        dow = dt.weekday()  # Monday=0
        if dow in (4, 0):  # Friday, Monday
            return 0.45, 0.55
        if dow in (1, 2):  # Tue, Wed
            return 0.50, 0.65
        if dow == 3:  # Thu
            return 0.65, 1.0
        return 0.45, 0.55

    def _nearest_thursday(self, dt: datetime) -> date:
        # Next or same Thursday
        days_ahead = (3 - dt.weekday()) % 7
        if days_ahead == 0:
            return dt.date()
        return (dt + timedelta(days=days_ahead)).date()

    def select(self, option_chain: pd.DataFrame, now: datetime, signal_direction: str) -> Optional[SelectionResult]:
        lo, hi = self._weekday_bucket(now)
        expiry = self._nearest_thursday(now)
        chain = option_chain[option_chain["expiry"] == expiry]
        if chain.empty:
            logger.warning("no contracts for expiry %s", expiry)
            return None
        chain = chain.copy()
        chain["delta_abs"] = chain["delta"].abs()
        filtered = chain[(chain["delta_abs"] >= lo) & (chain["delta_abs"] <= hi)]
        if filtered.empty:
            return None
        # choose by closest delta to upper bound when expiry day, else mid bucket
        target = hi if now.weekday() == 3 else (lo + hi) / 2
        filtered["delta_dist"] = (filtered["delta_abs"] - target).abs()
        pick = filtered.sort_values(["delta_dist", "expiry"], ascending=[True, True]).iloc[0]
        contract = OptionContract(
            symbol=pick["symbol"],
            strike=float(pick["strike"]),
            expiry=pick["expiry"],
            option_type=pick["type"],
            delta=float(pick["delta"]),
            ltp=float(pick["ltp"]),
        )
        return SelectionResult(contract=contract, reason=f"delta bucket {lo}-{hi}")

    @staticmethod
    def black_scholes_delta(spot: float, strike: float, t_expiry_years: float, rate: float, iv: float, call: bool) -> float:
        if spot <= 0 or strike <= 0 or t_expiry_years <= 0 or iv <= 0:
            raise ValueError("invalid inputs for delta")
        d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * t_expiry_years) / (iv * math.sqrt(t_expiry_years))
        if call:
            return norm.cdf(d1)
        return -norm.cdf(-d1)
