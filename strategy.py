import logging
from dataclasses import dataclass
from datetime import time
from typing import Optional

import pandas as pd
import pandas_ta as ta

logger = logging.getLogger(__name__)

TRADING_START = time(9, 15)
TRADING_END = time(15, 40)
IB_LOCK_END = time(10, 15)


@dataclass
class MarketState:
    state: str  # "TRENDING" or "SIDEWAYS"
    disable_signals: bool
    reason: Optional[str] = None


@dataclass
class Signal:
    direction: str  # "LONG" or "SHORT"
    type: str  # breakout_retest | liquidity_trap | inside_bar
    level: float
    details: dict


def in_trading_window(ts: pd.Timestamp) -> bool:
    t = ts.time()
    return TRADING_START <= t <= TRADING_END


def compute_cpr(df: pd.DataFrame) -> pd.DataFrame:
    # Assume df is daily; for intraday, pass prior day close/high/low via shift
    df = df.copy()
    pivot = (df["high"] + df["low"] + df["close"]) / 3
    bc = (df["high"] + df["low"]) / 2
    tc = 2 * pivot - bc
    df["pivot"] = pivot
    df["bc"] = bc
    df["tc"] = tc
    return df


def market_state_filters(df5: pd.DataFrame) -> MarketState:
    df = df5.copy()
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)
    df = compute_cpr(df)
    last = df.iloc[-1]
    width = abs(last.tc - last.bc)
    atr = last.atr
    state = "TRENDING"
    reason = None
    if pd.notna(atr) and width > atr * 1.5:
        state = "SIDEWAYS"
        reason = "CPR width > 1.5*ATR"
    # 20 EMA slope
    df["ema20"] = ta.ema(df["close"], length=20)
    recent = df["ema20"].tail(5)
    slope_deg = None
    if recent.count() == 5:
        y = recent.values
        x = range(len(y))
        # simple slope in degrees
        import numpy as np

        m, _ = np.polyfit(x, y, 1)
        slope_deg = np.degrees(np.arctan(m))
        if -15 <= slope_deg <= 15:
            state = "SIDEWAYS"
            reason = "EMA20 slope flat"
    disable = state == "SIDEWAYS"
    return MarketState(state=state, disable_signals=disable, reason=reason)


def lock_initial_balance(df5: pd.DataFrame) -> tuple[float, float]:
    ib = df5.between_time(TRADING_START, IB_LOCK_END)
    return float(ib.high.max()), float(ib.low.min())


def breakout_retest_signal(df5: pd.DataFrame, ib_high: float, ib_low: float) -> Optional[Signal]:
    last = df5.iloc[-1]
    prev = df5.iloc[-2]
    # breakout above IB high or PDH (use ib_high here)
    if prev.close <= ib_high and last.close > ib_high:
        level = ib_high
        # retest check: wick rejection (lower wick > 2x body)
        body = abs(last.close - last.open)
        lower_wick = last.open - last.low if last.close >= last.open else last.close - last.low
        if lower_wick > 2 * body:
            return Signal(
                direction="LONG",
                type="breakout_retest",
                level=level,
                details={
                    "wick": lower_wick,
                    "body": body,
                    "rejection_low": float(last.low),
                    "rejection_high": float(last.high),
                },
            )
    # symmetric short side
    if prev.close >= ib_low and last.close < ib_low:
        level = ib_low
        body = abs(last.open - last.close)
        upper_wick = last.high - max(last.open, last.close)
        if upper_wick > 2 * body:
            return Signal(
                direction="SHORT",
                type="breakout_retest",
                level=level,
                details={
                    "wick": upper_wick,
                    "body": body,
                    "rejection_low": float(last.low),
                    "rejection_high": float(last.high),
                },
            )
    return None


def liquidity_trap_signal(df5: pd.DataFrame, ib_low: float, ib_high: float, volume_available: bool) -> Optional[Signal]:
    # long trap below IB low then close back above with volume > SMA20
    if len(df5) < 3:
        return None
    recent = df5.iloc[-3:]
    vol_ok = True
    if volume_available:
        recent["vma20"] = ta.sma(df5["volume"], length=20).iloc[-3:]
        vol_ok = (recent["volume"] > recent["vma20"]).any()
    first = recent.iloc[0]
    rest = recent.iloc[1:]
    broke = first.low < ib_low
    recovered = (rest.close > ib_low).any()
    if broke and recovered and vol_ok:
        return Signal(
            direction="LONG",
            type="liquidity_trap",
            level=ib_low,
            details={
                "volume_checked": volume_available,
                "fakeout_low": float(first.low),
                "fakeout_high": float(first.high),
            },
        )
    # symmetric short trap above IB high
    broke_up = first.high > ib_high
    recovered_down = (rest.close < ib_high).any()
    if broke_up and recovered_down and vol_ok:
        return Signal(
            direction="SHORT",
            type="liquidity_trap",
            level=ib_high,
            details={
                "volume_checked": volume_available,
                "fakeout_low": float(first.low),
                "fakeout_high": float(first.high),
            },
        )
    return None


def inside_bar_signal(df5: pd.DataFrame) -> Optional[Signal]:
    if len(df5) < 3:
        return None
    mother = df5.iloc[-3]
    inside = df5.iloc[-2]
    last = df5.iloc[-1]
    if inside.high < mother.high and inside.low > mother.low:
        if last.close > mother.high:
            return Signal(
                "LONG",
                "inside_bar",
                mother.high,
                {"trigger": "break_high", "mother_low": float(mother.low), "mother_high": float(mother.high)},
            )
        if last.close < mother.low:
            return Signal(
                "SHORT",
                "inside_bar",
                mother.low,
                {"trigger": "break_low", "mother_low": float(mother.low), "mother_high": float(mother.high)},
            )
    return None


def generate_signal(df5: pd.DataFrame, df15: pd.DataFrame, volume_available: bool) -> Optional[Signal]:
    # Ensure trading window and sufficient data
    if len(df5) < 25:
        return None
    last_ts = df5.index[-1]
    if not in_trading_window(last_ts):
        return None
    ib_high, ib_low = lock_initial_balance(df5)
    # block trades inside IB range after lock
    last = df5.iloc[-1]
    if ib_low <= last.close <= ib_high:
        return None

    mstate = market_state_filters(df5)
    if mstate.disable_signals:
        logger.info("signals disabled: %s", mstate.reason)
        return None

    sig = breakout_retest_signal(df5, ib_high, ib_low)
    if sig:
        return sig
    sig = liquidity_trap_signal(df5, ib_low, ib_high, volume_available)
    if sig:
        return sig
    sig = inside_bar_signal(df5)
    return sig
