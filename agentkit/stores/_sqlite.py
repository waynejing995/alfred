from __future__ import annotations

import random
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from loguru import logger

T = TypeVar("T")


class SQLiteStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self.path,
            timeout=1.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._write_count = 0
        self._wal_fallback_warned = False
        self._configure()

    def close(self) -> None:
        self._try_wal_checkpoint()
        self._conn.close()

    def write(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        for attempt in range(15):
            try:
                with self._lock:
                    self._conn.execute("BEGIN IMMEDIATE")
                    try:
                        result = fn(self._conn)
                    except BaseException:
                        self._conn.rollback()
                        raise
                    else:
                        self._conn.commit()
                self._write_count += 1
                if self._write_count % 50 == 0:
                    self._try_wal_checkpoint()
                return result
            except sqlite3.OperationalError as exc:
                if not _is_busy(exc) or attempt >= 14:
                    raise
                time.sleep(random.uniform(0.020, 0.150))
        raise RuntimeError("unreachable sqlite retry state")

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def executemany(self, sql: str, params: list[tuple]) -> sqlite3.Cursor:
        return self._conn.executemany(sql, params)

    def _configure(self) -> None:
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        try:
            mode = self._conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        except sqlite3.OperationalError as exc:
            self._warn_wal_fallback(exc)
            self._conn.execute("PRAGMA journal_mode=DELETE")
        else:
            if str(mode).lower() != "wal":
                self._warn_wal_fallback(RuntimeError(f"journal_mode={mode}"))
                self._conn.execute("PRAGMA journal_mode=DELETE")

    def _try_wal_checkpoint(self) -> None:
        try:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.OperationalError as exc:
            logger.warning("sqlite WAL checkpoint failed for {}: {}", self.path, exc)

    def _warn_wal_fallback(self, exc: BaseException) -> None:
        if self._wal_fallback_warned:
            return
        self._wal_fallback_warned = True
        logger.warning("sqlite WAL unavailable for {}; falling back to DELETE: {}", self.path, exc)


def _is_busy(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "locked" in message or "busy" in message

