"""
Arena-native PolymarketScan trading loop (one-man shop, low resources).

Key design (validated from your curl payloads):
- Arena place_order(BUY) uses amount = USD cost and returns shares + price; fills immediately.
- Arena place_order(SELL) uses amount = shares to sell.
- my_portfolio is the source of truth for positions + current_price + unrealized_pnl + recent_orders (includes UUID).
- Polyscan "markets" feed is used for watchlist discovery (created_at desc), and for selecting trade targets.
- Binance WS drives "latency" triggers (world-price moves).
- No Polymarket CLOB calls required.

Repo layout expected:
  collectors/
    binance_collector.py
    polyscan_collector.py
  trading/
    arena_trader.py
  storage/
    db.py
  config/config.yaml

This file is intentionally MVP-lean.
"""

from __future__ import annotations

import asyncio
import json
import signal
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import yaml

from collectors.binance_collector import BinanceCollector
from collectors.polymarket_collector import PolymarketCLOB
from collectors.polyscan_collector import PolyscanClient
from storage.db import DB
from trading.arena_trader import ArenaTrader


# -----------------------------
# Utils
# -----------------------------

def now_ts() -> int:
    return int(time.time())


def load_config(path: str = "config/config.yaml") -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_iso_to_ts(iso_str: str) -> int:
    """
    Minimal ISO8601 -> epoch seconds.
    Avoid heavy deps; handles '2026-02-17T09:25:00+00:00'.
    """
    import datetime as dt

    return int(dt.datetime.fromisoformat(iso_str).timestamp())


# -----------------------------
# Data models
# -----------------------------

@dataclass
class MarketRow:
    market_id: str
    slug: str
    title: str
    category: str
    yes_price: float
    no_price: float
    liquidity_usd: float
    volume_usd: float
    closes_at_iso: str
    closes_at_ts: int
    is_resolved: bool


@dataclass
class EntryDecision:
    market: MarketRow
    side: str
    usd: float
    fair_value: Optional[float]
    reason: str
    meta: Dict[str, Any]


# -----------------------------
# Core helpers
# -----------------------------

class EventDetector:
    """Simple rolling window detector for bps move in window_seconds."""

    def __init__(self, window_seconds: int):
        self.window_seconds = window_seconds
        self._window: List[Tuple[int, float]] = []

    def update(self, ts: int, price: float) -> float:
        self._window.append((ts, price))
        cutoff = ts - self.window_seconds
        while self._window and self._window[0][0] < cutoff:
            self._window.pop(0)
        if len(self._window) < 2:
            return 0.0
        oldest = self._window[0][1]
        newest = self._window[-1][1]
        if oldest <= 0:
            return 0.0
        return ((newest - oldest) / oldest) * 10000.0


def choose_side_for_updown(move_bps: float) -> str:
    """
    For 'Up or Down' style markets:
      - If Binance moved up in window: buy YES
      - If moved down: buy NO
    """
    return "YES" if move_bps > 0 else "NO"


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# -----------------------------
# App
# -----------------------------

class App:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self.mode = cfg["env"]["mode"]

        # DB
        self.db = DB(path=cfg["storage"]["db_path"], schema_path=cfg["storage"]["schema_path"])

        self.binance = BinanceCollector(**cfg["binance"])
        self.polymarket = PolymarketCLOB()
        self.polyscan = PolyscanClient(
            base_url=cfg["polyscan"]["base_url"],
            agent_id=cfg["polyscan"]["agent_id"],
            timeout_s=cfg["polyscan"]["timeout_seconds"],
            cache_ttl_markets_s=cfg["polyscan"]["cache_ttl_markets_seconds"],
            cache_ttl_market_detail_s=cfg["polyscan"]["cache_ttl_market_detail_seconds"],
        )
        self.arena = ArenaTrader(
            base_url=cfg["polyscan"]["base_url"],
            agent_id=cfg["polyscan"]["agent_id"],
            timeout_s=cfg["polyscan"]["timeout_seconds"],
        )
        self.binance = BinanceCollector()
        self.polyscan = PolyscanClient()
        self.arena = ArenaTrader()
        self.db = DB()

        self.detector = EventDetector(window_seconds=int(cfg["signals"]["latency"]["window_seconds"]))
        self._shutdown = False

        self.watchlist: Dict[str, MarketRow] = {}
        self._last_watchlist_refresh = 0

        self.open_trade_ids_by_market: Dict[str, str] = {}
        self._last_portfolio_poll = 0
        self._cached_portfolio: Optional[Dict[str, Any]] = None

    async def init(self) -> None:
        self.db.init_schema()
        await self.refresh_watchlist(force=True)

    def request_shutdown(self) -> None:
        self._shutdown = True

    async def refresh_watchlist(self, force: bool = False) -> None:
        cfg = self.cfg
        refresh_seconds = int(cfg["watchlist"]["market_search"]["refresh_seconds"])
        if not force and (now_ts() - self._last_watchlist_refresh) < refresh_seconds:
            return
        self._last_watchlist_refresh = now_ts()

        cat = "Crypto"
        limit = int(cfg["budget"]["max_watchlist_markets"]) * 3
        elig = cfg["watchlist"]["eligibility"]

        try:
            category = self.cfg["watchlist"]["categories"][0] if self.cfg["watchlist"]["categories"] else "crypto"
            candidates = await self.polyscan.list_markets(category=category, limit=100)
        except Exception as e:
            print(f"[WATCHLIST] polyscan.search failed: {e}")
            return
        raw = await self.polyscan.list_markets(category=cat, limit=limit, sort="created_at", order="desc")

        rows: List[MarketRow] = []
        now_utc = now_ts()
        min_seconds_to_close = int(elig.get("min_seconds_to_close", 120))

        for item in raw:
            try:
                closes_at_iso = item.get("closes_at")
                if not closes_at_iso:
                    continue
                closes_at_ts = parse_iso_to_ts(closes_at_iso)
                if closes_at_ts <= (now_utc + min_seconds_to_close):
                    continue
                if bool(item.get("is_resolved", False)):
                    continue

                liquidity = float(item.get("liquidity_usd", 0.0) or 0.0)
                if liquidity < float(elig["min_liquidity_usd"]):
                    continue

                title = str(item.get("title", ""))
                if "Up or Down" not in title and "updown" not in str(item.get("slug", "")):
                    continue

                row = MarketRow(
                    market_id=str(item["market_id"]),
                    slug=str(item.get("slug", "")),
                    title=title,
                    category=str(item.get("category", "Crypto")),
                    yes_price=float(item.get("yes_price", 0.5) or 0.5),
                    no_price=float(item.get("no_price", 0.5) or 0.5),
                    liquidity_usd=liquidity,
                    volume_usd=float(item.get("volume_usd", 0.0) or 0.0),
                    closes_at_iso=closes_at_iso,
                    closes_at_ts=closes_at_ts,
                    is_resolved=bool(item.get("is_resolved", False)),
                )
                rows.append(row)
            except Exception:
                continue

        rows = sorted(rows, key=lambda r: r.closes_at_ts)
        max_wl = int(cfg["budget"]["max_watchlist_markets"])
        rows = rows[:max_wl]

        new_watchlist: Dict[str, MarketRow] = {r.market_id: r for r in rows}

        old_ids = set(self.watchlist.keys())
        new_ids = set(new_watchlist.keys())
        for mid in new_ids - old_ids:
            self.db.record_watchlist_event(mid, "ADD", "eligibility_passed")
        for mid in old_ids - new_ids:
            self.db.record_watchlist_event(mid, "REMOVE", "refresh_rotation")

        for r in rows:
            self.db.upsert_market(r.market_id, r.title, r.category, source="polyscan", slug=r.slug)

        self.watchlist = new_watchlist
        print(f"[WATCHLIST] {len(self.watchlist)} markets (filtered)")

    async def get_portfolio_cached(self, ttl_seconds: int = 2) -> Dict[str, Any]:
        now = now_ts()
        if self._cached_portfolio is None or (now - self._last_portfolio_poll) >= ttl_seconds:
            self._cached_portfolio = await self.arena.my_portfolio()
            self._last_portfolio_poll = now
        return self._cached_portfolio

    def has_open_position_in_market(self, portfolio: Dict[str, Any], market_id: str) -> bool:
        positions = portfolio.get("positions", []) or []
        for p in positions:
            if str(p.get("market_id")) == str(market_id) and float(p.get("shares", 0.0) or 0.0) > 0:
                return True
        return False

    async def decide_entry(self, move_bps: float, binance_price: float) -> Optional[EntryDecision]:
        cfg = self.cfg
        latency = cfg["signals"]["latency"]
        exec_cfg = cfg["execution"]
        budget = cfg["budget"]

        if abs(move_bps) < float(latency["trigger_bps"]):
            return None

        if self.db.get_today_trade_count(self.mode) >= int(budget["max_trades_per_day"]):
            return None

        portfolio = await self.get_portfolio_cached(ttl_seconds=2)

        candidates = list(self.watchlist.values())
        candidates = [m for m in candidates if "Bitcoin Up or Down" in m.title] or list(self.watchlist.values())
        candidates = [m for m in candidates if not self.has_open_position_in_market(portfolio, m.market_id)]

        if not candidates:
            return None

        target = candidates[0]

        side = choose_side_for_updown(move_bps)

        usd = float(exec_cfg.get("default_buy_usd", 1.0))
        usd = clamp(usd, 1.0, float(exec_cfg.get("max_buy_usd", 25.0)))

        fv = 0.5 + clamp(move_bps / 2000.0, -0.1, 0.1)
        fv = float(clamp(fv, 0.01, 0.99))

        return EntryDecision(
            market=target,
            side=side,
            usd=usd,
            fair_value=fv,
            reason="latency_trigger",
            meta={
                "move_bps": move_bps,
                "binance_price": binance_price,
                "market_yes_price": target.yes_price,
                "market_no_price": target.no_price,
                "closes_at": target.closes_at_iso,
            },
        )

    async def execute_entry(self, d: EntryDecision) -> Optional[str]:
        trade_id = str(uuid.uuid4())
        ts_entry = now_ts()

        resp = await self.arena.place_order(
            market_id=d.market.market_id,
            side=d.side,
            amount=d.usd,
            action="BUY",
            fair_value=d.fair_value,
        )
        shares = float(resp["shares"])
        price = float(resp["price"])
        cost = float(resp["cost"])

        portfolio = await self.get_portfolio_cached(ttl_seconds=0)
        recent_orders = portfolio.get("recent_orders", []) or []
        arena_order_id = None
        filled_at = None
        if recent_orders:
            newest = sorted(recent_orders, key=lambda x: x.get("created_at", ""), reverse=True)[0]
            arena_order_id = newest.get("id")
            filled_at = newest.get("filled_at") or newest.get("created_at")

        self.db.record_trade(
            trade_id=trade_id,
            ts_entry=ts_entry,
            mode=self.mode,
            strategy="latency",
            market_id=d.market.market_id,
            side=d.side,
            qty=shares,
            price_entry=price,
            notes=json.dumps({"reason": d.reason, "meta": d.meta}),
            arena_order_id=arena_order_id,
            filled_at=filled_at,
            fees_estimated=0.0,
        )

        self.db.record_trade_snapshot(
            trade_id=trade_id,
            ts=ts_entry,
            phase="ENTRY",
            binance_price=d.meta.get("binance_price"),
            yes_price=d.market.yes_price,
            no_price=d.market.no_price,
            liquidity_usd=d.market.liquidity_usd,
            edge_est=(d.fair_value - (d.market.yes_price if d.side == "YES" else d.market.no_price)) if d.fair_value else None,
            unrealized_pnl=0.0,
            portfolio_value=portfolio.get("portfolio_value"),
        )

        self.open_trade_ids_by_market[d.market.market_id] = trade_id

        print(
            f"[ENTRY] trade_id={trade_id} arena_order_id={arena_order_id} "
            f"market={d.market.market_id} side={d.side} cost=${cost:.2f} shares={shares:.4f} px={price:.4f}"
        )
        return trade_id

    async def maybe_exit_positions(self) -> None:
        cfg = self.cfg
        exec_cfg = cfg["execution"]

        portfolio = await self.get_portfolio_cached(ttl_seconds=0)
        positions = portfolio.get("positions", []) or []

        take_profit_usd = float(exec_cfg.get("take_profit_usd", 0.05))
        stop_loss_usd = float(exec_cfg.get("stop_loss_usd", 0.05))
        max_hold_seconds = int(exec_cfg.get("max_hold_seconds", 120))

        now = now_ts()

        for p in positions:
            market_id = str(p.get("market_id"))
            side = str(p.get("side"))
            shares = float(p.get("shares", 0.0) or 0.0)
            if shares <= 0:
                continue

            trade_id = self.open_trade_ids_by_market.get(market_id)
            if not trade_id:
                continue

            unreal = float(p.get("unrealized_pnl", 0.0) or 0.0)
            current_price = float(p.get("current_price", 0.0) or 0.0)
            avg_price = float(p.get("avg_price", 0.0) or 0.0)

            ts_entry = self._entry_ts_by_trade.get(trade_id)
            age = (now - ts_entry) if ts_entry else 0

            exit_reason = None
            if unreal >= take_profit_usd:
                exit_reason = "target"
            elif unreal <= -stop_loss_usd:
                exit_reason = "stop"
            elif ts_entry and age >= max_hold_seconds:
                exit_reason = "timeout"

            if not exit_reason:
                continue

            sell_resp = await self.arena.place_order(
                market_id=market_id,
                side=side,
                amount=shares,
                action="SELL",
                fair_value=None,
            )

        try:
            whales = await self.polyscan.whales(limit=200)
        except Exception as e:
            print(f"[WHALES] fetch failed: {e}")
            return candidates

        whales = [w for w in whales if int(w.get("ts") or 0) >= since]

        # Index whales by market for quick lookup
        whales_by_market: Dict[str, List[Dict[str, Any]]] = {}
        for w in whales:
            m = w.get("market_id")
            if not m:
                continue
            whales_by_market.setdefault(m, []).append(w)
            portfolio2 = await self.get_portfolio_cached(ttl_seconds=0)
            portfolio_value = portfolio2.get("portfolio_value")

            sell_price = float(sell_resp.get("price", current_price) or current_price)
            realized = (sell_price - avg_price) * shares

            arena_order_id = None
            filled_at = None
            ro2 = portfolio2.get("recent_orders", []) or []
            if ro2:
                newest2 = sorted(ro2, key=lambda x: x.get("created_at", ""), reverse=True)[0]
                arena_order_id = newest2.get("id")
                filled_at = newest2.get("filled_at") or newest2.get("created_at")

            self.db.record_trade(
                trade_id=trade_id,
                ts_exit=now_ts(),
                price_exit=sell_price,
                pnl=realized,
                outcome="WIN" if realized > 0 else ("LOSS" if realized < 0 else "FLAT"),
                exit_reason=exit_reason,
                arena_exit_order_id=arena_order_id,
                exit_filled_at=filled_at,
            )

            self.db.record_trade_snapshot(
                trade_id=trade_id,
                ts=now_ts(),
                phase="EXIT",
                binance_price=None,
                yes_price=None,
                no_price=None,
                liquidity_usd=None,
                edge_est=None,
                unrealized_pnl=0.0,
                portfolio_value=portfolio_value,
                current_price=sell_price,
                avg_price=avg_price,
                market_value=0.0,
            )

            self.open_trade_ids_by_market.pop(market_id, None)
            self._entry_ts_by_trade.pop(trade_id, None)

            print(
                f"[EXIT] trade_id={trade_id} reason={exit_reason} "
                f"market={market_id} side={side} shares={shares:.4f} sell_px={sell_price:.4f} realized={realized:.4f}"
            )

    async def run(self) -> None:
        if self.mode != "arena":
            print(f"[WARN] env.mode is '{self.mode}'. This main.py is Arena-native; set env.mode: arena")

        self._entry_ts_by_trade: Dict[str, int] = {}

        monitor_task = asyncio.create_task(self.monitor_loop())

        try:
            async for tick in self.binance.stream_ticks():
                if self._shutdown:
                    break

                ts = int(tick["ts"])
                price = float(tick["price"])
                move_bps = self.detector.update(ts, price)

                await self.refresh_watchlist(force=False)

                d = await self.decide_entry(move_bps=move_bps, binance_price=price)
                if not d:
                    continue

        # Record entry trade and snapshot
        self.db.record_trade(
            trade_id=trade_id,
            ts_entry=ts_entry,
            mode=self.mode,
            strategy=c.strategy,
            market_id=c.market_id,
            side=c.side,
            qty=c.qty,
            price_entry=c.price_ref,
            fees_estimated=c.cost_cents_est / 100.0,  # placeholder conversion
            notes=json.dumps(c.meta),
        )
        self.db.record_trade_snapshot(
            trade_id=trade_id,
            ts=ts_entry,
            phase="ENTRY",
            binance_price=c.meta.get("binance_price"),
            yes_price=c.price_ref,
            no_price=max(0.0, 1.0 - c.price_ref),
            liquidity_usd=c.liquidity_usd,
            edge_est=c.edge_cents_est,
            current_price=c.price_ref,
            avg_price=c.price_ref,
            market_value=c.qty * c.price_ref,
            extra=json.dumps({"spread_cents": c.spread_cents, "cost_cents_est": c.cost_cents_est, "stale_seconds": c.stale_seconds}),
        )
                trade_id = await self.execute_entry(d)
                if trade_id:
                    self._entry_ts_by_trade[trade_id] = now_ts()

        except asyncio.CancelledError:
            pass
        finally:
            self._shutdown = True
            monitor_task.cancel()

    async def monitor_loop(self) -> None:
        poll_seconds = float(self.cfg.get("monitoring", {}).get("poll_seconds", 2.0))
        while not self._shutdown:
            try:
                await asyncio.sleep(poll_seconds)
                await self.maybe_exit_positions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[MONITOR] error: {e}")

    async def exit_position(self, pos: Position, exit_reason: str) -> bool:
        ts_exit = now_ts()
        mid = await self.polymarket.get_midpoint(pos.market_id)
        if mid is None:
            # count as exit failure
            self.kill.trigger_pause("Exit failed: no midpoint")
            return False

        # Simulated exit at midpoint
        pnl = (mid - pos.entry_price) * pos.qty if pos.side == "YES" else (pos.entry_price - mid) * pos.qty

        self.db.record_trade(
            trade_id=pos.trade_id,
            ts_exit=ts_exit,
            price_exit=mid,
            pnl=pnl,
            outcome="WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "FLAT"),
            exit_reason=exit_reason,
        )
        self.db.record_trade_snapshot(
            trade_id=pos.trade_id,
            ts=ts_exit,
            phase="EXIT",
            binance_price=None,
            yes_price=mid,
            no_price=max(0.0, 1.0 - mid),
            liquidity_usd=None,
            edge_est=None,
            unrealized_pnl=0.0,
            current_price=mid,
            avg_price=pos.entry_price,
            market_value=mid * pos.qty,
            extra=json.dumps({"exit_reason": exit_reason}),
        )
        print(f"[TRADE] Exited {pos.trade_id} reason={exit_reason} @ {mid:.2f} pnl={pnl:.4f}")
        return True

    def request_shutdown(self) -> None:
        self._shutdown = True


# -----------------------------
# Entrypoint
# -----------------------------

async def main_async() -> None:
    cfg = load_config("config/config.yaml")
    app = App(cfg)
    await app.init()

    loop = asyncio.get_running_loop()

    def _handle_sig(*_args):
        print("[SYSTEM] Shutdown requested...")
        app.request_shutdown()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, _handle_sig)
        except NotImplementedError:
            signal.signal(s, lambda *_: _handle_sig())

    await app.run()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
