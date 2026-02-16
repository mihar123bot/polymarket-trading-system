"""
Lean, one-man Polymarket arb loop (event-driven, low API calls).
- Binance WS drives opportunity scans
- Polymarket CLOB read-only used for quotes/books on a small watchlist
- PolymarketScan (Polyscan) is confirmatory + watchlist refresh, behind circuit breaker + rate limiter
- No LLM calls in the hot path

Repo layout assumed (from your plan):
  collectors/
    binance_collector.py
    polymarket_collector.py
    polyscan_collector.py
  storage/
    db.py
  core/
    event_detector.py
    lag_analyzer.py
    market_scanner.py
  risk/
    kill_switch.py
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
# Utilities
# -----------------------------

def now_ts() -> int:
    return int(time.time())


def load_config(path: str = "config/config.yaml") -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass
class TradeCandidate:
    market_id: str
    side: str                      # "YES"/"NO" (or mapped "UP"/"DOWN")
    qty: float
    price_ref: float               # reference midpoint at decision time
    score: float
    edge_cents_est: float
    cost_cents_est: float
    liquidity_usd: float
    spread_cents: float
    stale_seconds: float
    strategy: str                  # e.g. "latency" or "latency+whale"
    meta: Dict[str, Any]


@dataclass
class Position:
    trade_id: str
    market_id: str
    side: str
    qty: float
    entry_price: float
    ts_entry: int
    strategy: str
    target_edge_cents: float
    max_hold_seconds: int
    meta: Dict[str, Any]


class EventDetector:
    """core/event_detector.py wrapper (placeholder)."""

    def __init__(self, window_seconds: int):
        self.window_seconds = window_seconds
        self._window: List[Tuple[int, float]] = []

    def update(self, ts: int, price: float) -> float:
        """
        Updates price window and returns bps move from oldest to newest in window.
        """
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
        return ((newest - oldest) / oldest) * 10000.0  # bps


class KillSwitch:
    """risk/kill_switch.py wrapper (placeholder)."""

    def __init__(self, max_consecutive_exit_failures: int, max_daily_drawdown_pct: float, pause_minutes: int):
        self.max_consecutive_exit_failures = max_consecutive_exit_failures
        self.max_daily_drawdown_pct = max_daily_drawdown_pct
        self.pause_minutes = pause_minutes
        self.pause_until_ts: int = 0

    def is_paused(self) -> bool:
        return now_ts() < self.pause_until_ts

    def trigger_pause(self, reason: str) -> None:
        self.pause_until_ts = now_ts() + self.pause_minutes * 60
        print(f"[KILL_SWITCH] Pausing for {self.pause_minutes}m. Reason: {reason}")


# -----------------------------
# Core logic helpers
# -----------------------------

def estimate_costs_cents(
    price_mid: float,
    best_bid: float,
    best_ask: float,
    fee_rate: float,
    slippage_buffer_cents: float,
) -> Tuple[float, float]:
    """
    Returns (spread_cents, cost_cents_est).
    Price units assumed in "cents" probability pricing for YES shares (0-100).
    If you use 0-1 pricing, adjust conversions consistently.
    """
    spread = max(0.0, (best_ask - best_bid))
    # Conservative cost estimate: fees proportional to notional + half-spread + slippage buffer
    # For probability-style markets, notional depends on qty; we treat per-share cents for a minimal gate.
    # Codex: refine using actual Polymarket fee model once you have it per market.
    cost = (spread) + slippage_buffer_cents + (price_mid * fee_rate)
    return spread, cost


def choose_side_from_binance_move(move_bps: float) -> str:
    # Map direction: Binance up => YES on "Up" market; Binance down => YES on "Down" market
    # Here we just return "YES" vs "NO" placeholder. Codex should map to the market's actual outcomes.
    return "YES" if move_bps > 0 else "NO"


# -----------------------------
# Watchlist manager
# -----------------------------

class Watchlist:
    def __init__(self, max_size: int):
        self.max_size = max_size
        self.market_ids: List[str] = []
        self.meta: Dict[str, Dict[str, Any]] = {}  # market_id -> metadata (title/category/liquidity/spread cache)

    def set(self, market_rows: List[Dict[str, Any]]) -> None:
        self.market_ids = [m["market_id"] for m in market_rows][: self.max_size]
        self.meta = {m["market_id"]: m for m in market_rows if "market_id" in m}

    def __iter__(self):
        return iter(self.market_ids)

    def __len__(self):
        return len(self.market_ids)


# -----------------------------
# Main Orchestrator
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

        # State
        self.watchlist = Watchlist(max_size=cfg["budget"]["max_watchlist_markets"])
        self.detector = EventDetector(window_seconds=cfg["signals"]["latency"]["window_seconds"])
        self.kill = KillSwitch(
            max_consecutive_exit_failures=cfg["risk"]["kill_switch"]["max_consecutive_exit_failures"],
            max_daily_drawdown_pct=cfg["risk"]["kill_switch"]["max_daily_drawdown_pct"],
            pause_minutes=cfg["risk"]["kill_switch"]["pause_minutes_on_trigger"],
        )

        self.last_polymarket_mid: Dict[str, Tuple[float, int]] = {}  # market_id -> (mid, ts)
        self.open_positions: Dict[str, Position] = {}               # trade_id -> Position

        # Rate limiting / budgets
        self._shutdown = False
        self._last_watchlist_refresh_ts = 0

    async def init(self) -> None:
        self.db.init_schema()
        await self.refresh_watchlist(force=True)

    async def refresh_watchlist(self, force: bool = False) -> None:
        refresh_seconds = int(self.cfg["watchlist"]["market_search"]["refresh_seconds"])
        if not force and (now_ts() - self._last_watchlist_refresh_ts) < refresh_seconds:
            return
        self._last_watchlist_refresh_ts = now_ts()

        keywords = self.cfg["watchlist"]["market_search"]["keywords"]
        max_markets = self.cfg["budget"]["max_watchlist_markets"]
        elig = self.cfg["watchlist"]["eligibility"]

        try:
            category = self.cfg["watchlist"]["categories"][0] if self.cfg["watchlist"]["categories"] else "crypto"
            candidates = await self.polyscan.list_markets(category=category, limit=100)
        except Exception as e:
            print(f"[WATCHLIST] polyscan.search failed: {e}")
            return

        # Filter by microstructure (liquidity/spread) with minimal calls:
        # - Call market_detail only for top N candidates
        market_rows: List[Dict[str, Any]] = []
        for c in candidates[: max_markets * 2]:
            mid = c.get("market_id")
            if not mid:
                continue
            try:
                detail = await self.polyscan.market_detail(mid)
            except Exception:
                continue

            liquidity = float(detail.get("liquidity_usd", 0.0) or 0.0)
            spread_cents = float(detail.get("spread_cents", 9999.0) or 9999.0)

            if liquidity < float(elig["min_liquidity_usd"]):
                continue
            if spread_cents > float(elig["max_spread_cents"]):
                continue

            row = {
                "market_id": mid,
                "title": c.get("title", ""),
                "category": ",".join(self.cfg["watchlist"]["categories"]),
                "source": "polyscan",
                "liquidity_usd": liquidity,
                "spread_cents": spread_cents,
            }
            market_rows.append(row)

        market_rows = sorted(market_rows, key=lambda r: r.get("liquidity_usd", 0.0), reverse=True)[:max_markets]
        self.watchlist.set(market_rows)

        for r in market_rows:
            self.db.upsert_market(r["market_id"], r.get("title", ""), r.get("category", ""), r.get("source", "polyscan"))

        print(f"[WATCHLIST] refreshed: {len(self.watchlist)} markets")

    async def run(self) -> None:
        # Background task: monitor open positions
        monitor_task = asyncio.create_task(self.position_monitor_loop())

        # Main event loop: Binance tick stream
        try:
            async for tick in self.binance.stream_ticks():
                if self._shutdown:
                    break

                if self.kill.is_paused():
                    continue

                ts = int(tick["ts"])
                price = float(tick["price"])
                move_bps = self.detector.update(ts, price)

                # Periodic watchlist refresh (low cadence)
                await self.refresh_watchlist(force=False)

                # Trigger scan only when move is meaningful
                latency_cfg = self.cfg["signals"]["latency"]
                if abs(move_bps) >= float(latency_cfg["trigger_bps"]):
                    await self.opportunity_scan(binance_ts=ts, binance_price=price, move_bps=move_bps)

        except asyncio.CancelledError:
            pass
        finally:
            self._shutdown = True
            monitor_task.cancel()

    async def opportunity_scan(self, binance_ts: int, binance_price: float, move_bps: float) -> None:
        # Budget checks
        if self.db.get_today_trade_count(self.mode) >= int(self.cfg["budget"]["max_trades_per_day"]):
            return

        # Only scan watchlist markets (bounded)
        candidates: List[TradeCandidate] = []
        latency_cfg = self.cfg["signals"]["latency"]
        exec_cfg = self.cfg["execution"]
        elig = self.cfg["watchlist"]["eligibility"]

        side_hint = choose_side_from_binance_move(move_bps)

        # Optional whale confirmation: only fetch if we have at least one candidate
        whales_needed = bool(self.cfg["signals"]["whales"]["enabled"])

        for market_id in self.watchlist:
            # Read midpoint (cheap)
            mid = await self.polymarket.get_midpoint(market_id)
            if mid is None:
                continue

            last_mid, last_ts = self.last_polymarket_mid.get(market_id, (mid, binance_ts))
            self.last_polymarket_mid[market_id] = (mid, binance_ts)

            stale_seconds = max(0.0, (binance_ts - last_ts))
            if stale_seconds < float(latency_cfg["stale_price_seconds"]):
                continue

            # Book for spread + top-of-book liquidity proxy
            book = await self.polymarket.get_top_of_book(market_id)
            if not book:
                continue

            best_bid = float(book["best_bid"])
            best_ask = float(book["best_ask"])

            spread_cents, cost_cents = estimate_costs_cents(
                price_mid=mid,
                best_bid=best_bid,
                best_ask=best_ask,
                fee_rate=float(self.cfg["fees"]["fallback_taker_fee_rate"]),
                slippage_buffer_cents=float(exec_cfg["slippage_buffer_cents"]),
            )

            # Use cached liquidity from watchlist refresh; if missing, skip
            liq = float(self.watchlist.meta.get(market_id, {}).get("liquidity_usd", 0.0))
            if liq < float(elig["min_liquidity_usd"]):
                continue
            if spread_cents > float(elig["max_spread_cents"]):
                continue

            # Minimal edge estimate: convert move_bps into "edge cents"
            # Codex: replace with your actual probability mapping model.
            edge_cents = max(0.0, (abs(move_bps) / 10.0))  # heuristic: 20 bps => 2.0 "cents"
            edge_after_costs = edge_cents - cost_cents
            if edge_after_costs < float(exec_cfg["min_edge_after_costs_cents"]):
                continue

            score = float(latency_cfg["confidence_min"]) + min(1.0, abs(move_bps) / 100.0)

            candidate = TradeCandidate(
                market_id=market_id,
                side=side_hint,
                qty=self.compute_qty(liquidity_usd=liq),
                price_ref=mid,
                score=score,
                edge_cents_est=edge_cents,
                cost_cents_est=cost_cents,
                liquidity_usd=liq,
                spread_cents=spread_cents,
                stale_seconds=stale_seconds,
                strategy="latency",
                meta={"binance_ts": binance_ts, "binance_price": binance_price, "move_bps": move_bps},
            )
            candidates.append(candidate)

        if not candidates:
            return

        # Optional: whale confirmation (single polyscan call) then rescore
        if whales_needed:
            candidates = await self.apply_whale_confirmation(candidates)

        # Pick best candidate
        best = sorted(candidates, key=lambda c: c.score, reverse=True)[0]

        # Risk checks and execute
        if not self.risk_check(best):
            return
        await self.execute(best)

    def compute_qty(self, liquidity_usd: float) -> float:
        """
        Conservative position sizing:
        - cap at max_position_pct_of_liquidity * liquidity_usd
        - convert to qty in "shares" or contracts depending on your execution model
        """
        cap_pct = float(self.cfg["watchlist"]["eligibility"]["max_position_pct_of_liquidity"])
        notional_cap = max(0.0, liquidity_usd * cap_pct)
        # Placeholder: 1 qty == 1 USDC notional-ish. Codex should map properly.
        return max(1.0, min(notional_cap, 500.0))

    async def apply_whale_confirmation(self, candidates: List[TradeCandidate]) -> List[TradeCandidate]:
        whales_cfg = self.cfg["signals"]["whales"]
        since = now_ts() - int(whales_cfg["confirm_window_seconds"])

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

        out: List[TradeCandidate] = []
        for c in candidates:
            confirmations = whales_by_market.get(c.market_id, [])
            confirmed = False

            for w in confirmations:
                tier = (w.get("tier") or "").lower()
                amt = float(w.get("amount_usd", 0.0) or 0.0)
                side = (w.get("side") or "").upper()

                if tier not in [t.lower() for t in whales_cfg["tier_allowlist"]]:
                    continue
                if amt < float(whales_cfg["min_amount_usd"]):
                    continue
                # Direction alignment (placeholder: YES/NO)
                if side and side == c.side:
                    confirmed = True
                    self.db.record_whale_touch(
                        ts=now_ts(),
                        market_id=c.market_id,
                        wallet=w.get("wallet"),
                        side=side,
                        amount_usd=amt,
                        tier=tier,
                        anomaly_tags=json.dumps(w.get("anomaly_tags", [])),
                        used_as_confirmation=1,
                    )
                    break

            if confirmed:
                c.score += 0.25
                c.strategy = "latency+whale"
            out.append(c)

        return out

    def risk_check(self, c: TradeCandidate) -> bool:
        if self.kill.is_paused():
            return False

        # Exit-failure safeguard
        exit_failures = self.db.get_exit_failure_count_today(self.mode)
        if exit_failures >= int(self.cfg["risk"]["kill_switch"]["max_consecutive_exit_failures"]):
            self.kill.trigger_pause("Too many exit failures today")
            return False

        # Liquidity concentration check
        max_pct = float(self.cfg["watchlist"]["eligibility"]["max_position_pct_of_liquidity"])
        # qty mapping is placeholder; enforce notional cap broadly
        if c.qty > (c.liquidity_usd * max_pct):
            return False

        return True

    async def execute(self, c: TradeCandidate) -> None:
        trade_id = str(uuid.uuid4())
        ts_entry = now_ts()

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

        # Paper / Arena / Live execution is abstracted.
        # For now, we assume fill at reference midpoint.
        # Codex: wire to paper_trader or arena_trader implementations.
        position = Position(
            trade_id=trade_id,
            market_id=c.market_id,
            side=c.side,
            qty=c.qty,
            entry_price=c.price_ref,
            ts_entry=ts_entry,
            strategy=c.strategy,
            target_edge_cents=float(self.cfg["execution"]["min_edge_after_costs_cents"]),
            max_hold_seconds=300,  # 5 min default; tune per market
            meta=c.meta,
        )
        self.open_positions[trade_id] = position
        print(f"[TRADE] Entered {trade_id} {c.strategy} {c.market_id} side={c.side} qty={c.qty} @ {c.price_ref:.2f}")

    async def position_monitor_loop(self) -> None:
        """
        Monitor open positions only (cheap). No Polyscan calls here.
        """
        while not self._shutdown:
            try:
                await asyncio.sleep(2.0)
                if not self.open_positions:
                    continue

                to_close: List[str] = []
                for trade_id, pos in list(self.open_positions.items()):
                    # Timeout exit
                    age = now_ts() - pos.ts_entry
                    if age > pos.max_hold_seconds:
                        exit_reason = "timeout"
                        ok = await self.exit_position(pos, exit_reason)
                        if ok:
                            to_close.append(trade_id)
                        continue

                    # Check current mid/book for exit edge
                    mid = await self.polymarket.get_midpoint(pos.market_id)
                    if mid is None:
                        continue

                    # Simple profit target: mid moved favorably by target_edge_cents
                    favorable = (mid - pos.entry_price) if pos.side == "YES" else (pos.entry_price - mid)
                    if favorable >= (pos.target_edge_cents / 1.0):
                        exit_reason = "target"
                        ok = await self.exit_position(pos, exit_reason)
                        if ok:
                            to_close.append(trade_id)

                for tid in to_close:
                    self.open_positions.pop(tid, None)

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
            # Windows may not support add_signal_handler for SIGTERM in some setups
            signal.signal(s, lambda *_: _handle_sig())

    await app.run()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
