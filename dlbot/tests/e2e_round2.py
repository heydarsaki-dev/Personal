#!/usr/bin/env python3
"""Round 2 of the live drive: commands, cookies-missing, over-cap limit, /kill."""
from __future__ import annotations

import os
import sys
import time
from dataclasses import replace
from pathlib import Path

DLBOT = Path("/home/runner/.hermes/company/work/personal/dlbot")
sys.path.insert(0, str(DLBOT))

os.environ.setdefault("JOB_START_EPOCH", str(int(time.time())))
os.environ.setdefault("JOB_TIMEOUT_MINUTES", "335")

from bot import Bot  # noqa: E402
from config import load_config  # noqa: E402

CHAT = 180329538
BIG = "https://proof.ovh.net/files/10Mb.dat"
BIG_ALT = "https://ftp.gnu.org/gnu/gzip/gzip-1.13.tar.gz"


def drive(bot: Bot, texts: list[str]) -> None:
    orig = bot.tg.send_message

    def spy(chat_id, text, **kw):
        result = orig(chat_id, text, **kw)
        texts.append(text)
        print(f"   [out] {text[:130].replace(chr(10), ' | ')}", flush=True)
        return result

    bot.tg.send_message = spy


def send(bot: Bot, texts: list[str], text: str, *, user: int = CHAT) -> str:
    mark = len(texts)
    bot.on_message({"message_id": 1, "chat": {"id": CHAT, "type": "private"},
                    "from": {"id": user, "is_bot": False}, "text": text})
    deadline = time.time() + 60
    while len(texts) <= mark and time.time() < deadline:
        time.sleep(1)
    return texts[-1] if len(texts) > mark else "<no reply>"


def main() -> int:
    base = load_config()
    failures: list[str] = []

    print("=== A) commands ===")
    bot = Bot(base)
    for d in (base.root, base.files_dir, base.tmp_dir, base.logs_dir):
        d.mkdir(parents=True, exist_ok=True)
    bot.reg.startup_cleanup(base.files_dir, base.tmp_dir)
    bot.jobs.start()
    texts: list[str] = []
    drive(bot, texts)

    for label, payload in [("/help", "/help"), ("/status", "/status"), ("/cancel", "/cancel")]:
        reply = send(bot, texts, payload)
        print(f"   {label:8s} -> {reply[:70].replace(chr(10), ' | ')}")
        if reply == "<no reply>":
            failures.append(label)
    if "yt-dlp" not in texts[-2] and "نسخه" not in " ".join(texts):
        failures.append("/status missing yt-dlp version")
    print("   status shows ytdlp:", [t for t in texts if "yt-dlp" in t][0][:0] or "yes"
          if any("yt-dlp" in t for t in texts) else "NO")

    reply = send(bot, texts, "/kill", user=999)
    print(f"   /kill as stranger -> {reply[:70].replace(chr(10), ' | ')}")
    if "فقط برای مدیر" not in reply:
        failures.append("/kill not refused for stranger")
    reply = send(bot, texts, "/kill", user=CHAT)
    print(f"   /kill as admin     -> {reply[:70].replace(chr(10), ' | ')}")
    if "متوقف" not in reply or not bot.stop.is_set():
        failures.append("admin /kill did not stop the bot")
    bot.jobs.stop()

    print("\n=== B) cookies missing (requirement 6) ===")
    bot_b = Bot(replace(base, cookies=Path("/nonexistent/cookies.txt")))
    bot_b.jobs.start()
    texts_b: list[str] = []
    drive(bot_b, texts_b)
    reply = send(bot_b, texts_b, "https://www.youtube.com/watch?v=jNQXAC9IVRw")
    print(f"   youtube link -> {reply[:120].replace(chr(10), ' | ')}")
    if "کوکی" not in reply:
        failures.append("cookies-missing message wrong")
    bot_b.jobs.stop()

    print("\n=== C) over-cap direct link (requirement 6) ===")
    bot_c = Bot(replace(base, max_file_mb=1))
    bot_c.jobs.start()
    texts_c: list[str] = []
    drive(bot_c, texts_c)
    reply = send(bot_c, texts_c, BIG)
    if "بزرگ‌تر" not in reply and "ناموفق" in reply:
        print("   primary test host failed, retrying with a mirror")
        reply = send(bot_c, texts_c, BIG_ALT)
    print(f"   10 MB file with a 1 MB cap -> {reply[:120].replace(chr(10), ' | ')}")
    if "بزرگ" not in reply:
        failures.append("over-cap message wrong")
    bot_c.jobs.stop()

    print("\nRESULT:", "ALL ROUND-2 CHECKS PASSED" if not failures else f"FAILURES: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
