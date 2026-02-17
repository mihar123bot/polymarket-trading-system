"""
Arena trader — HTTP client for PolymarketScan Agent Arena API.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import aiohttp


class ArenaTrader:
    def __init__(self, base_url: str = "", agent_id: str = "", timeout_s: int = 15):
        self.base_url = base_url.rstrip("/") if base_url else ""
        self.agent_id = agent_id
        self.timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def place_order(
        self,
        market_id: str,
        side: str,
        amount: float,
        action: str = "BUY",
        fair_value: Optional[float] = None,
    ) -> Dict[str, Any]:
        sess = await self._get_session()
        payload: Dict[str, Any] = {
            "agent_id": self.agent_id,
            "market_id": market_id,
            "side": side,
            "amount": amount,
            "action": action,
        }
        if fair_value is not None:
            payload["fair_value"] = fair_value

        async with sess.post(self.base_url, params={"action": "place_order"}, json=payload) as r:
            data = await r.json()
        if not data.get("ok", False):
            raise RuntimeError(data.get("error", "place_order failed"))
        return data.get("data", data)

    async def my_portfolio(self) -> Dict[str, Any]:
        sess = await self._get_session()
        params = {"action": "my_portfolio", "agent_id": self.agent_id}
        async with sess.get(self.base_url, params=params) as r:
            data = await r.json()
        if not data.get("ok", False):
            raise RuntimeError(data.get("error", "my_portfolio failed"))
        return data.get("data", data)

    async def arena_leaderboard(self, limit: int = 10) -> List[Dict[str, Any]]:
        sess = await self._get_session()
        params = {"action": "arena_leaderboard", "limit": str(limit)}
        async with sess.get(self.base_url, params=params) as r:
            data = await r.json()
        if not data.get("ok", False):
            raise RuntimeError(data.get("error", "arena_leaderboard failed"))
        return data.get("data", [])
