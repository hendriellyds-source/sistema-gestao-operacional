from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any


class MemoryQuery:
    def __init__(self, client: "MemorySupabase", table: str):
        self.client = client
        self.table = table
        self.filters: list[tuple[str, str, Any]] = []
        self.operation = "select"
        self.payload: Any = None
        self.limit_count: int | None = None

    def select(self, *_args: Any, **_kwargs: Any) -> "MemoryQuery":
        return self

    def eq(self, key: str, value: Any) -> "MemoryQuery":
        self.filters.append(("eq", key, value))
        return self

    def is_(self, key: str, value: Any) -> "MemoryQuery":
        self.filters.append(("is", key, value))
        return self

    def gte(self, key: str, value: Any) -> "MemoryQuery":
        self.filters.append(("gte", key, value))
        return self

    def lte(self, key: str, value: Any) -> "MemoryQuery":
        self.filters.append(("lte", key, value))
        return self

    def order(self, *_args: Any, **_kwargs: Any) -> "MemoryQuery":
        return self

    def limit(self, value: int) -> "MemoryQuery":
        self.limit_count = value
        return self

    def insert(self, payload: Any) -> "MemoryQuery":
        self.operation = "insert"
        self.payload = payload
        return self

    def update(self, payload: dict[str, Any]) -> "MemoryQuery":
        self.operation = "update"
        self.payload = payload
        return self

    def _matches(self, row: dict[str, Any]) -> bool:
        for operation, key, value in self.filters:
            current = row.get(key)
            if operation == "eq" and current != value:
                return False
            if operation == "is" and value == "null" and current is not None:
                return False
            if operation == "gte" and str(current or "") < str(value):
                return False
            if operation == "lte" and str(current or "") > str(value):
                return False
        return True

    def execute(self) -> SimpleNamespace:
        rows = self.client.tables.setdefault(self.table, [])
        if self.operation == "insert":
            inserted = deepcopy(self.payload if isinstance(self.payload, list) else [self.payload])
            rows.extend(inserted)
            return SimpleNamespace(data=deepcopy(inserted))
        matched = [row for row in rows if self._matches(row)]
        if self.operation == "update":
            for row in matched:
                row.update(deepcopy(self.payload))
        if self.limit_count is not None:
            matched = matched[: self.limit_count]
        return SimpleNamespace(data=deepcopy(matched))


class MemorySupabase:
    def __init__(self, tables: dict[str, list[dict[str, Any]]] | None = None):
        self.tables = deepcopy(tables or {})

    def table(self, name: str) -> MemoryQuery:
        return MemoryQuery(self, name)
