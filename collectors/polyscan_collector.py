"""
PolymarketScan (Polymarket Chronicle) client for OpenClaw.

MVP responsibilities:
- Provide list_markets() for watchlist discovery (action=markets).
- Provide market_detail() for deeper fields when needed (action=market&id=...).
- Optional: whales() and ai-vs-humans() stubs for later.

Includes:
- simple in-memory caching
- circuit breaker with exponential backoff
- float normalization

No auth required. Always pass agent_id.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import aiohttp


def _now() -> float:
    return time.time()


@dataclass
class CircuitBreakerState:
    failures: int = 0
    open_until: float = 0.0
    backoff_seconds: float = 15.0

    def is_open(self) -> bool:
        return _now() < self.open_until

    def on_success(self) -> None:
        self.failures = 0
        self.open_until = 0.0
        self.backoff_seconds = 15.0

    def on_failure(self) -> None:
        self.failures += 1
        if self.failures >= 3:
            self.open_until = _now() + self.backoff_seconds
            self.backoff_seconds = min(self.backoff_seconds * 2.0, 600.0)  # cap at 10 min


class PolyscanClient:
    def __init__(
        self,
        base_url: str,
        agent_id: str,
        timeout_s: int = 15,
        cache_ttl_markets_s: int = 15,
        cache_ttl_market_detail_s: int = 10,
    ):
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self.timeout = aiohttp.ClientTimeout(total=timeout_s)

        self._session: Optional[aiohttp.ClientSession] = None
        self._breaker = CircuitBreakerState()

        # cache: key -> (expires_at, value)
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self.cache_ttl_markets_s = cache_ttl_markets_s
        self.cache_ttl_market_detail_s = cache_ttl_market_detail_s

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _cache_get(self, key: str) -> Optional[Any]:
        item = self._cache.get(key)
        if not item:
            return None
        exp, val = item
        if _now() >= exp:
            self._cache.pop(key, None)
            return None
        return val

    def _cache_set(self, key: str, val: Any, ttl_s: int) -> None:
        self._cache[key] = (_now() + ttl_s, val)

    async def _get_json(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self._breaker.is_open():
            raise RuntimeError(f"Polyscan circuit open until {self._breaker.open_until:.0f}")

        # Always include agent_id
        params = dict(params)
        params["agent_id"] = self.agent_id

        # Polyscan uses action-style query parameters
        # Endpoint is base_url itself (no /search etc)
        url = self.base_url

        sess = await self._get_session()
        try:
            async with sess.get(url, params=params) as r:
                data = await r.json()
            if not data.get("ok", False):
                self._breaker.on_failure()
                raise RuntimeError(data.get("error", "Polyscan request failed"))
            self._breaker.on_success()
            return data
        except Exception:
            self._breaker.on_failure()
            raise

    @staticmethod
    def _to_float(x: Any, default: float = 0.0) -> float:
        try:
            if x is None:
                return default
            return float(x)
        except Exception:
            return default

from typing import Any, Dict, List


class PolyscanClient:
    async def list_markets(
        self,
        category: str,
        limit: int,
        sort: str = "created_at",
        order: str = "desc",
        offset: int = 0,
        use_cache: bool = True,
    ) -> List[Dict[str, Any]]:
        cache_key = f"markets:{category}:{limit}:{sort}:{order}:{offset}"
        if use_cache:
            cached = self._cache_get(cache_key)
            if cached is not None:
                return cached

        data = await self._get_json(
            {
                "action": "markets",
                "category": category,
                "limit": int(limit),
                "offset": int(offset),
                "sort": sort,
                "order": order,
            }
        )
        rows = data.get("data", []) or []

        out: List[Dict[str, Any]] = []
        for it in rows:
            spread = self._to_float(it.get("spread"), 0.0)
            out.append(
                {
                    "market_id": str(it.get("market_id", "")),
                    "title": it.get("title") or "",
                    "slug": it.get("slug") or "",
                    "category": it.get("category") or "",
                    "yes_price": self._to_float(it.get("yes_price"), 0.5),
                    "no_price": self._to_float(it.get("no_price"), 0.5),
                    "volume_usd": self._to_float(it.get("volume_usd"), 0.0),
                    "liquidity_usd": self._to_float(it.get("liquidity_usd"), 0.0),
                    "spread": spread,
                    "spread_cents": spread,
                    "closes_at": it.get("closes_at"),
                    "is_resolved": bool(it.get("is_resolved", False)),
                    "open_interest": self._to_float(it.get("open_interest"), 0.0),
                    "trade_count_24h": int(it.get("trade_count_24h") or 0),
                    "unique_traders_24h": int(it.get("unique_traders_24h") or 0),
                    "image": it.get("image"),
                    "smart_money_bias": self._to_float(it.get("smart_money_bias"), 0.0),
                    "whale_count": int(it.get("whale_count") or 0),
                }
            )

        if use_cache:
            self._cache_set(cache_key, out, ttl_s=self.cache_ttl_markets_s)
        return out

    async def market_detail(self, market_id: str, use_cache: bool = True) -> Dict[str, Any]:
        market_id = str(market_id)
        cache_key = f"market:{market_id}"
        if use_cache:
            cached = self._cache_get(cache_key)
            if cached is not None:
                return cached

        data = await self._get_json({"action": "market", "id": market_id})
        d = data.get("data", {}) or {}
        spread = self._to_float(d.get("spread"), 0.0)

        out = {
            **d,
            "market_id": str(d.get("market_id", market_id)),
            "yes_price": self._to_float(d.get("yes_price"), 0.5),
            "no_price": self._to_float(d.get("no_price"), 0.5),
            "volume_usd": self._to_float(d.get("volume_usd"), 0.0),
            "liquidity_usd": self._to_float(d.get("liquidity_usd"), 0.0),
            "spread": spread,
            "spread_cents": spread,
            "smart_money_bias": self._to_float(d.get("smart_money_bias"), 0.0),
            "whale_volume_usd": self._to_float(d.get("whale_volume_usd"), 0.0),
            "whale_count": int(d.get("whale_count") or 0),
        }

        if use_cache:
            self._cache_set(cache_key, out, ttl_s=self.cache_ttl_market_detail_s)
        return out

    async def whales(self, limit: int = 50) -> List[Dict[str, Any]]:
        data = await self._get_json({"action": "whales", "limit": int(limit)})
        return data.get("data", []) or []

    async def ai_vs_humans(self, limit: int = 50) -> List[Dict[str, Any]]:
        data = await self._get_json({"action": "ai-vs-humans", "limit": int(limit)})
        return data.get("data", []) or []
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError("Implement Polyscan action=markets client in collectors/polyscan_collector.py")
