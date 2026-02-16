class DB:
    def init_schema(self) -> None:
        raise NotImplementedError("Implement DB.init_schema in storage/db.py")

    def upsert_market(self, market_id: str, title: str, category: str, source: str, slug: str = "") -> None:
        raise NotImplementedError("Implement DB.upsert_market in storage/db.py")

    def record_watchlist_event(self, market_id: str, action: str, reason: str) -> None:
        raise NotImplementedError("Implement DB.record_watchlist_event in storage/db.py")

    def record_trade(self, **kwargs) -> None:
        raise NotImplementedError("Implement DB.record_trade in storage/db.py")

    def record_trade_snapshot(self, **kwargs) -> None:
        raise NotImplementedError("Implement DB.record_trade_snapshot in storage/db.py")

    def get_today_trade_count(self, mode: str) -> int:
        raise NotImplementedError("Implement DB.get_today_trade_count in storage/db.py")
