"""Job queue: concurrency cap, per-user hourly quota, cancel, per-chat state (spec 4.8)."""
from __future__ import annotations

import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from config import Config
from downloader import MediaInfo


class JobRejected(RuntimeError):
    """key is a strings.py key, e.g. err_rate_limit / err_user_busy / err_queue_full."""

    def __init__(self, key: str, **kw: Any) -> None:
        super().__init__(key)
        self.key = key
        self.kw = kw


@dataclass
class ChatState:
    """Per-chat, in memory only: a restart wipes every pending picker (D2)."""

    token: str | None = None
    stage: Literal["idle", "await_format", "await_audio", "await_video_choice"] = "idle"
    url: str | None = None
    info: MediaInfo | None = None
    job_id: str | None = None
    progress_message_id: int | None = None

    def reset(self) -> None:
        self.token = None
        self.stage = "idle"
        self.url = None
        self.info = None
        self.job_id = None
        self.progress_message_id = None


@dataclass
class Job:
    job_id: str
    chat_id: int
    user_id: int
    run: Callable[[threading.Event], None]
    cancel: threading.Event = field(default_factory=threading.Event)
    created_at: float = field(default_factory=time.time)


class JobQueue:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._lock = threading.RLock()
        self._queue: deque[Job] = deque()
        self._running: dict[str, Job] = {}
        self._active_chats: set[int] = set()
        self._history: dict[int, deque[float]] = defaultdict(deque)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ---- lifecycle -----------------------------------------------------
    def start(self) -> None:
        for index in range(self.cfg.max_concurrent):
            thread = threading.Thread(target=self._worker, name=f"job-{index}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            jobs = list(self._running.values())
        for job in jobs:
            job.cancel.set()

    # ---- quotas --------------------------------------------------------
    def _usage(self, user_id: int) -> int:
        window = self._history[user_id]
        cutoff = time.time() - 3600
        while window and window[0] < cutoff:
            window.popleft()
        return len(window)

    def usage(self, user_id: int) -> int:
        with self._lock:
            return self._usage(user_id)

    def retry_in_minutes(self, user_id: int) -> int:
        with self._lock:
            window = self._history[user_id]
            if not window:
                return 0
            return max(1, int((window[0] + 3600 - time.time()) // 60) + 1)

    def active_count(self) -> int:
        with self._lock:
            return len(self._running)

    def queued_count(self) -> int:
        with self._lock:
            return len(self._queue)

    def is_busy(self, chat_id: int) -> bool:
        with self._lock:
            return chat_id in self._active_chats

    # ---- submission ----------------------------------------------------
    def submit(self, chat_id: int, user_id: int, run: Callable[[threading.Event], None]) -> str:
        from config import can_start_job

        if not can_start_job(self.cfg):
            raise JobRejected("err_no_time", mm=(__import__("config").remaining_seconds(self.cfg) // 60))
        with self._lock:
            if self._usage(user_id) >= self.cfg.per_user_per_hour:
                raise JobRejected("err_rate_limit", per_hour=self.cfg.per_user_per_hour,
                                  retry_in=self.retry_in_minutes(user_id))
            if chat_id in self._active_chats:
                raise JobRejected("err_user_busy")
            if len(self._running) >= self.cfg.max_concurrent and len(self._queue) >= self.cfg.max_queue:
                raise JobRejected("err_queue_full", concurrent=self.cfg.max_concurrent,
                                  queued=len(self._queue))
            job = Job(job_id=uuid.uuid4().hex[:12], chat_id=chat_id, user_id=user_id, run=run)
            self._active_chats.add(chat_id)
            self._history[user_id].append(time.time())
            self._queue.append(job)
            position = len(self._queue) + len(self._running)
        self._wake.set()
        return str(position)

    def cancel_chat(self, chat_id: int) -> bool:
        found = False
        with self._lock:
            for job in list(self._queue):
                if job.chat_id == chat_id:
                    self._queue.remove(job)
                    self._active_chats.discard(chat_id)
                    found = True
            for job in self._running.values():
                if job.chat_id == chat_id:
                    job.cancel.set()
                    found = True
        return found

    # ---- worker --------------------------------------------------------
    def _worker(self) -> None:
        while not self._stop.is_set():
            job: Job | None = None
            with self._lock:
                if self._queue:
                    job = self._queue.popleft()
                    self._running[job.job_id] = job
            if job is None:
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            try:
                job.run(job.cancel)
            except Exception:
                pass
            finally:
                with self._lock:
                    self._running.pop(job.job_id, None)
                    self._active_chats.discard(job.chat_id)
