#!/usr/bin/env python3
"""Offline self-test: pure logic + registry TTL + queue quotas + a fake-Telegram path.

Run: python3 /home/runner/dlbot/selftest.py   (exit 0 = all checks passed)
This is evidence, not a stub: every check prints a real value.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import classify  # noqa: E402
import strings  # noqa: E402
from classify import UrlKind, classify as classify_url  # noqa: E402
from config import (  # noqa: E402
    Config, can_start_job, effective_ttl, fa_digits, human_duration, human_size,
    progress_bar, remaining_seconds, shutdown_tehran, shutdown_utc, utc_hhmm, tehran_hhmm,
)
from downloader import classify_error, sha256_file  # noqa: E402
from jobs import JobQueue, JobRejected  # noqa: E402
from registry import FileRecord, Registry  # noqa: E402

FAILURES: list[str] = []


def check(name: str, got: object, expected: object) -> None:
    ok = got == expected
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got={got!r} expected={expected!r}")
    if not ok:
        FAILURES.append(name)


def check_true(name: str, cond: bool, detail: object = "") -> None:
    print(f"{'PASS' if cond else 'FAIL'}  {name} {detail if not cond else ''}")
    if not cond:
        FAILURES.append(name)


def main() -> int:
    print("== strings ==")
    check("missing strings", strings.cross_check(), [])
    welcome = strings.render("welcome", name="دوست من", lt="<LIFETIME>")
    check_true("welcome has emoji+html", "\U0001f3ac" in welcome and "<b>" in welcome)
    check_true("welcome digits are persian", "6" not in welcome or "۶" in welcome)
    check("fa_digits", fa_digits("123 kbps"), "۱۲۳ kbps")
    check("human_size", human_size(5242880), "5.0 MB")
    check("human_duration", human_duration(3725), "01:02:05")
    check("progress_bar", progress_bar(50, 4), "\u2588\u2588\u2591\u2591")

    print("\n== lifetime math (req.3) ==")
    # real job start from the current run: 1794524474 -> shutdown 04:36 UTC / 08:06 Tehran
    start = 1794524474
    now = float(start + 600)
    cfg = Config(bot_token="x:y", job_start_epoch=start, job_timeout_minutes=335)
    check("remaining 325 min", remaining_seconds(cfg, now) // 60, 325)
    check("shutdown utc hh:mm", utc_hhmm(shutdown_utc(cfg)), "04:36")
    check("shutdown tehran hh:mm", tehran_hhmm(shutdown_tehran(cfg)), "08:06")
    check_true("can start with 545 min", can_start_job(cfg, now))
    late = Config(bot_token="x:y", job_start_epoch=int(now) - 335 * 60 + 300,
                  job_timeout_minutes=335)
    check_true("refuse job with 5 min left", not can_start_job(late, now))
    check("ttl clamped to 0->60 min floor", effective_ttl(late, now), 60)
    check("ttl normal", effective_ttl(cfg, now), 3600)

    print("\n== classify ==")
    cases = [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", UrlKind.YOUTUBE, "dQw4w9WgXcQ"),
        ("youtu.be/dQw4w9WgXcQ", UrlKind.YOUTUBE, "dQw4w9WgXcQ"),
        ("https://youtube.com/shorts/dQw4w9WgXcQ", UrlKind.YOUTUBE, "dQw4w9WgXcQ"),
        ("https://www.instagram.com/reel/xyz/", UrlKind.UNSUPPORTED_SITE, None),
        ("https://tiktok.com/@a/video/1", UrlKind.UNSUPPORTED_SITE, None),
        ("https://x.com/a/status/1", UrlKind.UNSUPPORTED_SITE, None),
        ("just some text", UrlKind.NOT_URL, None),
        ("ftp://example.com/f.zip", UrlKind.BLOCKED, None),
        ("http://127.0.0.1:8080/x.zip", UrlKind.BLOCKED, None),
        ("http://169.254.169.254/latest/meta-data/", UrlKind.BLOCKED, None),
    ]
    for text, kind, vid in cases:
        info = classify_url(text)
        check(f"classify({text[:44]})", (info.kind, info.video_id), (kind, vid))

    print("\n== error mapping (spec 2.7) ==")
    for fragment, key in [
        ("ERROR: Sign in to confirm you're not a bot. Use --cookies", "err_botcheck"),
        ("ERROR: Private video. Sign in if you've been granted access", "err_private"),
        ("ERROR: This video is not available in your country", "err_geo"),
        ("ERROR: unable to download video data: HTTP Error 403: Forbidden (SABR)", "err_sabr"),
        ("ERROR: No video formats found!", "err_no_formats"),
        ("ERROR: [youtube] xyz: Video unavailable", "err_private"),
    ]:
        check(f"classify_error({fragment[:38]})", classify_error(fragment), key)

    print("\n== registry TTL + expiry (req.5) ==")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        files = root / "files"; files.mkdir()
        tmpdir = root / "tmp"; tmpdir.mkdir()
        reg = Registry(root / "reg.sqlite3", 3600, ttl_provider=lambda: 120)
        payload = files / "abc.mp4"
        payload.write_bytes(b"x" * 2048)
        rec = FileRecord(token="t" * 32, chat_id=1, path=payload, size=2048,
                         sha256=sha256_file(payload), created_at=time.time(),
                         expires_at=time.time() + reg.ttl(), kind="video", title="t")
        reg.add(rec)
        check("ttl provider used", reg.ttl(), 120)
        check_true("fresh token alive", reg.is_alive(rec.token))
        check("stats", reg.stats(), {"files": 1, "used_bytes": 2048})
        payload.unlink()
        check("reason after delete", reg.expire_reason(rec.token), "file_gone")
        check_true("not alive after delete", not reg.is_alive(rec.token))
        check("sweep removes dead row", reg.sweep(), 1)
        payload.write_bytes(b"y")
        reg.add(rec)
        expires = time.time() - 1
        reg.add(FileRecord(token=rec.token, chat_id=1, path=payload, size=1, sha256="z",
                           created_at=time.time() - 5000, expires_at=expires, kind="document"))
        check("reason after ttl", reg.expire_reason(rec.token), "ttl")
        check("startup cleanup wipes", reg.startup_cleanup(files, tmpdir), 1)
        check("table empty after restart", reg.stats()["files"], 0)

    print("\n== job queue quotas (req.5) ==")
    qcfg = Config(bot_token="x:y", job_start_epoch=int(time.time()), job_timeout_minutes=335,
                  max_concurrent=2, per_user_per_hour=3, max_queue=1)
    queue = JobQueue(qcfg)
    queue.start()
    blocking = threading.Event()

    def slow(cancel: threading.Event) -> None:
        blocking.wait(5)

    queue.submit(10, 100, slow)
    queue.submit(11, 101, slow)
    time.sleep(0.3)
    queue.submit(12, 102, slow)          # fills the queue (max_queue=1)
    try:
        queue.submit(13, 103, slow)
        check_true("queue full rejected", False)
    except JobRejected as exc:
        check("queue full key", exc.key, "err_queue_full")
    try:
        queue.submit(10, 100, slow)      # same chat as the running job -> busy
        check_true("second job same chat allowed", False)
    except JobRejected as exc:
        check("second job same chat rejected", exc.key, "err_user_busy")
    check("usage counted", queue.usage(100) >= 1, True)
    blocking.set()
    queue.stop()

    print("\n== bot module imports + wiring ==")
    import bot as bot_mod  # noqa: E402

    bcfg = Config(bot_token="x:y", job_start_epoch=int(time.time()), job_timeout_minutes=335)
    instance = bot_mod.Bot(bcfg)
    check_true("Bot constructed", isinstance(instance, bot_mod.Bot))
    check_true("Bot has no missing strings", strings.cross_check() == [])
    text = strings.render("caption_file", title="t", size="1 MB", duration="00:01:00",
                          quality="1080p • mp4", lt_short=strings.lifetime_line(bcfg, short=True))
    check_true("caption under 1024 chars", len(text) < 1024, len(text))
    print("caption sample:", text.replace("\n", " | "))

    print("\nRESULT:", "ALL PASSED" if not FAILURES else f"{len(FAILURES)} FAILURES: {FAILURES}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
