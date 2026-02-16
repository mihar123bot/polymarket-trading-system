"""
SQLite storage layer (MVP).

- Uses sqlite3 only (no ORM).
- Idempotent schema initialization via storage/schema.sql.
- Upsert patterns for markets and trades.
- Simple insert for snapshots and watchlist events.
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Any, Dict


def now_ts() -> int:
    return int(time.time())


class DB:
    def __init__(self, path: str = "storage/polymarket.db", schema_path: str = "storage/schema.sql"):
        self.path = path
        self.schema_path = schema_path
        db_dir = os.path.dirname(self.path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    def init_schema(self) -> None:
        with open(self.schema_path, "r", encoding="utf-8") as f:
            sql = f.read()
        with self.conn:
            self.conn.executescript(sql)

    def _execute_retry(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        last_err = None
        for _ in range(5):
            try:
                return self.conn.execute(sql, params)
            except sqlite3.OperationalError as e:
                last_err = e
                if "locked" in str(e).lower():
                    time.sleep(0.05)
                    continue
                raise
        raise last_err  # type: ignore[misc]

    def upsert_market(self, market_id: str, title: str, category: str, source: str, slug: str = "") -> None:
        ts = now_ts()
        with self.conn:
            self._execute_retry(
                """
                INSERT INTO markets (market_id, slug, title, category, source, created_at_ts, updated_at_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market_id) DO UPDATE SET
                  slug=excluded.slug,
                  title=excluded.title,
                  category=excluded.category,
                  source=excluded.source,
                  updated_at_ts=excluded.updated_at_ts
                """,
                (str(market_id), str(slug), str(title), str(category), str(source), ts, ts),
            )

    def record_watchlist_event(self, market_id: str, action: str, reason: str) -> None:
        with self.conn:
            self._execute_retry(
                "INSERT INTO watchlist_events (ts, market_id, action, reason) VALUES (?, ?, ?, ?)",
                (now_ts(), str(market_id), str(action), str(reason)),
            )

    def record_trade(self, **kwargs: Any) -> None:
        trade_id = kwargs.get("trade_id")
        if not trade_id:
            raise ValueError("record_trade requires trade_id")

        cur = self.conn.execute("SELECT trade_id FROM trades WHERE trade_id = ?", (str(trade_id),))
        exists = cur.fetchone() is not None

        if not exists:
            required = ["ts_entry", "mode", "strategy", "market_id", "side", "qty", "price_entry"]
            missing = [k for k in required if k not in kwargs]
            if missing:
                raise ValueError(f"record_trade insert missing fields: {missing}")

            cols = list(kwargs.keys())
            vals = [kwargs[k] for k in cols]
            placeholders = ",".join(["?"] * len(cols))
            sql = f"INSERT INTO trades ({','.join(cols)}) VALUES ({placeholders})"
            with self.conn:
                self._execute_retry(sql, tuple(vals))
            return

        set_cols = []
        vals = []
        for k, v in kwargs.items():
            if k == "trade_id":
                continue
            set_cols.append(f"{k} = ?")
            vals.append(v)

        if not set_cols:
            return

        sql = f"UPDATE trades SET {', '.join(set_cols)} WHERE trade_id = ?"
        vals.append(str(trade_id))
        with self.conn:
            self._execute_retry(sql, tuple(vals))

    def record_trade_snapshot(self, **kwargs: Any) -> None:
        required = ["trade_id", "ts", "phase"]
        missing = [k for k in required if k not in kwargs]
        if missing:
            raise ValueError(f"record_trade_snapshot missing fields: {missing}")

        cols = list(kwargs.keys())
        vals = [kwargs[k] for k in cols]
        placeholders = ",".join(["?"] * len(cols))
        sql = f"INSERT INTO trade_snapshots ({','.join(cols)}) VALUES ({placeholders})"
        with self.conn:
            self._execute_retry(sql, tuple(vals))

    def record_whale_touch(self, **kwargs: Any) -> None:
        cols = list(kwargs.keys())
        vals = [kwargs[k] for k in cols]
        placeholders = ",".join(["?"] * len(cols))
        sql = f"INSERT INTO whale_touches ({','.join(cols)}) VALUES ({placeholders})"
        with self.conn:
            self._execute_retry(sql, tuple(vals))

    def get_today_trade_count(self, mode: str) -> int:
        cur = self.conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM trades
            WHERE mode = ?
              AND date(ts_entry, 'unixepoch') = date('now')
            """,
            (str(mode),),
        )
        row = cur.fetchone()
        return int(row["c"]) if row else 0

    def get_exit_failure_count_today(self, mode: str) -> int:
        cur = self.conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM trades
            WHERE mode = ?
              AND exit_reason = 'exit_failed'
              AND date(ts_entry, 'unixepoch') = date('now')
            """,
            (str(mode),),
        )
        row = cur.fetchone()
        return int(row["c"]) if row else 0

    def compute_daily_rollup(self, day: str, mode: str) -> Dict[str, Any]:
        cur = self.conn.execute(
            """
            SELECT
              COUNT(*) AS trades,
              AVG(CASE WHEN pnl > 0 THEN 1.0 ELSE 0.0 END) AS win_rate,
              AVG(COALESCE(pnl, 0.0)) AS avg_pnl,
              SUM(CASE WHEN exit_reason = 'exit_failed' THEN 1 ELSE 0 END) AS exit_failures
            FROM trades
            WHERE mode = ?
              AND date(ts_entry, 'unixepoch') = ?
            """,
            (str(mode), str(day)),
        )
        row = cur.fetchone()
        return {
            "day": day,
            "mode": mode,
            "trades": int(row["trades"] or 0) if row else 0,
            "win_rate": float(row["win_rate"] or 0.0) if row else 0.0,
            "avg_pnl": float(row["avg_pnl"] or 0.0) if row else 0.0,
            "exit_failures": int(row["exit_failures"] or 0) if row else 0,
        }
class DB:
    def init_schema(self) -> None:
        raise NotImplementedError("Implement DB.init_schema in storage/db.py")

    def upsert_market(self, market_id: str, title: str, category: str, source: str, slug: str = "") -> None:
        raise NotImplementedError("Implement DB.upsert_market in storage/db.py")

    def record_watchlist_event(self, market_id: str, action: str, reason: str) -> None:
        raise NotImplementedError("Implement DB.record_watchlist_event in storage/db.py")

    def record_trade(self, **kwargs) -> None:
        raise NotImplementedError("Implement DB.record_trade in storage/db.py")

    def record_trade_snapshot(self, **kwargs) -> None:
        raise NotImplementedError("Implement DB.record_trade_snapshot in storage/db.py")

    def get_today_trade_count(self, mode: str) -> int:
        raise NotImplementedError("Implement DB.get_today_trade_count in storage/db.py")
