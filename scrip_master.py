import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from config import load_config
from scrip_master_fetcher import auto_resolve_token, download_scrip_master

logger = logging.getLogger(__name__)


def load_scrip_master(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Scrip master not found: {path}")
    if p.suffix.lower() == ".zip":
        return pd.read_csv(p, compression="zip")
    return pd.read_csv(p)


def find_index_token(df: pd.DataFrame, symbol: str, exchange_segment: str) -> Optional[str]:
    cols = {c.lower(): c for c in df.columns}
    # common columns in Kotak scrip master: instrument_token, trading_symbol, exchange_segment (may differ)
    inst_col = cols.get("instrument_token") or cols.get("instrumenttoken")
    sym_col = cols.get("trading_symbol") or cols.get("symbol") or cols.get("tradingsymbol")
    exch_col = cols.get("exchange_segment") or cols.get("exchseg") or cols.get("exch_segment")
    if not (inst_col and sym_col and exch_col):
        logger.error("Could not find necessary columns in scrip master")
        return None
    matches = df[
        (df[sym_col].astype(str).str.upper() == symbol.upper())
        & (df[exch_col].astype(str).str.lower() == exchange_segment.lower())
    ]
    if matches.empty:
        logger.error("No scrip match for %s on %s", symbol, exchange_segment)
        return None
    token = str(matches.iloc[0][inst_col])
    return token


def resolve_sensex_token() -> Optional[str]:
    cfg = load_config()
    if cfg.sensex_instrument_token:
        return cfg.sensex_instrument_token
    path = None
    if cfg.auto_refresh_token or cfg.scrip_master_path:
        try:
            if cfg.auto_refresh_token:
                path = download_scrip_master(cfg.scrip_master_url, dest=cfg.scrip_master_path or "scrip_master.csv")
            else:
                path = Path(cfg.scrip_master_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to download scrip master: %s", exc)
    if not path or not Path(path).exists():
        logger.warning("SCRIP_MASTER_PATH not available; cannot resolve SENSEX token")
        return None
    df = load_scrip_master(path)
    token = find_index_token(df, cfg.sensex_symbol, cfg.sensex_exchange_segment)
    if token:
        logger.info("Resolved SENSEX token: %s", token)
    return token
