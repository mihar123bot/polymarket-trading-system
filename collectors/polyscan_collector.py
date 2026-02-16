from typing import Any, Dict, List


class PolyscanClient:
    async def list_markets(
        self,
        category: str,
        limit: int,
        sort: str = "created_at",
        order: str = "desc",
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError("Implement Polyscan action=markets client in collectors/polyscan_collector.py")
