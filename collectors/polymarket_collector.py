from __future__ import annotations

from typing import Any, Dict, Optional


class PolymarketCLOB:
    """Minimal read-only Polymarket client stub."""

    def __init__(self, **_: Any):
        pass

    async def get_midpoint(self, market_id: str) -> Optional[float]:
        _ = market_id
        return None

    async def get_top_of_book(self, market_id: str) -> Optional[Dict[str, float]]:
        _ = market_id
        return None
