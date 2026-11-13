#!/usr/bin/env python3
"""Round 4: the video path through the picker, and the SABR/403 case (spec E7)."""
from __future__ import annotations

import hashlib
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
from config import load_config, human_size  # noqa: E402
from downloader import sha256_file  # noqa: E402

CHAT = 180329538
VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"   # 19 s, 240p
SABR = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"      # spec E7: SABR-only, 403


def wait_for(pred, timeout, what, poll=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(poll)
    print(f"   !! timeout after {timeout:.0f}s waiting for {what}")
    return False


def run_case(bot: Bot, url: str, callback: str, label: str, sent: list, texts: list,
             timeout: float = 300) -> list[str]:
    failures: list[str] = []
    print(f"\n=== {label} ===")
    mark = len(texts)
    bot.on_message({"message_id": 1, "chat": {"id": CHAT, "type": "private"},
                    "from": {"id": CHAT, "is_bot": False}, "text": url})
    if not wait_for(lambda: bot.states.get(CHAT) and bot.states[CHAT].stage == "await_format",
                    240, f"{label} probe"):
        return [f"{label}: probe"]
    st = bot.states[CHAT]
    print(f"   probe: {st.info.title!r} duration={st.info.duration_s}s "
          f"hint={human_size(st.info.filesize_hint)}")
    before = len(sent)
    bot.on_callback({"id": "cb", "from": {"id": CHAT}, "data": callback.format(token=st.token),
                     "message": {"message_id": 50, "chat": {"id": CHAT}}})
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(sent) > before:
            break
        if not bot.jobs.is_busy(CHAT) and len(texts) > mark and time.time() > deadline - timeout + 30:
            break          # job finished without delivering -> error message
        time.sleep(2)
    if len(sent) > before:
        item = sent[-1]
        path = Path(item["path"])
        doc = item["result"].get("video") or item["result"].get("audio") or \
            item["result"].get("document") or {}
        print(f"   DELIVERED: {path.name} {path.stat().st_size} bytes "
              f"message_id={item['result'].get('message_id')} api_size={doc.get('file_size')}")
        print(f"   sha256: {sha256_file(path)}")
        print(f"   caption: {item['caption'][:150].replace(chr(10), ' | ')}")
        if doc.get("file_size") and int(doc["file_size"]) != path.stat().st_size:
            failures.append(f"{label}: api size mismatch")
    else:
        print(f"   no delivery; last bot texts:")
        for t in texts[mark:]:
            print("     -", t[:120].replace("\n", " | "))
        failures.append(f"{label}: no delivery")
    return failures


def main() -> int:
    base = load_config()
    bot = Bot(base)
    for d in (base.root, base.files_dir, base.tmp_dir, base.logs_dir):
        d.mkdir(parents=True, exist_ok=True)
    bot.reg.startup_cleanup(base.files_dir, base.tmp_dir)
    bot.jobs.start()

    sent: list[dict] = []
    texts: list[str] = []
    orig_send_file = bot.tg.send_file
    orig_send_message = bot.tg.send_message

    def spy_file(chat_id, path, **kw):
        result = orig_send_file(chat_id, path, **kw)
        sent.append({"path": str(path), "result": result, "caption": kw.get("caption", "")})
        return result

    def spy_message(chat_id, text, **kw):
        result = orig_send_message(chat_id, text, **kw)
        texts.append(text)
        print(f"   [out] {text[:110].replace(chr(10), ' | ')}", flush=True)
        return result

    bot.tg.send_file = spy_file
    bot.tg.send_message = spy_message

    failures: list[str] = []
    failures += run_case(bot, VIDEO, "vid:best:{token}", "video path (mp4 through the picker)",
                         sent, texts, timeout=360)
    print("\n   waiting for the queue to go idle before the next case...")
    while bot.jobs.is_busy(CHAT):
        time.sleep(3)
    failures += run_case(bot, SABR, "vid:best:{token}", "SABR-only rendition (spec E7)",
                         sent, texts, timeout=300)

    print("\nRESULT:", "ALL ROUND-4 CHECKS PASSED" if not failures else f"FAILURES: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
