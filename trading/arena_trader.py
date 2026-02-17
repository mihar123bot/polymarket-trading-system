from __future__ import annotations

from typing import Any, Dict, Optional


class ArenaTrader:
    """Arena execution stub. Replace with real order routing implementation."""

    def __init__(self, base_url: str = "", agent_id: str = "", timeout_s: int = 15):
        self.base_url = base_url
        self.agent_id = agent_id
        self.timeout_s = timeout_s

    async def place_order(
        self,
        market_id: str,
        side: str,
        amount: float,
        action: str,
        fair_value: Optional[float] = None,
    ) -> Dict[str, Any]:
        return {
            "ok": True,
            "market_id": market_id,
            "side": side,
            "amount": amount,
            "action": action,
            "shares": amount / 0.5,
            "price": 0.5,
            "cost": amount,
            "order_id": None,
        }

    async def my_portfolio(self) -> Dict[str, Any]:
        return {
            "positions": [],
            "recent_orders": [],
            "portfolio_value": 0.0,
        }
