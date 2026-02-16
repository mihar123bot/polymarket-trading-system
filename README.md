# Polymarket Trading System

A lightweight Python trading bot prototype focused on event-based paper trading ideas for Polymarket-style YES/NO markets.

The project wires together:
- **Market discovery** via a Polyscan API client.
- **Price/event signal input** via a Binance BTCUSDT collector.
- **Order/portfolio actions** via an Arena trader client.
- **Persistence** via a local SQLite database.

> Status: **prototype / MVP**. The codebase is functional for local experimentation but still evolving.

## Repository Layout

```text
.
├── main.py                         # App orchestration loop
├── config/config.yaml              # Runtime configuration
├── collectors/
│   ├── binance_collector.py        # Binance price feed collector
│   ├── polymarket_collector.py     # Polymarket CLOB helper/client
│   └── polyscan_collector.py       # Polyscan market discovery client
├── trading/arena_trader.py         # Arena order + portfolio API wrapper
├── storage/
│   ├── db.py                       # SQLite persistence wrapper
│   └── schema.sql                  # DB schema
└── tests/test_paper_trading_system.py
```

## Quick Start

### 1) Create a Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install pyyaml aiohttp websockets pytest
```

### 2) Configure runtime values

Edit `config/config.yaml` for your local mode, thresholds, and API settings.

At minimum, verify:
- `polyscan.base_url`
- `polyscan.agent_id`
- signal thresholds in `signals.latency`
- risk/budget constraints

### 3) Initialize and run

```bash
python main.py
```

The app initializes the SQLite schema, refreshes watchlist candidates, tracks market signals, and manages entries/exits based on configured logic.

## Running Tests

```bash
pytest -q
```

The included unit test validates a basic paper-trading cycle (entry then exit) with fake in-memory collaborators.

## Documentation

Additional docs live in [`docs/`](docs):
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md)

## Notes and Limitations

- The project currently favors simplicity over hardening.
- Several components are API-dependent; use mocked/fake providers for deterministic local testing.
- Validate configuration thoroughly before running live integrations.

## License

No license file is currently included. Add one before external distribution.
