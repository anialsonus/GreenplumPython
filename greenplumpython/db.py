from typing import Iterable, List, Optional

import psycopg2
import psycopg2.extras


class Database:
    def __init__(self, **params) -> None:
        self._conn = psycopg2.connect(
            " ".join([f"{k}={v}" for k, v in params.items()]),
            cursor_factory=psycopg2.extras.RealDictCursor,
        )

    def execute(self, query: str, args: List = [], has_results: bool = True) -> Optional[Iterable]:
        with self._conn.cursor() as cursor:
            cursor.execute(query, args)
            return cursor.fetchall() if has_results else None

    def close(self) -> None:
        self._conn.close()


def database(**conn_strings) -> Database:
    return Database(**conn_strings)