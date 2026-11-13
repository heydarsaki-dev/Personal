#!/usr/bin/env python3
"""Round 3: the two round-2 checks re-done with correct detection."""
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
from downloader import YtDlpError, download_direct  # noqa: E402

CHAT = 180329538
BIG = "https://proof.ovh.net/files/10Mb.dat"


def main() -> int:
    failures: list[str] = []
    base = load_config()

    print("=== A) /kill authorisation (stranger vs admin) ===")
    bot = Bot(base)
    seen: list[str] = []
    bot.tg.send_message = lambda chat_id, text, **kw: (seen.append(text), {"message_id": 1})[1]
    bot.on_command(chat_id=CHAT, user_id=999, text="/kill")
    print("   stranger /kill ->", seen[-1][:70].replace("\n", " | "))
    if "فقط برای مدیر" not in seen[-1]:
        failures.append("stranger /kill not refused")
    print("   stop flag after stranger:", bot.stop.is_set())
    if bot.stop.is_set():
        failures.append("stranger stopped the bot")
    bot.on_command(chat_id=CHAT, user_id=CHAT, text="/kill")
    print("   admin /kill    ->", seen[-1][:70].replace("\n", " | "))
    if not bot.stop.is_set():
        failures.append("admin /kill did not stop the bot")

    print("\n=== B) over-cap direct link: real curl --max-filesize ===")
    root = Path("/home/runner/dlbot")
    cfg = replace(base, max_file_mb=1)
    t0 = time.time()
    try:
        result = download_direct(BIG, cfg)
        failures.append(f"over-cap download was NOT rejected: {result.size} bytes")
        print(f"   !! downloaded {result.size} bytes - cap not enforced")
    except YtDlpError as exc:
        print(f"   YtDlpError key={exc.key} detail={exc.detail[:120]!r} "
              f"after {time.time() - t0:.1f}s")
        if exc.key != "err_too_big":
            failures.append(f"over-cap key {exc.key}")
        else:
            print("   Persian message:",
                  __import__("strings").render(exc.key, size="۱۰ MB", max_mb=1).replace("\n", " | "))

    print("\nRESULT:", "ALL ROUND-3 CHECKS PASSED" if not failures else f"FAILURES: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
