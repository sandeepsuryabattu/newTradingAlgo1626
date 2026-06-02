import asyncio
import logging
from datetime import datetime
from typing import List, Optional, Dict

import pandas as pd
import uvicorn

from config import load_config
from data_feed import KotakNeoFeed, Tick, resample_ticks
from storage import Storage, SignalRecord
from strategy import generate_signal
from telegram_client import TelegramClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def start_web_server(host: str = "0.0.0.0", port: int = 8000):
    """Start FastAPI dashboard as a background coroutine."""
    config = uvicorn.Config("web:app", host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


class SignalEngine:
    def __init__(self):
        self.cfg = load_config()
        self.storage = Storage()  # uses local SQLite (storage.db by default)
        self.tg = TelegramClient()
        self.ticks: List[Tick] = []
        self.crude_ticks: List[Tick] = []
        self.last_notified: Dict[int, str] = {}

    # ---------- Helpers for SL/TP computation ----------
    def _compute_initial_levels(self, sig) -> dict:
        """Compute entry, SL, RR targets per confirmed rules."""
        entry = float(sig.level)
        direction = sig.direction.upper()
        details = sig.details or {}
        if sig.type == "breakout_retest":
            if direction == "LONG":
                sl = float(details.get("rejection_low", entry)) - 10
            else:
                sl = float(details.get("rejection_high", entry)) + 10
        elif sig.type == "liquidity_trap":
            sl = float(details.get("fakeout_low" if direction == "LONG" else "fakeout_high", entry))
        elif sig.type == "inside_bar":
            if direction == "LONG":
                sl = float(details.get("mother_low", entry))
            else:
                sl = float(details.get("mother_high", entry))
        else:
            sl = entry
        if direction == "LONG":
            risk = entry - sl
            target1 = entry + 2 * risk if risk > 0 else None
        else:
            risk = sl - entry
            target1 = entry - 2 * risk if risk > 0 else None
        rr = 2.0 if risk else None
        return {
            "entry_price": entry,
            "sl_initial": sl,
            "sl_active": sl,
            "target1": target1,
            "rr": rr,
            "trail_basis": "ema9_5m",
        }

    def _format_signal_msg(self, sig: SignalRecord) -> str:
        base = f"{sig.direction} {sig.signal_type} at {sig.entry_price}"
        extra = []
        if sig.sl_initial is not None:
            extra.append(f"SL {sig.sl_initial}")
        if sig.target1 is not None:
            extra.append(f"T1 {sig.target1}")
        extra.append(f"status: {sig.status}")
        if sig.reason:
            extra.append(f"reason: {sig.reason}")
        return base + " — " + ", ".join(extra)

    def _send_update(self, text: str):
        try:
            self.tg.send_message(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("telegram send failed: %s", exc)

    def on_tick(self, tick: Tick):
        sym = (tick.symbol or "").upper()
        if self.cfg.crude_symbol.upper() in sym or "CRUDE" in sym:
            self.crude_ticks.append(tick)
            self.storage.insert_crude_tick(tick.ts, tick.price, tick.volume)
        else:
            self.ticks.append(tick)
            self.storage.insert_tick(tick.ts, tick.price, tick.volume)

    def _manage_open_positions(self, df5: pd.DataFrame):
        if df5.empty:
            return
        ema9_series = df5["close"].ewm(span=9, adjust=False).mean()
        ema9 = float(ema9_series.iloc[-1]) if not ema9_series.empty else None
        last = df5.iloc[-1]
        open_, high, low, close = float(last.open), float(last.high), float(last.low), float(last.close)
        prior = df5.iloc[-2] if len(df5) >= 2 else last
        prior_open, prior_high, prior_low, prior_close = (
            float(prior.open),
            float(prior.high),
            float(prior.low),
            float(prior.close),
        )

        # read closed-candle-only preference
        closed_only = (self.storage.get_setting("closed_candle_trailing", "false") or "false").lower() == "true"

        for rec in self.storage.fetch_open_signals():
            direction = rec["direction"].upper()
            entry = rec.get("entry_price") or rec.get("level")
            sl_active = rec.get("sl_active") or rec.get("sl_initial")
            target1 = rec.get("target1")
            hit_t1 = bool(rec.get("hit_target1"))
            updates = {"updated_at": datetime.utcnow()}
            messages: List[str] = []

            # Target 1 hit check (intra-bar high/low)
            if not hit_t1 and target1 is not None:
                if direction == "LONG" and high >= target1:
                    updates["hit_target1"] = 1
                    updates["sl_active"] = entry  # move to breakeven
                    updates.setdefault("reason", "Target1 hit; SL moved to BE")
                    messages.append(f"T1 hit ({target1}); SL to BE {entry}")
                elif direction == "SHORT" and low <= target1:
                    updates["hit_target1"] = 1
                    updates["sl_active"] = entry
                    updates.setdefault("reason", "Target1 hit; SL moved to BE")
                    messages.append(f"T1 hit ({target1}); SL to BE {entry}")

            # Trail SL to EMA9 after T1 (use prior close if closed-only)
            if ema9 is not None and (hit_t1 or updates.get("hit_target1")):
                ema_ref = ema9 if not closed_only else float(df5["close"].ewm(span=9, adjust=False).mean().iloc[-2]) if len(df5) >= 2 else ema9
                new_sl = ema_ref
                if direction == "LONG":
                    if sl_active is None or new_sl > sl_active:
                        updates["sl_active"] = new_sl
                        updates.setdefault("reason", "Trailing SL via EMA9")
                        messages.append(f"SL trailed to EMA9 {round(new_sl,2)}")
                else:
                    if sl_active is None or new_sl < sl_active:
                        updates["sl_active"] = new_sl
                        updates.setdefault("reason", "Trailing SL via EMA9")
                        messages.append(f"SL trailed to EMA9 {round(new_sl,2)}")
                sl_active = updates.get("sl_active", sl_active)

            # Exit checks: SL hit intra-bar or 5m body beyond EMA9
            exit_reason: Optional[str] = None
            sl_ref = updates.get("sl_active", sl_active)
            if sl_ref is not None:
                if direction == "LONG" and low <= sl_ref:
                    exit_reason = f"SL hit {sl_ref}"
                elif direction == "SHORT" and high >= sl_ref:
                    exit_reason = f"SL hit {sl_ref}"
            ema_close = ema9
            body_open, body_close = (open_, close) if not closed_only else (prior_open, prior_close)
            ema_body_ref = ema_close if not closed_only else float(df5["close"].ewm(span=9, adjust=False).mean().iloc[-2]) if len(df5) >= 2 else ema_close
            if ema_body_ref is not None:
                if direction == "LONG" and body_open < ema_body_ref and body_close < ema_body_ref:
                    exit_reason = exit_reason or "Body close below EMA9"
                if direction == "SHORT" and body_open > ema_body_ref and body_close > ema_body_ref:
                    exit_reason = exit_reason or "Body close above EMA9"

            if exit_reason:
                updates.update({"status": "closed", "exited": 1, "reason": exit_reason})
                messages.append(f"Exit: {exit_reason}")

            # Persist updates if any
            if len(updates) > 1:  # more than updated_at
                self.storage.update_signal_fields(rec["id"], updates)
                if messages:
                    msg = f"{direction} {rec['signal_type']} update: " + "; ".join(messages)
                    last_msg = self.last_notified.get(rec["id"])
                    if msg != last_msg:
                        self._send_update(msg)
                        self.last_notified[rec["id"]] = msg

    async def run(self):
        # Start web dashboard in background so it's always accessible
        asyncio.create_task(start_web_server())
        feed = KotakNeoFeed(on_tick=self.on_tick)
        asyncio.create_task(feed.connect())
        while True:
            await asyncio.sleep(5)
            df5 = resample_ticks(self.ticks, "5min")
            df15 = resample_ticks(self.ticks, "15min")
            self.storage.insert_ohlc("5m", df5)
            self.storage.insert_ohlc("15m", df15)
            # Crude OHLC
            crude5 = resample_ticks(self.crude_ticks, "5min")
            crude15 = resample_ticks(self.crude_ticks, "15min")
            self.storage.insert_crude_ohlc("5m", crude5)
            self.storage.insert_crude_ohlc("15m", crude15)
            # Manage open positions (trailing, exits)
            self._manage_open_positions(df5)
            sig = generate_signal(df5, df15, volume_available=df5["volume"].notna().any())
            if sig:
                levels = self._compute_initial_levels(sig)
                rec = SignalRecord(
                    ts=df5.index[-1],
                    signal_type=sig.type,
                    direction=sig.direction,
                    level=sig.level,
                    status="open",
                    reason=sig.details.get("trigger") or sig.details.get("reason") or "",
                    details=sig.details,
                    entry_price=levels["entry_price"],
                    sl_initial=levels["sl_initial"],
                    sl_active=levels["sl_active"],
                    target1=levels["target1"],
                    rr=levels["rr"],
                    trail_basis=levels["trail_basis"],
                )
                self.storage.insert_signal(rec)
                self._send_update(self._format_signal_msg(rec))


def main():
    engine = SignalEngine()
    asyncio.run(engine.run())


if __name__ == "__main__":
    main()
