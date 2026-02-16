# Operations Guide

## Prerequisites

- Python 3.10+
- Network access to configured API endpoints
- Optional virtualenv for dependency isolation

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install pyyaml aiohttp websockets pytest
```

## Configuration Checklist

Update `config/config.yaml` before running:

- `env.mode`: choose runtime mode.
- `binance.symbol`: default signal source.
- `watchlist.eligibility`: liquidity/spread filters.
- `signals.latency`: movement threshold and timing.
- `budget` + `risk.kill_switch`: loss/control guards.
- `polyscan.*`: API base URL and agent identity.
- `storage.*`: SQLite file and schema path.

## Run the Bot

```bash
python main.py
```

## Run Tests

```bash
pytest -q
```

## Troubleshooting

### YAML parse/config errors

If startup fails while loading config:
- Validate indentation and duplicate keys in `config/config.yaml`.
- Use a YAML validator and ensure each top-level section appears once.

### API timeouts

- Increase `polyscan.timeout_seconds`.
- Confirm endpoint availability and credentials/agent ID.

### No trade activity

- Lower `signals.latency.trigger_bps` for easier trigger conditions.
- Confirm watchlist filters are not too strict.
- Verify incoming Binance prices are updating.
