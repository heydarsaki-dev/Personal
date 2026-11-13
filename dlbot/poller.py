"""Single-instance getUpdates long-poll loop (spec section 4.3).

Cross-platform single-instance guard: an advisory ``flock`` on POSIX, an
``msvcrt`` byte lock on Windows (both fall back to a telemetry-style "no lock
mechanism" -> proceed).  Stale pollers of the same bot are killed first, because
two concurrent ``getUpdates`` loops make Telegram return 409 conflicts.
"""
from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from config import Config
from tg import TelegramClient, TelegramError

IS_WINDOWS = os.name == "nt"


class Poller:
    def __init__(self, cfg: Config, tg: TelegramClient,
                 on_update: Callable[[dict[str, Any]], None]) -> None:
        self.cfg = cfg
        self.tg = tg
        self.on_update = on_update
        self.offset: int | None = None
        self.first = True
        self._lock_path = cfg.root / "bot.lock"
        self._lock_file: Any = None

    # ---- single instance (R9) ------------------------------------------
    @staticmethod
    def kill_previous_pollers() -> int:
        """Kill other pollers of this bot: two pollers -> Telegram 409 conflicts."""
        me = os.getpid()
        sibling = Path(__file__).resolve().parent.as_posix()
        killed = 0
        if IS_WINDOWS:
            killed = _kill_windows_pollers(me)
            return killed
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
            if "bot.py" in cmdline and ("dlbot" in cmdline or sibling in cmdline):
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGTERM)
                    killed += 1
        return killed

    def acquire_lock(self) -> bool:
        """Exclusive lock file next to the bot root (flock / msvcrt)."""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import fcntl
        except ImportError:
            fcntl = None  # type: ignore[assignment]
        if fcntl is not None:
            self._lock_file = open(self._lock_path, "w")
            try:
                fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return False
            self._lock_file.write(str(os.getpid()))
            self._lock_file.flush()
            return True
        try:
            import msvcrt
            self._lock_file = open(self._lock_path, "a+")
            self._lock_file.seek(0)
            msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            self._lock_file.seek(0)
            self._lock_file.truncate()
            self._lock_file.write(str(os.getpid()))
            self._lock_file.flush()
            return True
        except Exception:
            # No usable advisory lock (unusual): carry on, the stale-poller
            # sweep above is the real guard against 409 conflicts.
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


def _kill_windows_pollers(me: int) -> int:
    """Find python processes running a dlbot bot.py via CIM and taskkill them."""
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
        "Where-Object { $_.CommandLine -like '*bot.py*' -and $_.CommandLine -like '*dlbot*' } | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    killed = 0
    for token in out.split():
        if not token.isdigit():
            continue
        pid = int(token)
        if pid == me:
            continue
        with contextlib.suppress(Exception):
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=15)
            killed += 1
    return killed
