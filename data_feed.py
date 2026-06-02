import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

import pandas as pd
import pyotp
from neo_api_client import NeoAPI

from config import load_config
from scrip_master import find_index_token, load_scrip_master
from scrip_master import resolve_sensex_token

logger = logging.getLogger(__name__)


@dataclass
class Tick:
    ts: datetime
    price: float
    volume: Optional[float] = None
    symbol: Optional[str] = None


def resolve_crude_token() -> Optional[str]:
    cfg = load_config()
    if cfg.crude_instrument_token:
        return cfg.crude_instrument_token
    path = None
    if cfg.auto_refresh_token or cfg.scrip_master_path:
        try:
            if cfg.auto_refresh_token:
                from scrip_master_fetcher import download_scrip_master
                path = download_scrip_master(cfg.scrip_master_url, dest=cfg.scrip_master_path or "scrip_master.csv")
            else:
                path = Path(cfg.scrip_master_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to download scrip master: %s", exc)
    if not path or not Path(path).exists():
        logger.warning("SCRIP_MASTER_PATH not available; cannot resolve CRUDE token")
        return None
    try:
        df = load_scrip_master(path)
        token = find_index_token(df, cfg.crude_symbol, cfg.crude_exchange_segment)
        if token:
            logger.info("Resolved CRUDE token: %s", token)
        return token
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to resolve CRUDE token: %s", exc)
        return None


class KotakNeoFeed:
    """Light wrapper around neo_api_client for tick streaming."""

    def __init__(self, on_tick: Callable[[Tick], None]):
        self.cfg = load_config()
        self.on_tick = on_tick
        self.client = NeoAPI(
            environment=self.cfg.environment,
            neo_fin_key=self.cfg.neo_fin_key or None,
            consumer_key=self.cfg.consumer_key,
        )
        self._stop = False
        self._sub_tokens: list[dict] = []
        self._connected = False

    def _do_subscribe(self):
        """Subscribe to stored tokens using SDK's built-in subscribe."""
        if not self._sub_tokens:
            return
        sensex = [t for t in self._sub_tokens if t["exchange_segment"].lower() == self.cfg.sensex_exchange_segment.lower()]
        crude = [t for t in self._sub_tokens if t["exchange_segment"].lower() == self.cfg.crude_exchange_segment.lower()]
        if sensex:
            try:
                self.client.subscribe(instrument_tokens=sensex, isIndex=True, isDepth=False)
                logger.info("Subscribed SENSEX: %s", [t["instrument_token"] for t in sensex])
            except Exception as exc:  # noqa: BLE001
                logger.warning("SENSEX subscribe failed: %s", exc)
        if crude:
            try:
                self.client.subscribe(instrument_tokens=crude, isIndex=False, isDepth=False)
                logger.info("Subscribed CRUDE: %s", [t["instrument_token"] for t in crude])
            except Exception as exc:  # noqa: BLE001
                logger.warning("CRUDE subscribe failed: %s", exc)

    def _generate_totp(self) -> str:
        if not self.cfg.totp_secret:
            raise ValueError("TOTP_SECRET not set; provide totp code manually")
        return pyotp.TOTP(self.cfg.totp_secret).now()

    def login(self, totp_code: Optional[str] = None):
        code = totp_code or self._generate_totp()
        self.client.totp_login(
            mobile_number=self.cfg.mobile_number,
            ucc=self.cfg.ucc,
            totp=code,
        )
        token = self.client.totp_validate(mpin=self.cfg.mpin)
        # The SDK returns token in response; fall back to session_id if present
        if isinstance(token, dict):
            self.cfg.access_token = token.get("token") or token.get("access_token") or self.cfg.access_token
        if not self.cfg.access_token:
            self.cfg.access_token = getattr(self.client, "session_id", None) or self.cfg.access_token
        logger.info("Neo login completed; token acquired")

    def _handle_message(self, message):
        try:
            data = json.loads(message) if isinstance(message, str) else message
        except Exception:  # noqa: BLE001
            logger.debug("non-json message: %s", message)
            return
        tick = self._parse_tick(data)
        if tick:
            self.on_tick(tick)

    def _handle_error(self, error_message):
        logger.error("feed error: %s", error_message)

    def _handle_open(self, msg):
        logger.info("feed opened: %s", msg)
        self._connected = True
        self._do_subscribe()

    def _handle_close(self, msg):
        logger.info("feed closed: %s", msg)
        self._connected = False

    def _parse_tick(self, data: dict) -> Optional[Tick]:
        # Kotak SDK binary protocol maps to these JSON keys:
        #   ltp=last traded price, v=volume, tk=symbol, ftm0/dtm1=timestamps
        price = data.get("ltp") or data.get("last_price") or data.get("lastTradedPrice")
        ts_val = data.get("ftm0") or data.get("dtm1") or data.get("timestamp") or data.get("ts")
        volume = data.get("v") or data.get("volume") or data.get("vol")
        symbol = data.get("tk") or data.get("symbol") or data.get("tradingSymbol") or data.get("instrumentToken")
        if price is None:
            logger.debug("unparsed tick (no price): %s", data)
            return None
        if ts_val:
            if isinstance(ts_val, (int, float)):
                ts = datetime.fromtimestamp(ts_val / 1000 if ts_val > 1e10 else ts_val)
            else:
                ts = pd.to_datetime(ts_val)
        else:
            ts = datetime.now()
        return Tick(ts=ts, price=float(price), volume=volume, symbol=symbol)

    async def connect(self, totp_code: Optional[str] = None):
        # Login (if needed) then subscribe using SDK websocket
        self.login(totp_code=totp_code)

        self.client.on_message = self._handle_message
        self.client.on_error = self._handle_error
        self.client.on_close = self._handle_close
        self.client.on_open = self._handle_open

        # Download scrip master via authenticated SDK so token resolution works
        from scrip_master_fetcher import download_scrip_master_via_sdk, load_master, resolve_token_from_master

        sm_path = None
        try:
            sm_path = download_scrip_master_via_sdk(self.client, self.cfg.sensex_exchange_segment, dest="scrip_master_sensex.csv")
        except Exception as exc:  # noqa: BLE001
            logger.warning("SDK scrip master download failed: %s", exc)

        sensex_token = self.cfg.sensex_instrument_token
        if not sensex_token and sm_path:
            try:
                df = load_master(sm_path)
                sensex_token = resolve_token_from_master(df, self.cfg.sensex_symbol, self.cfg.sensex_exchange_segment, pick_nearest_expiry=True)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to resolve SENSEX from downloaded master: %s", exc)
        if not sensex_token:
            sensex_token = resolve_sensex_token()
        if not sensex_token:
            raise RuntimeError("Unable to resolve SENSEX instrument_token; set SENSEX_INSTRUMENT_TOKEN or SCRIP_MASTER_PATH")

        tokens = [
            {
                "instrument_token": sensex_token,
                "exchange_segment": self.cfg.sensex_exchange_segment,
            }
        ]

        # Resolve CRUDE using same scrip master (MCX segment)
        crude_token = self.cfg.crude_instrument_token
        if not crude_token:
            try:
                crude_path = download_scrip_master_via_sdk(self.client, self.cfg.crude_exchange_segment, dest="scrip_master_crude.csv")
                if crude_path:
                    df_crude = load_master(crude_path)
                    crude_token = resolve_token_from_master(df_crude, self.cfg.crude_symbol, self.cfg.crude_exchange_segment, inst_type="FUTCOM", pick_nearest_expiry=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to resolve CRUDE via SDK: %s", exc)
        if not crude_token:
            crude_token = resolve_crude_token()
        if crude_token:
            tokens.append({
                "instrument_token": crude_token,
                "exchange_segment": self.cfg.crude_exchange_segment,
            })
            logger.info("Subscribing to CRUDE: %s", crude_token)
        else:
            logger.warning("CRUDE token not resolved; skipping crude subscription")

        # Store tokens for reconnect resubscription
        self._sub_tokens = tokens[:]

        # Subscribe SENSEX as index and CRUDE as futures separately
        loop = asyncio.get_event_loop()
        sensex_tokens = [t for t in tokens if t["exchange_segment"].lower() == self.cfg.sensex_exchange_segment.lower()]
        crude_tokens = [t for t in tokens if t["exchange_segment"].lower() == self.cfg.crude_exchange_segment.lower()]
        if sensex_tokens:
            await loop.run_in_executor(
                None, lambda: self.client.subscribe(instrument_tokens=sensex_tokens, isIndex=True, isDepth=False)
            )
        if crude_tokens:
            await loop.run_in_executor(
                None, lambda: self.client.subscribe(instrument_tokens=crude_tokens, isIndex=False, isDepth=False)
            )
        # Client handles websocket internally; detect disconnect and re-subscribe via SDK
        _disconnected_at: Optional[float] = None
        while not self._stop:
            await asyncio.sleep(1)
            if not self._connected:
                if _disconnected_at is None:
                    _disconnected_at = asyncio.get_event_loop().time()
                elif asyncio.get_event_loop().time() - _disconnected_at > 10:
                    logger.info("Detected disconnect >10s; forcing re-subscribe via SDK")
                    try:
                        self._do_subscribe()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Re-subscribe failed: %s", exc)
                    _disconnected_at = None
            else:
                _disconnected_at = None

    def stop(self):
        self._stop = True
        try:
            tokens = [
                {
                    "instrument_token": resolve_sensex_token() or self.cfg.sensex_instrument_token,
                    "exchange_segment": self.cfg.sensex_exchange_segment,
                }
            ]
            crude_token = resolve_crude_token()
            if crude_token:
                tokens.append({
                    "instrument_token": crude_token,
                    "exchange_segment": self.cfg.crude_exchange_segment,
                })
            self.client.un_subscribe(instrument_tokens=tokens, isIndex=True, isDepth=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("unsubscribe error: %s", exc)


def resample_ticks(ticks: list[Tick], timeframe: str) -> pd.DataFrame:
    if not ticks:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(
        {
            "ts": [t.ts for t in ticks],
            "price": [t.price for t in ticks],
            "volume": [t.volume for t in ticks],
        }
    ).set_index("ts")
    ohlc = df["price"].resample(timeframe).ohlc()
    vol = df["volume"].resample(timeframe).sum()
    ohlc["volume"] = vol
    return ohlc.dropna(how="all")
