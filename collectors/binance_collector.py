from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Dict


class BinanceCollector:
    """Lightweight placeholder collector for wiring and local testing."""

    def __init__(self, symbol: str = "BTCUSDT", poll_seconds: float = 1.0, price_seed: float = 60000.0, **_: Any):
        self.symbol = symbol
        self.poll_seconds = float(poll_seconds)
        self.price = float(price_seed)

    async def stream_ticks(self) -> AsyncIterator[Dict[str, Any]]:
        while True:
            await asyncio.sleep(self.poll_seconds)
            yield {"ts": int(time.time()), "price": self.price}
