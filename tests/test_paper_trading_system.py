import asyncio
import sys
import time
from pathlib import Path
import types

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if "yaml" not in sys.modules:
    yaml_stub = types.ModuleType("yaml")
    yaml_stub.safe_load = lambda *_args, **_kwargs: {}
    sys.modules["yaml"] = yaml_stub

# Stub aiohttp if not installed
if "aiohttp" not in sys.modules:
    aiohttp_stub = types.ModuleType("aiohttp")
    aiohttp_stub.ClientSession = type("ClientSession", (), {})
    aiohttp_stub.ClientTimeout = lambda **kw: None
    sys.modules["aiohttp"] = aiohttp_stub

from main import App


class FakeDB:
    def __init__(self):
        self.trades = []
        self.snapshots = []
        self.watchlist_events = []
        self.markets = []

    def init_schema(self):
        return None

    def upsert_market(self, market_id, title, category, source, slug=""):
        self.markets.append(
            {
                "market_id": market_id,
                "title": title,
                "category": category,
                "source": source,
                "slug": slug,
            }
        )

    def record_watchlist_event(self, market_id, action, reason):
        self.watchlist_events.append({"market_id": market_id, "action": action, "reason": reason})

    def record_trade(self, **kwargs):
        self.trades.append(kwargs)

    def record_trade_snapshot(self, **kwargs):
        self.snapshots.append(kwargs)

    def get_today_trade_count(self, mode):
        return 0


class FakePolyscan:
    async def list_markets(self, category, limit, sort="created_at", order="desc"):
        return [
            {
                "market_id": "m1",
                "slug": "bitcoin-updown-test",
                "title": "Bitcoin Up or Down - Paper Test",
                "category": "Crypto",
                "yes_price": 0.52,
                "no_price": 0.48,
                "liquidity_usd": 50000,
                "volume_usd": 100000,
                "closes_at": "2099-12-31T00:00:00+00:00",
                "is_resolved": False,
            }
        ]


class FakeArenaPaperTrader:
    def __init__(self):
        self.cash = 1000.0
        self.positions = {}
        self.recent_orders = []

    async def place_order(self, market_id, side, amount, action, fair_value=None):
        if action == "BUY":
            price = 0.5
            shares = amount / price
            cost = amount
            self.cash -= cost
            self.positions[market_id] = {
                "market_id": market_id,
                "side": side,
                "shares": shares,
                "avg_price": price,
                "current_price": price,
                "unrealized_pnl": 0.0,
            }
            self.recent_orders.append(
                {
                    "id": f"buy-{len(self.recent_orders)+1}",
                    "created_at": "2099-01-01T00:00:00+00:00",
                    "filled_at": "2099-01-01T00:00:01+00:00",
                }
            )
            return {"shares": shares, "price": price, "cost": cost}

        if action == "SELL":
            pos = self.positions[market_id]
            shares = min(amount, pos["shares"])
            price = pos["current_price"]
            proceeds = shares * price
            self.cash += proceeds
            pos["shares"] -= shares
            if pos["shares"] <= 0:
                del self.positions[market_id]
            self.recent_orders.append(
                {
                    "id": f"sell-{len(self.recent_orders)+1}",
                    "created_at": "2099-01-01T00:01:00+00:00",
                    "filled_at": "2099-01-01T00:01:01+00:00",
                }
            )
            return {"shares": shares, "price": price, "cost": proceeds}

        raise ValueError(f"Unsupported action: {action}")

    async def my_portfolio(self):
        portfolio_positions = list(self.positions.values())
        market_value = sum(p["shares"] * p["current_price"] for p in portfolio_positions)
        return {
            "positions": portfolio_positions,
            "recent_orders": self.recent_orders,
            "portfolio_value": self.cash + market_value,
        }


def test_paper_trading_entry_and_exit_cycle():
    async def _run():
        cfg = {
            "env": {"mode": "paper"},
            "signals": {"latency": {"window_seconds": 10, "trigger_bps": 5}},
            "budget": {"max_watchlist_markets": 8, "max_trades_per_day": 50},
            "execution": {
                "default_buy_usd": 1,
                "max_buy_usd": 10,
                "take_profit_usd": 0.05,
                "stop_loss_usd": 0.05,
                "max_hold_seconds": 120,
            },
            "watchlist": {
                "market_search": {"refresh_seconds": 15},
                "eligibility": {"min_liquidity_usd": 8000, "min_seconds_to_close": 120},
            },
            "monitoring": {"poll_seconds": 0.1},
        }

        app = App(cfg)
        app.db = FakeDB()
        app.polyscan = FakePolyscan()
        app.arena = FakeArenaPaperTrader()

        await app.init()

        decision = await app.decide_entry(move_bps=15.0, binance_price=65000.0)
        assert decision is not None
        assert decision.side == "YES"

        app._entry_ts_by_trade = {}
        trade_id = await app.execute_entry(decision)
        assert trade_id is not None
        app._entry_ts_by_trade[trade_id] = int(time.time()) - 5

        position = app.arena.positions[decision.market.market_id]
        position["current_price"] = 0.55
        position["unrealized_pnl"] = (position["current_price"] - position["avg_price"]) * position["shares"]

        await app.maybe_exit_positions()

        assert decision.market.market_id not in app.arena.positions

        entry_records = [t for t in app.db.trades if "ts_entry" in t]
        exit_records = [t for t in app.db.trades if "ts_exit" in t]
        assert len(entry_records) == 1
        assert len(exit_records) == 1
        assert exit_records[0]["outcome"] == "WIN"

        phases = {snap["phase"] for snap in app.db.snapshots}
        assert {"ENTRY", "EXIT"}.issubset(phases)

    asyncio.run(_run())
