import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import pandas as pd
from sqlalchemy import JSON, Column, DateTime, Float, Integer, MetaData, String, Table, create_engine, text

from config import load_config

logger = logging.getLogger(__name__)


metadata = MetaData()

signals_table = Table(
    "signals",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime, index=True, nullable=False),
    Column("signal_type", String, nullable=False),
    Column("direction", String, nullable=False),
    Column("level", Float, nullable=False),
    Column("status", String, nullable=False, default="open"),
    Column("reason", String, nullable=True),
    Column("entry_price", Float, nullable=True),
    Column("sl_initial", Float, nullable=True),
    Column("sl_active", Float, nullable=True),
    Column("target1", Float, nullable=True),
    Column("rr", Float, nullable=True),
    Column("trail_basis", String, nullable=True),
    Column("hit_target1", Integer, nullable=False, default=0),
    Column("exited", Integer, nullable=False, default=0),
    Column("updated_at", DateTime, nullable=True),
    Column("details", JSON, nullable=True),
)

ticks_table = Table(
    "ticks",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime, index=True, nullable=False),
    Column("price", Float, nullable=False),
    Column("volume", Float, nullable=True),
)

ohlc_table = Table(
    "ohlc",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime, index=True, nullable=False),
    Column("timeframe", String, index=True, nullable=False),
    Column("open", Float, nullable=False),
    Column("high", Float, nullable=False),
    Column("low", Float, nullable=False),
    Column("close", Float, nullable=False),
    Column("volume", Float, nullable=True),
)

settings_table = Table(
    "settings",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", String, nullable=False),
)


@dataclass
class SignalRecord:
    ts: datetime
    signal_type: str
    direction: str
    level: float
    status: str
    reason: str
    details: dict
    entry_price: Optional[float] = None
    sl_initial: Optional[float] = None
    sl_active: Optional[float] = None
    target1: Optional[float] = None
    rr: Optional[float] = None
    trail_basis: Optional[str] = None
    hit_target1: int = 0
    exited: int = 0
    updated_at: Optional[datetime] = None


class Storage:
    def __init__(self):
        self.cfg = load_config()
        self.engine = create_engine(f"sqlite:///{self.cfg.sqlite_path}", future=True)
        metadata.create_all(self.engine)
        self._ensure_signal_columns()

    def _ensure_signal_columns(self):
        # SQLite auto-migration for newly added columns
        expected = {
            "status",
            "reason",
            "entry_price",
            "sl_initial",
            "sl_active",
            "target1",
            "rr",
            "trail_basis",
            "hit_target1",
            "exited",
            "updated_at",
        }
        with self.engine.begin() as conn:
            res = conn.execute(text("PRAGMA table_info(signals)"))
            cols = {row[1] for row in res}
            missing = expected - cols
            for col in missing:
                if col == "status":
                    conn.execute(text("ALTER TABLE signals ADD COLUMN status TEXT DEFAULT 'open'"))
                elif col == "reason":
                    conn.execute(text("ALTER TABLE signals ADD COLUMN reason TEXT"))
                elif col == "entry_price":
                    conn.execute(text("ALTER TABLE signals ADD COLUMN entry_price REAL"))
                elif col == "sl_initial":
                    conn.execute(text("ALTER TABLE signals ADD COLUMN sl_initial REAL"))
                elif col == "sl_active":
                    conn.execute(text("ALTER TABLE signals ADD COLUMN sl_active REAL"))
                elif col == "target1":
                    conn.execute(text("ALTER TABLE signals ADD COLUMN target1 REAL"))
                elif col == "rr":
                    conn.execute(text("ALTER TABLE signals ADD COLUMN rr REAL"))
                elif col == "trail_basis":
                    conn.execute(text("ALTER TABLE signals ADD COLUMN trail_basis TEXT"))
                elif col == "hit_target1":
                    conn.execute(text("ALTER TABLE signals ADD COLUMN hit_target1 INTEGER DEFAULT 0"))
                elif col == "exited":
                    conn.execute(text("ALTER TABLE signals ADD COLUMN exited INTEGER DEFAULT 0"))
                elif col == "updated_at":
                    conn.execute(text("ALTER TABLE signals ADD COLUMN updated_at DATETIME"))

    def insert_signal(self, rec: SignalRecord):
        sql = signals_table.insert()
        payload = {
            "ts": rec.ts,
            "signal_type": rec.signal_type,
            "direction": rec.direction,
            "level": rec.level,
            "status": rec.status,
            "reason": rec.reason,
            "entry_price": rec.entry_price,
            "sl_initial": rec.sl_initial,
            "sl_active": rec.sl_active,
            "target1": rec.target1,
            "rr": rec.rr,
            "trail_basis": rec.trail_basis,
            "hit_target1": rec.hit_target1,
            "exited": rec.exited,
            "updated_at": rec.updated_at,
            "details": rec.details,
        }
        with self.engine.begin() as conn:
            result = conn.execute(sql, payload)
            return result.inserted_primary_key[0] if result.inserted_primary_key else None

    def insert_tick(self, ts: datetime, price: float, volume: Optional[float]):
        sql = ticks_table.insert()
        with self.engine.begin() as conn:
            conn.execute(sql, {"ts": ts, "price": price, "volume": volume})

    def update_signal_status(self, signal_id: int, status: str, reason: str = ""):
        sql = text("UPDATE signals SET status = :status, reason = :reason WHERE id = :id")
        with self.engine.begin() as conn:
            conn.execute(sql, {"status": status, "reason": reason, "id": signal_id})

    def update_signal_fields(self, signal_id: int, fields: dict[str, Any]):
        if not fields:
            return
        # ensure booleans stored as ints for SQLite
        normalized = {
            k: (1 if v is True else 0 if v is False else v)
            for k, v in fields.items()
        }
        sets = ", ".join([f"{k} = :{k}" for k in normalized.keys()])
        sql = text(f"UPDATE signals SET {sets} WHERE id = :id")
        normalized["id"] = signal_id
        with self.engine.begin() as conn:
            conn.execute(sql, normalized)

    def fetch_open_signals(self) -> list[dict[str, Any]]:
        sql = text(
            """
            SELECT id, ts, signal_type, direction, level, status, reason, details,
                   entry_price, sl_initial, sl_active, target1, rr, trail_basis,
                   hit_target1, exited, updated_at
            FROM signals
            WHERE COALESCE(exited, 0) = 0 AND (status IS NULL OR status != 'closed')
            ORDER BY ts DESC
            """
        )
        with self.engine.connect() as conn:
            res = conn.execute(sql)
            rows = res.mappings().all()
        return [dict(row) for row in rows]

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        sql = text("SELECT value FROM settings WHERE key = :key LIMIT 1")
        with self.engine.connect() as conn:
            res = conn.execute(sql, {"key": key}).scalar()
        return res if res is not None else default

    def set_setting(self, key: str, value: str):
        sql = text(
            """
            INSERT INTO settings (key, value) VALUES (:key, :value)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """
        )
        with self.engine.begin() as conn:
            conn.execute(sql, {"key": key, "value": value})

    def insert_ohlc(self, timeframe: str, df: pd.DataFrame):
        if df.empty:
            return
        records = df.reset_index()
        sql = ohlc_table.insert()
        with self.engine.begin() as conn:
            for _, row in records.iterrows():
                conn.execute(
                    sql,
                    {
                        "ts": row["ts"],
                        "timeframe": timeframe,
                        "open": row["open"],
                        "high": row["high"],
                        "low": row["low"],
                        "close": row["close"],
                        "volume": row.get("volume"),
                    },
                )

    def healthcheck(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("db healthcheck failed: %s", exc)
            return False
