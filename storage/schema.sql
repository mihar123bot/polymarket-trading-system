PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS markets (
  market_id TEXT PRIMARY KEY,
  slug TEXT,
  title TEXT,
  category TEXT,
  source TEXT,
  created_at_ts INTEGER,
  updated_at_ts INTEGER
);

CREATE INDEX IF NOT EXISTS idx_markets_updated ON markets(updated_at_ts);

CREATE TABLE IF NOT EXISTS watchlist_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  market_id TEXT NOT NULL,
  action TEXT NOT NULL,
  reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_watchlist_events_ts ON watchlist_events(ts);

CREATE TABLE IF NOT EXISTS trades (
  trade_id TEXT PRIMARY KEY,
  ts_entry INTEGER NOT NULL,
  ts_exit INTEGER,
  mode TEXT NOT NULL,
  strategy TEXT NOT NULL,
  market_id TEXT NOT NULL,
  side TEXT NOT NULL,
  qty REAL NOT NULL,
  price_entry REAL NOT NULL,
  price_exit REAL,
  pnl REAL,
  outcome TEXT,
  exit_reason TEXT,
  fees_estimated REAL DEFAULT 0,
  arena_order_id TEXT,
  filled_at TEXT,
  arena_exit_order_id TEXT,
  exit_filled_at TEXT,
  notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_trades_ts_entry ON trades(ts_entry);
CREATE INDEX IF NOT EXISTS idx_trades_market ON trades(market_id, ts_entry);

CREATE TABLE IF NOT EXISTS trade_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_id TEXT NOT NULL,
  ts INTEGER NOT NULL,
  phase TEXT NOT NULL,
  binance_price REAL,
  yes_price REAL,
  no_price REAL,
  liquidity_usd REAL,
  edge_est REAL,
  unrealized_pnl REAL,
  portfolio_value REAL,
  current_price REAL,
  avg_price REAL,
  market_value REAL,
  extra TEXT
);

CREATE INDEX IF NOT EXISTS idx_trade_snapshots_trade_ts ON trade_snapshots(trade_id, ts);

CREATE TABLE IF NOT EXISTS daily_metrics (
  day TEXT PRIMARY KEY,
  mode TEXT,
  trades INTEGER,
  win_rate REAL,
  avg_pnl REAL,
  exit_failures INTEGER,
  portfolio_end REAL,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS whale_touches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  market_id TEXT NOT NULL,
  wallet TEXT,
  side TEXT,
  amount_usd REAL,
  tier TEXT,
  anomaly_tags TEXT,
  used_as_confirmation INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_whale_touches_ts ON whale_touches(ts);
