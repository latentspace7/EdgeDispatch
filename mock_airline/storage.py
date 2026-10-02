from __future__ import annotations

import json
import sqlite3
from functools import lru_cache
from pathlib import Path
from threading import RLock

from . import AS_OF, DATASET_VERSION

DATA = Path(__file__).parent / "data"


class Store:
    def __init__(self, source: str):
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = RLock()
        try:
            self.connection.executescript((DATA / f"{source}.sql").read_text())
            self.connection.execute("PRAGMA query_only = ON")
        except BaseException:
            self.connection.close()
            raise

    def rows(self, sql: str, parameters: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(row) for row in self.connection.execute(sql, parameters)]


@lru_cache(maxsize=2)
def database(source: str) -> Store:
    if source not in {"customers", "support"}:
        raise ValueError("Unknown data source")
    return Store(source)


def result(source: str, **values) -> dict:
    return {
        "source": source,
        "dataset_version": DATASET_VERSION,
        "as_of": AS_OF,
        **values,
    }


def page(source: str, rows: list[dict], offset: int, limit: int) -> dict:
    if not 1 <= limit <= 50 or offset < 0:
        raise ValueError("Use limit between 1 and 50 and offset at least zero")
    end = offset + limit
    return result(
        source,
        records=rows[offset:end],
        total=len(rows),
        offset=offset,
        next_offset=end if end < len(rows) else None,
    )


@lru_cache(maxsize=1)
def policies() -> list[dict]:
    return json.loads((DATA / "policies.json").read_text())
