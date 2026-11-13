"""Single-instance getUpdates long-poll loop (spec section 4.3)."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from config import Config
from tg import TelegramClient, TelegramError


class Poller:
    def __init__(self, cfg: Config, tg: TelegramClient,
                 on_update: Callable[[dict[str, Any]], None]) -> None:
        self.cfg = cfg
        self.tg = tg
        self.on_update = on_update
        self.offset: int | None = None
        self.first = True
        self._lock_path = cfg.root / "bot.lock"

    # ---- single instance (R9) ------------------------------------------
    @staticmethod
    def kill_previous_pollers() -> int:
        """Kill other pollers of this bot: two pollers -> Telegram 409 conflicts."""
        me = os.getpid()
        killed = 0
        proc_root = Path("/proc")
        if not proc_root.exists():
            return killed
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            if pid == me:
                continue
            try:
                cmdline = (entry / "cmdline").read_bytes().decode("utf-8", "replace").replace("\0", " ")
            except OSError:
                continue
            if "dlbot/bot.py" in cmdline or "/home/runner/dlbot/bot.py" in cmdline:
                try:
                    os.kill(pid, signal.SIGTERM)
                    killed += 1
                except OSError:
                    continue
        return killed

    def acquire_lock(self) -> bool:
        """Prevent two pollers in the same container (advisory flock)."""
        try:
            import fcntl
        except ImportError:
            return True
        self._lock_file = open(self._lock_path, "w")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        self._lock_file.write(str(os.getpid()))
        self._lock_file.flush()
        return True

    # ---- loop ----------------------------------------------------------
    def run_forever(self, stop: threading.Event) -> None:
        backoff = 1
        while not stop.is_set():
            try:
                updates = self.tg.get_updates(
                    self.offset, timeout=25, drop_pending=self.first
                )
                self.first = False
                backoff = 1
            except TelegramError:
                if stop.wait(backoff):
                    break
                backoff = min(backoff * 2, 30)
                continue
            except Exception:
                if stop.wait(backoff):
                    break
                backoff = min(backoff * 2, 30)
                continue
            for upd in updates:
                update_id = int(upd.get("update_id", 0))
                self.offset = update_id + 1
                try:
                    self.on_update(upd)
                except Exception:
                    continue
