from typing import Any, Dict, Optional


class ArenaTrader:
    async def place_order(
        self,
        market_id: str,
        side: str,
        amount: float,
        action: str,
        fair_value: Optional[float] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError("Implement Arena place_order in trading/arena_trader.py")

    async def my_portfolio(self) -> Dict[str, Any]:
        raise NotImplementedError("Implement Arena my_portfolio in trading/arena_trader.py")
