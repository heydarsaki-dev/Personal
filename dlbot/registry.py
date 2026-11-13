"""TTL file registry (SQLite) + sweeper + startup cleanup (spec section 4.6)."""
from __future__ import annotations

import contextlib
import os
import secrets
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
  token TEXT PRIMARY KEY,
  chat_id INTEGER NOT NULL,
  message_id INTEGER,
  path TEXT NOT NULL,
  size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  kind TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  quality TEXT NOT NULL DEFAULT '',
  duration_s INTEGER
);
CREATE INDEX IF NOT EXISTS idx_files_expires ON files(expires_at);
"""


@dataclass
class FileRecord:
    token: str
    chat_id: int
    path: Path
    size: int
    sha256: str
    created_at: float
    expires_at: float
    kind: str = "document"
    title: str = ""
    quality: str = ""
    message_id: int | None = None
    duration_s: int | None = None

    def to_row(self) -> tuple:
        return (
            self.token, self.chat_id, self.message_id, str(self.path), self.size, self.sha256,
            self.created_at, self.expires_at, self.kind, self.title, self.quality, self.duration_s,
        )

    @classmethod
    def from_row(cls, row: tuple) -> "FileRecord":
        token, chat_id, message_id, path, size, sha256, created_at, expires_at, kind, title, quality, duration = row
        return cls(
            token=token, chat_id=chat_id, message_id=message_id, path=Path(path), size=size,
            sha256=sha256, created_at=created_at, expires_at=expires_at, kind=kind, title=title,
            quality=quality, duration_s=duration,
        )


class Registry:
    def __init__(self, db_path: Path, ttl_s: int, *, ttl_provider: Callable[[], int] | None = None) -> None:
        self.db_path = Path(db_path)
        self.ttl_s = ttl_s
        self._ttl_provider = ttl_provider
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    # ---- plumbing ------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def init_schema(self) -> None:
        with self._lock, contextlib.closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def ttl(self) -> int:
        if self._ttl_provider is None:
            return self.ttl_s
        try:
            return int(self._ttl_provider())
        except Exception:
            return self.ttl_s

    @staticmethod
    def reserve_token() -> str:
        return secrets.token_hex(16)

    # ---- rows ----------------------------------------------------------
    def add(self, rec: FileRecord) -> None:
        with self._lock, contextlib.closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", rec.to_row()
            )
            conn.commit()

    def get(self, token: str) -> FileRecord | None:
        with self._lock, contextlib.closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM files WHERE token=?", (token,)).fetchone()
        return FileRecord.from_row(row) if row else None

    def find_by_message(self, chat_id: int, message_id: int) -> FileRecord | None:
        """Used when a user replies to a file the bot sent (requirement 3)."""
        if not message_id:
            return None
        with self._lock, contextlib.closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM files WHERE chat_id=? AND message_id=? ORDER BY created_at DESC LIMIT 1",
                (chat_id, message_id),
            ).fetchone()
        return FileRecord.from_row(row) if row else None

    def mark_sent(self, token: str, chat_id: int, message_id: int) -> None:
        with self._lock, contextlib.closing(self._connect()) as conn:
            conn.execute(
                "UPDATE files SET chat_id=?, message_id=? WHERE token=?",
                (chat_id, message_id, token),
            )
            conn.commit()

    def is_alive(self, token: str, *, now: float | None = None) -> bool:
        rec = self.get(token)
        if rec is None:
            return False
        now = time.time() if now is None else now
        return rec.path.exists() and now < rec.expires_at

    def expire_reason(self, token: str, *, now: float | None = None) -> str | None:
        rec = self.get(token)
        now = time.time() if now is None else now
        if rec is None:
            return "no_record"
        if not rec.path.exists():
            return "file_gone"
        if now >= rec.expires_at:
            return "ttl"
        return None

    def drop(self, token: str, *, delete_file: bool = True) -> None:
        rec = self.get(token)
        if rec is not None and delete_file:
            with contextlib.suppress(OSError):
                rec.path.unlink()
        with self._lock, contextlib.closing(self._connect()) as conn:
            conn.execute("DELETE FROM files WHERE token=?", (token,))
            conn.commit()

    def sweep(self, *, now: float | None = None) -> int:
        """Remove expired rows and vanished files. Returns the number removed."""
        now = time.time() if now is None else now
        removed = 0
        with self._lock, contextlib.closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM files").fetchall()
        for row in rows:
            rec = FileRecord.from_row(row)
            if now >= rec.expires_at or not rec.path.exists():
                self.drop(rec.token)
                removed += 1
        return removed

    def startup_cleanup(self, files_dir: Path, tmp_dir: Path) -> int:
        """D2/R2: a restart means every old token is dead -> wipe files/, tmp/ and the table."""
        removed = 0
        for directory in (files_dir, tmp_dir):
            if directory.exists():
                for child in directory.iterdir():
                    with contextlib.suppress(OSError):
                        if child.is_dir():
                            shutil.rmtree(child, ignore_errors=True)
                        else:
                            child.unlink()
                            removed += 1
        with self._lock, contextlib.closing(self._connect()) as conn:
            conn.execute("DELETE FROM files")
            conn.commit()
        return removed

    def stats(self) -> dict[str, int]:
        with self._lock, contextlib.closing(self._connect()) as conn:
            rows = conn.execute("SELECT size, path FROM files").fetchall()
        count = 0
        used = 0
        for size, path in rows:
            if os.path.exists(path):
                count += 1
                used += int(size or 0)
        return {"files": count, "used_bytes": used}


def run_sweeper(reg: Registry, stop: threading.Event, *, interval_s: int = 60,
                files_dir: Path | None = None, tmp_dir: Path | None = None) -> None:
    """Daemon loop: TTL sweep + stray tmp cleanup."""
    while not stop.wait(interval_s):
        try:
            reg.sweep()
            if tmp_dir is not None and tmp_dir.exists():
                cutoff = time.time() - 3600
                for child in tmp_dir.iterdir():
                    with contextlib.suppress(OSError):
                        if child.is_file() and child.stat().st_mtime < cutoff:
                            child.unlink()
        except Exception:
            continue


def admin_alert(tg: "object", cfg: "object", title: str, detail: str, code: str = "") -> None:
    """Send the admin_alert template to the admin only (never to the user)."""
    import strings

    try:
        text = strings.render(
            "admin_alert", title=title, detail=detail, code=code or "-",
            time=time.strftime("%H:%M", time.gmtime()),
        )
        tg.send_message(cfg.admin_id, text)  # type: ignore[attr-defined]
    except Exception:
        return
