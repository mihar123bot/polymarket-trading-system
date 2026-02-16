from typing import Any, AsyncIterator, Dict


class BinanceCollector:
    async def stream_ticks(self) -> AsyncIterator[Dict[str, Any]]:
        raise NotImplementedError("Implement Binance websocket stream in collectors/binance_collector.py")
