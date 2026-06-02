import logging
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)


DEFAULT_URL = "https://developers.kotaksecurities.com/scrip-master"


def download_scrip_master(url: str = DEFAULT_URL, dest: str = "scrip_master.csv") -> Path:
    """Download the daily scrip master CSV/ZIP from Kotak."""
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    dest_path = Path(dest)
    dest_path.write_bytes(resp.content)
    logger.info("Downloaded scrip master to %s", dest_path)
    return dest_path


def resolve_token_from_master(df: pd.DataFrame, symbol: str, exchange_segment: str) -> Optional[str]:
    cols = {c.lower(): c for c in df.columns}
    inst_col = cols.get("instrument_token") or cols.get("instrumenttoken")
    sym_col = cols.get("trading_symbol") or cols.get("symbol") or cols.get("tradingsymbol")
    exch_col = cols.get("exchange_segment") or cols.get("exchseg") or cols.get("exch_segment")
    if not (inst_col and sym_col and exch_col):
        logger.error("Missing columns in scrip master")
        return None
    matches = df[
        (df[sym_col].astype(str).str.upper() == symbol.upper())
        & (df[exch_col].astype(str).str.lower() == exchange_segment.lower())
    ]
    if matches.empty:
        logger.error("No match for %s on %s", symbol, exchange_segment)
        return None
    return str(matches.iloc[0][inst_col])


def load_master(dest: Path) -> pd.DataFrame:
    if dest.suffix.lower() == ".zip":
        return pd.read_csv(dest, compression="zip")
    return pd.read_csv(dest)


def auto_resolve_token(symbol: str, exchange_segment: str, download_url: str = DEFAULT_URL, dest: str = "scrip_master.csv") -> Optional[str]:
    path = download_scrip_master(download_url, dest)
    df = load_master(path)
    return resolve_token_from_master(df, symbol, exchange_segment)
