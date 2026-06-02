import logging
from pathlib import Path
from typing import Any, List

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi import Body
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, text

from config import load_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

cfg = load_config()


def get_engine():
    """Lazy engine creation using local SQLite."""
    if not getattr(get_engine, "_engine", None):
        try:
            get_engine._engine = create_engine(f"sqlite:///{cfg.sqlite_path}", future=True)
        except Exception as exc:  # noqa: BLE001
            logger.error("failed to create engine: %s", exc)
            get_engine._engine = None
    return get_engine._engine


app = FastAPI(title="SENSEX Signals Dashboard")

static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/api/ohlc/{timeframe}")
async def ohlc(timeframe: str, limit: int = Query(200, ge=10, le=2000)) -> List[dict[str, Any]]:
    engine = get_engine()
    if engine is None:
        raise HTTPException(status_code=503, detail="DB not configured")
    sql = text(
        """
        SELECT ts, open, high, low, close, volume
        FROM ohlc
        WHERE timeframe = :tf
        ORDER BY ts DESC
        LIMIT :lim
        """
    )
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"tf": timeframe, "lim": limit})
    if df.empty:
        return []
    df.sort_values("ts", inplace=True)
    return df.to_dict(orient="records")


@app.get("/api/settings/closed_candle_trailing")
async def get_closed_candle_flag() -> dict[str, str]:
    engine = get_engine()
    if engine is None:
        raise HTTPException(status_code=503, detail="DB not configured")
    with engine.connect() as conn:
        val = conn.execute(text("SELECT value FROM settings WHERE key = 'closed_candle_trailing'"))
        v = val.scalar()
    return {"value": v or "false"}


@app.post("/api/settings/closed_candle_trailing")
async def set_closed_candle_flag(payload: dict = Body(...)) -> dict[str, str]:
    engine = get_engine()
    if engine is None:
        raise HTTPException(status_code=503, detail="DB not configured")
    value = str(payload.get("value", "false")).lower()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO settings (key, value) VALUES ('closed_candle_trailing', :v)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """
            ),
            {"v": value},
        )
    return {"value": value}


@app.get("/api/signals")
async def signals(limit: int = Query(50, ge=1, le=500)) -> List[dict[str, Any]]:
    engine = get_engine()
    if engine is None:
        raise HTTPException(status_code=503, detail="DB not configured")
    sql = text(
        """
        SELECT ts, signal_type, direction, level, status, reason, details,
               entry_price, sl_initial, sl_active, target1, rr, trail_basis,
               hit_target1, exited, updated_at
        FROM signals
        ORDER BY ts DESC
        LIMIT :lim
        """
    )
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"lim": limit})
    if df.empty:
        return []
    return df.to_dict(orient="records")


@app.get("/api/price")
async def price() -> dict[str, Any]:
    engine = get_engine()
    if engine is None:
        raise HTTPException(status_code=503, detail="DB not configured")

    def _fmt_ts(ts):
        if ts is None:
            return None
        if hasattr(ts, "isoformat"):
            return ts.isoformat()
        return str(ts)

    # prefer live tick, fall back to latest OHLC close
    with engine.connect() as conn:
        tick = conn.execute(
            text("SELECT ts, price, volume FROM ticks ORDER BY ts DESC LIMIT 1")
        ).mappings().first()
        if tick:
            return {
                "price": float(tick["price"]),
                "ts": _fmt_ts(tick["ts"]),
                "volume": tick.get("volume"),
            }
        ohlc = conn.execute(
            text("SELECT ts, close FROM ohlc WHERE timeframe = '5m' ORDER BY ts DESC LIMIT 1")
        ).mappings().first()
        if ohlc:
            return {
                "price": float(ohlc["close"]),
                "ts": _fmt_ts(ohlc["ts"]),
                "volume": None,
            }
    return {"price": None, "ts": None, "volume": None}


@app.get("/api/crude")
async def crude() -> dict[str, Any]:
    engine = get_engine()
    if engine is None:
        raise HTTPException(status_code=503, detail="DB not configured")

    def _fmt_ts(ts):
        if ts is None:
            return None
        if hasattr(ts, "isoformat"):
            return ts.isoformat()
        return str(ts)

    # prefer live crude tick, fall back to latest crude OHLC close
    with engine.connect() as conn:
        tick = conn.execute(
            text("SELECT ts, price, volume FROM crude_ticks ORDER BY ts DESC LIMIT 1")
        ).mappings().first()
        if tick:
            return {
                "price": float(tick["price"]),
                "ts": _fmt_ts(tick["ts"]),
                "volume": tick.get("volume"),
            }
        ohlc = conn.execute(
            text("SELECT ts, close FROM crude_ohlc WHERE timeframe = '5m' ORDER BY ts DESC LIMIT 1")
        ).mappings().first()
        if ohlc:
            return {
                "price": float(ohlc["close"]),
                "ts": _fmt_ts(ohlc["ts"]),
                "volume": None,
            }
    return {"price": None, "ts": None, "volume": None}


@app.get("/api/health")
async def health() -> dict[str, str]:
    engine = get_engine()
    if engine is None:
        return {"status": "error", "detail": "DB not configured"}
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        logger.error("healthcheck failed: %s", exc)
        return {"status": "error", "detail": str(exc)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("web:app", host="0.0.0.0", port=8000, reload=True)
