# Architecture Overview

## Core Flow

The runtime in `main.py` coordinates four layers:

1. **Collectors**
   - `BinanceCollector` emits spot-price updates used for short-window momentum detection.
   - `PolyscanClient` provides market discovery and market metadata.
   - `PolymarketCLOB` is available for CLOB-specific integrations.

2. **Signal + Decision**
   - `EventDetector` tracks rolling price movement in bps over a configured window.
   - Entry decision logic maps movement direction to market side (`YES`/`NO`) and budgeted order sizing.

3. **Execution**
   - `ArenaTrader` places BUY/SELL orders and provides portfolio snapshots.

4. **Persistence**
   - `DB` writes watchlist snapshots, trade events, and execution metadata to SQLite (`storage/schema.sql`).

## App State

`App` keeps in-memory runtime state for:
- Active watchlist (`watchlist`)
- Open trade IDs by market (`open_trade_ids_by_market`)
- Cached portfolio snapshot
- Signal detector rolling window

This enables low-overhead decision-making between API polls.

## Data Sources

- **Binance**: real-time BTCUSDT signal input.
- **Polyscan**: market list + detail endpoints.
- **Arena**: order placement and portfolio/position state.

## Design Principles

- **MVP-first**: prioritize a runnable loop over abstraction-heavy architecture.
- **Config-driven behavior**: thresholds, budgets, and execution controls are YAML-configurable.
- **Testability**: `tests/test_paper_trading_system.py` uses fake components to validate entry/exit behavior.
