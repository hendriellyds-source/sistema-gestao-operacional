"""Short-lived SQLite transactions with deterministic resource cleanup."""

import sqlite3


class ClosingConnection(sqlite3.Connection):
    """Commit/rollback like sqlite3.Connection, then release its native cache.

    SQLite's default context manager does not close the connection. Depending on
    cyclic garbage collection lets polling jobs retain many databases and page
    caches, enough to exhaust a small web instance. These connections are owned
    by one operation; callers must not reuse them after leaving the with block.
    Explicit close() and contextlib.closing remain supported.
    """

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()
