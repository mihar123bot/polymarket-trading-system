from __future__ import annotations

from typing import Any, Dict


class ArenaTrader:
    """Arena execution stub. Replace with real order routing implementation."""

    def __init__(self, base_url: str, agent_id: str, timeout_s: int = 15):
        self.base_url = base_url
        self.agent_id = agent_id
        self.timeout_s = timeout_s

    async def place_order(self, market_id: str, side: str, qty: float, price: float) -> Dict[str, Any]:
        return {
            "ok": True,
            "market_id": market_id,
            "side": side,
            "qty": qty,
            "price": price,
            "order_id": None,
        }
