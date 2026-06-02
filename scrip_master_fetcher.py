import logging
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)


def download_scrip_master(url: str, dest: str = "scrip_master.csv") -> Path:
    """Download the daily scrip master CSV/ZIP from Kotak."""
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    dest_path = Path(dest)
    dest_path.write_bytes(resp.content)
    logger.info("Downloaded scrip master to %s", dest_path)
    return dest_path


def download_scrip_master_via_sdk(client, exchange_segment: str, dest: str = "scrip_master.csv") -> Optional[Path]:
    """Download scrip master using Kotak SDK to discover correct URL."""
    try:
        sm = client.scrip_master()
        files = sm.get("filesPaths", [])
        # map exchange_segment to file suffix
        seg_map = {
            "nse_cm": "nse_cm",
            "bse_cm": "bse_cm",
            "nse_fo": "nse_fo",
            "bse_fo": "bse_fo",
            "mcx_fo": "mcx_fo",
            "cde_fo": "cde_fo",
        }
        suffix = seg_map.get(exchange_segment.lower(), exchange_segment.lower().replace("_", ""))
        url = None
        for f in files:
            if suffix in f.lower():
                url = f
                break
        if not url:
            logger.error("No scrip master file found for %s in %s", exchange_segment, files)
            return None
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        dest_path = Path(dest)
        dest_path.write_bytes(resp.content)
        logger.info("Downloaded scrip master from SDK URL to %s", dest_path)
        return dest_path
    except Exception as exc:  # noqa: BLE001
        logger.error("SDK scrip master download failed: %s", exc)
        return None


def resolve_token_from_master(
    df: pd.DataFrame,
    symbol: str,
    exchange_segment: str,
    inst_type: Optional[str] = None,
    pick_nearest_expiry: bool = False,
) -> Optional[str]:
    cols = {c.lower(): c for c in df.columns}
    # Support both old flat names and Kotak p-prefix names
    inst_col = (
        cols.get("instrument_token")
        or cols.get("instrumenttoken")
        or cols.get("psymbol")
    )
    sym_col = (
        cols.get("trading_symbol")
        or cols.get("symbol")
        or cols.get("tradingsymbol")
        or cols.get("ptrdsymbol")
    )
    exch_col = (
        cols.get("exchange_segment")
        or cols.get("exchseg")
        or cols.get("exch_segment")
        or cols.get("pexchseg")
    )
    type_col = (
        cols.get("inst_type")
        or cols.get("pinsttype")
    )
    expiry_col = (
        cols.get("expiry_date")
        or cols.get("pexpirydate")
        or cols.get("l_expirydate")
    )
    if not (inst_col and sym_col and exch_col):
        logger.error("Missing columns in scrip master; have: %s", list(df.columns)[:10])
        return None

    # Try exact match first
    matches = df[
        (df[sym_col].astype(str).str.upper() == symbol.upper())
        & (df[exch_col].astype(str).str.lower() == exchange_segment.lower())
    ]

    # If no exact match and pick_nearest_expiry, find base symbol futures
    if matches.empty and pick_nearest_expiry:
        base_mask = (
            df[sym_col].astype(str).str.upper().str.startswith(symbol.upper())
            & (df[exch_col].astype(str).str.lower() == exchange_segment.lower())
        )
        if inst_type and type_col:
            base_mask &= df[type_col].astype(str).str.upper() == inst_type.upper()
        candidates = df[base_mask]
        if not candidates.empty and expiry_col:
            # pick nearest expiry (smallest expiry date value)
            candidates = candidates.copy()
            candidates["_exp"] = pd.to_numeric(candidates[expiry_col], errors="coerce")
            candidates = candidates[candidates["_exp"] > 0]
            if not candidates.empty:
                nearest = candidates.loc[candidates["_exp"].idxmin()]
                token = str(nearest[inst_col])
                sym = str(nearest[sym_col])
                logger.info("Resolved nearest expiry: %s -> %s", sym, token)
                return token
        matches = candidates

    if matches.empty:
        logger.error("No match for %s on %s", symbol, exchange_segment)
        return None
    return str(matches.iloc[0][inst_col])


def load_master(dest: Path) -> pd.DataFrame:
    if dest.suffix.lower() == ".zip":
        return pd.read_csv(dest, compression="zip")
    return pd.read_csv(dest)


def auto_resolve_token(symbol: str, exchange_segment: str, download_url: str = "", dest: str = "scrip_master.csv") -> Optional[str]:
    if download_url:
        path = download_scrip_master(download_url, dest)
    else:
        raise ValueError("download_url required for auto_resolve_token")
    df = load_master(path)
    return resolve_token_from_master(df, symbol, exchange_segment)
