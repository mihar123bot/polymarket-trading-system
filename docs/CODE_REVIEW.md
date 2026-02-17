# Code Review — Polymarket Trading System

**Date:** 2026-02-16

## Status Summary

The codebase is a **broken prototype**. The core design intent is sound — a Binance-signal-driven latency trader for Polymarket "Up or Down" markets via Arena/Polyscan APIs, with SQLite persistence. However, nearly every file contains **duplicate class definitions** (an implementation followed by a stub that overwrites it), `main.py` has **orphaned code blocks outside any function** that would cause `SyntaxError` or `IndentationError`, and `config/config.yaml` has **duplicate top-level keys** making it unparseable as intended. The code **cannot run as-is**.

## Component Assessment

| Component | File | Status | Notes |
|---|---|---|---|
| Main orchestrator | `main.py` | **Broken** | Orphaned code blocks mid-file; duplicate `request_shutdown`; references undefined `Position`, `self.kill`, variables `c`; dead `exit_position` method |
| Config | `config/config.yaml` | **Broken** | Duplicate keys (`trigger_bps`, `budget`, `execution`, `watchlist`). YAML spec: last value wins, so first block is silently lost |
| DB layer | `storage/db.py` | **Working → overwritten** | Full working implementation exists, then a second `class DB` with all `NotImplementedError` stubs overwrites it |
| Schema | `storage/schema.sql` | **Working** | Clean, well-indexed SQLite schema. Good. |
| Polyscan client | `collectors/polyscan_collector.py` | **Working → overwritten** | Full implementation with caching + circuit breaker, then a second stub class overwrites it. Also has a dangling `) -> List[...]` line |
| Binance collector | `collectors/binance_collector.py` | **Working → overwritten** | Simple placeholder (yields static price), then a second `stream_ticks` raises `NotImplementedError` |
| Arena trader | `trading/arena_trader.py` | **Broken** | Two `ArenaTrader` classes; first returns stub dict, second raises `NotImplementedError`. Neither matches `main.py`'s call signature (`place_order` params differ) |
| Polymarket CLOB | `collectors/polymarket_collector.py` | **Stub** | Returns `None` for everything. Used in `main.py` but only for `exit_position` (dead code) |
| Tests | `tests/test_paper_trading_system.py` | **Would fail** | Uses fakes that bypass broken code, but `App.__init__` would crash before fakes are injected (double init of attributes) |
| Docs | `README.md`, `ARCHITECTURE.md`, `OPERATIONS.md` | **Good** | Well-written, accurate to the *intended* design |

## What's Working (worth keeping)

1. **`storage/schema.sql`** — Clean, production-ready schema with proper indexes and WAL mode
2. **`storage/db.py` (first class)** — Solid SQLite wrapper with retry logic, upsert patterns, daily rollups
3. **`collectors/polyscan_collector.py` (first class)** — Good async client with caching, circuit breaker, float normalization
4. **`main.py` core logic** — `EventDetector`, `choose_side_for_updown`, `decide_entry`, `execute_entry`, `refresh_watchlist` are well-thought-out
5. **`config/config.yaml` (intent)** — Good config structure covering signals, risk, budget, execution
6. **Test design** — `test_paper_trading_system.py` has good fake objects and validates the full entry→exit cycle
7. **Documentation** — README, ARCHITECTURE, OPERATIONS are clear and useful

## What's Scaffolding (stubs/placeholders/incomplete)

1. **`BinanceCollector`** — Yields a static `price_seed` forever; no real websocket
2. **`PolymarketCLOB`** — Pure stub returning `None`
3. **`ArenaTrader`** — Neither version is a real implementation; just stubs
4. **Whale signal logic** — Config has `whales.enabled: false`; code in `maybe_exit_positions` references `self.polyscan.whales()` but it's orphaned/unreachable
5. **Kill switch** — Referenced in config (`risk.kill_switch`) but never implemented; `self.kill.trigger_pause()` called but `self.kill` doesn't exist

## Issues & Bugs

### Critical (prevents running)

1. **Duplicate class definitions in every module** — Python will use the *last* class defined. The working implementations are overwritten by `NotImplementedError` stubs. This is the #1 issue.
2. **`main.py` has code outside functions** — Around line 270+, there's a `self.db.record_trade(...)` block at module level (not inside any method). This is a `SyntaxError`.
3. **`config/config.yaml` duplicate keys** — `trigger_bps` appears in two places, `budget`, `execution`, `watchlist` are duplicated. YAML parsers silently take the last value, losing the first block's settings (like `max_spread_cents`, `slippage_buffer_cents`, `min_edge_after_costs_cents`, etc.).
4. **`App.__init__` initializes attributes twice** — Creates `self.binance`, `self.polyscan`, `self.arena`, `self.db` with config params, then immediately overwrites them with no-arg constructors (which would fail since the stubs require args or raise errors).

### Serious

5. **`maybe_exit_positions` references `self._entry_ts_by_trade`** — This dict is only created in `run()`, not in `__init__`. If `maybe_exit_positions` is called before `run()`, it crashes with `AttributeError`.
6. **`maybe_exit_positions` has orphaned whale code** — After the position exit loop, there's `whales = await self.polyscan.whales(...)` and `whales_by_market` code that's syntactically inside the method but logically disconnected; it also references undefined `since` and `candidates`.
7. **`exit_position` method references `self.kill`** — Never defined anywhere.
8. **`exit_position` references `Position` dataclass** — Never defined; only `MarketRow` and `EntryDecision` exist.
9. **ArenaTrader signature mismatch** — `main.py` calls `place_order(market_id, side, amount, action, fair_value)` but the first stub expects `(market_id, side, qty, price)`.

### Minor

10. **No `requirements.txt` or `pyproject.toml`** — Dependencies (pyyaml, aiohttp, websockets) only mentioned in docs
11. **No `__init__.py` files** — Package imports (`from collectors.binance_collector import ...`) work only if run from repo root
12. **`parse_iso_to_ts` imports `datetime` inside function** — Minor but unusual

## Improvement Suggestions

1. **Delete all duplicate class stubs** — Each file should have ONE class definition. Remove the `NotImplementedError` stubs that overwrite the working code.
2. **Fix `config/config.yaml`** — Merge duplicate keys into single blocks. Validate with `python -c "import yaml; yaml.safe_load(open('config/config.yaml'))"`.
3. **Fix `main.py` structure** — Remove orphaned code blocks. Remove dead `exit_position` method. Consolidate exit logic into `maybe_exit_positions` only.
4. **Remove double-init in `App.__init__`** — Keep only the parameterized constructors.
5. **Add `_entry_ts_by_trade` to `__init__`** — Don't rely on `run()` creating it.
6. **Add `requirements.txt`** — `pyyaml`, `aiohttp`, `websockets`, `pytest`
7. **Add `__init__.py`** files to `collectors/`, `trading/`, `storage/`
8. **Implement real `BinanceCollector`** — Use `websockets` library to connect to `wss://stream.binance.com:9443/ws/btcusdt@trade`
9. **Implement real `ArenaTrader`** — Wire up actual HTTP calls to the Polyscan agent-api for `place_order` and `my_portfolio`
10. **Add logging** — Replace `print()` with `logging` module; add structured log fields

## Open Questions for Waleed

1. **Why are there duplicate class definitions everywhere?** Was this from merging branches, iterative AI generation, or intentional stubs-alongside-implementations? Need to know which version to keep.
2. **Is Arena the only execution target?** The config has `env.mode: arena` but main.py mentions `paper` mode in comments. Is paper trading via fake Arena responses, or a separate path?
3. **What's the Polyscan agent-api auth model?** Just `agent_id` as query param? No API key/token? Is the Supabase URL in config still valid?
4. **Is the Polymarket CLOB client needed at all?** It's imported but only used in the dead `exit_position` method. Can we remove it?
5. **What's the intended whale signal flow?** Config has it disabled. Is this a future feature, or should the orphaned whale code be removed?
6. **Kill switch implementation** — Config defines `max_consecutive_exit_failures`, `max_daily_drawdown_pct`, `pause_minutes_on_trigger`. Do you want this implemented, or is it aspirational?
7. **Deployment target?** Local machine, VPS, Docker? This affects how we structure the project.
8. **Budget for real trading?** Config shows `default_buy_usd: 1`, `max_buy_usd: 10`. Are these real numbers or just test values?
9. **Do you want the test to actually pass?** Currently it can't due to `App.__init__` crashing. Should we prioritize making the test green?
10. **Single market focus or multi-market?** Current logic filters for "Bitcoin Up or Down" specifically. Is expanding to other market types planned?
