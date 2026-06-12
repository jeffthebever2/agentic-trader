"""SQLite OHLCV cache — LOG-1.

Stores fetched bars in a local SQLite database to avoid redundant network
calls across training runs. Thread-safe via connection-per-call pattern.

Schema: ohlcv(ticker TEXT, date TEXT, open REAL, high REAL, low REAL,
              close REAL, volume INTEGER, PRIMARY KEY (ticker, date))
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List, Optional

from .provider import OHLCVBar


class OHLCVCache:
    """Read/write OHLCV bars to a local SQLite file.

    Parameters
    ----------
    db_path : str or Path
        Path to SQLite file. Created if it doesn't exist.
    """

    def __init__(self, db_path: str = "ohlcv_cache.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ohlcv (
                    ticker  TEXT NOT NULL,
                    date    TEXT NOT NULL,
                    open    REAL,
                    high    REAL,
                    low     REAL,
                    close   REAL,
                    volume  INTEGER,
                    PRIMARY KEY (ticker, date)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker_date ON ohlcv(ticker, date)")

    def get(self, ticker: str, start: str, end: str) -> List[OHLCVBar]:
        """Return cached bars for ticker in [start, end]. Empty list if none cached."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT date, open, high, low, close, volume FROM ohlcv "
                "WHERE ticker=? AND date>=? AND date<=? ORDER BY date",
                (ticker, start, end),
            ).fetchall()
        return [
            OHLCVBar(date=r[0], open=r[1], high=r[2], low=r[3],
                     close=r[4], volume=r[5], ticker=ticker)
            for r in rows
        ]

    def put(self, bars: List[OHLCVBar]) -> int:
        """Insert or replace bars. Returns count written."""
        if not bars:
            return 0
        rows = [
            (b.ticker, b.date, b.open, b.high, b.low, b.close, b.volume)
            for b in bars
        ]
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO ohlcv(ticker,date,open,high,low,close,volume) "
                "VALUES(?,?,?,?,?,?,?)",
                rows,
            )
        return len(rows)

    def has_range(self, ticker: str, start: str, end: str) -> bool:
        """Return True if cache contains at least one bar in range for ticker."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM ohlcv WHERE ticker=? AND date>=? AND date<=? LIMIT 1",
                (ticker, start, end),
            ).fetchone()
        return row is not None

    def coverage_dates(self, ticker: str) -> tuple[Optional[str], Optional[str]]:
        """Return (min_date, max_date) for ticker, or (None, None) if not cached."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MIN(date), MAX(date) FROM ohlcv WHERE ticker=?",
                (ticker,),
            ).fetchone()
        return (row[0], row[1]) if row else (None, None)
