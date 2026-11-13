#!/usr/bin/env python3
"""End-to-end drive of the REAL bot code against the REAL chat 180329538.

Inbound updates are synthesised (nobody can type as the CEO's account); every
downstream step is real: yt-dlp with the cookies, curl, ffmpeg, the local Bot
API server on 127.0.0.1:8081, and delivery into the Telegram chat. The file that
Telegram holds is then fetched back and its sha256 compared with the local file.
"""
from __future__ import annotations

import hashlib
import os
import sys
import time
import urllib.request
from pathlib import Path

DLBOT = Path("/home/runner/.hermes/company/work/personal/dlbot")
sys.path.insert(0, str(DLBOT))

os.environ.setdefault("JOB_START_EPOCH", str(int(time.time())))
os.environ.setdefault("JOB_TIMEOUT_MINUTES", "335")

from bot import Bot  # noqa: E402
from config import load_config, remaining_seconds, human_size  # noqa: E402
from downloader import ffprobe_stream, sha256_file  # noqa: E402

CHAT = 180329538
DIRECT_URL = "https://raw.githubusercontent.com/torvalds/linux/master/README"
YT_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"   # "Me at the zoo" (19 s)


def wait_for(pred, timeout: float, what: str, poll: float = 2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = pred()
        if value:
            return value
        time.sleep(poll)
    print(f"!! timeout waiting for {what} ({timeout:.0f}s)")
    return None


def fetch_back(tg, cfg, file_id: str) -> tuple[str, bytes]:
    info = tg.call("getFile", {"file_id": file_id})["result"]
    path = info.get("file_path", "")
    url = f"{cfg.api_root}/file/bot{cfg.bot_token}/{path}"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            return path, resp.read()
    except Exception as exc:
        print(f"   http fetch failed ({exc}); reading {path} from disk")
        return path, Path(path).read_bytes()


def main() -> int:
    cfg = load_config()
    print(f"api_root={cfg.api_root} root={cfg.root} "
          f"remaining={remaining_seconds(cfg) // 60} min")
    bot = Bot(cfg)
    for d in (cfg.root, cfg.files_dir, cfg.tmp_dir, cfg.logs_dir):
        d.mkdir(parents=True, exist_ok=True)
    bot.reg.startup_cleanup(cfg.files_dir, cfg.tmp_dir)
    bot.jobs.start()

    sent: list[dict] = []
    texts: list[str] = []
    orig_send_file = bot.tg.send_file

    def spy_send_file(chat_id, path, **kw):
        result = orig_send_file(chat_id, path, **kw)
        sent.append({"path": str(path), "result": result, "caption": kw.get("caption", ""),
                     "title": kw.get("title", "")})
        return result

    bot.tg.send_file = spy_send_file
    orig_send_message = bot.tg.send_message

    def spy_send_message(chat_id, text, **kw):
        result = orig_send_message(chat_id, text, **kw)
        texts.append(text)
        print(f"[out->chat] {text[:150].replace(chr(10), ' | ')}", flush=True)
        return result

    bot.tg.send_message = spy_send_message

    tokens: list[str] = []
    orig_add = bot.reg.add

    def spy_add(rec):
        orig_add(rec)
        tokens.append(rec.token)

    bot.reg.add = spy_add

    def update(text: str) -> None:
        bot.on_message({"message_id": 1, "chat": {"id": CHAT, "type": "private"},
                        "from": {"id": CHAT, "is_bot": False}, "text": text})

    failures: list[str] = []

    print("\n=== 1) /start ===")
    update("/start")
    print("welcome contains lifetime line:", any("زمان باقی" in t for t in texts))

    print("\n=== 2) direct link: document delivery (requirement 2) ===")
    before = len(sent)
    update(DIRECT_URL)
    got = wait_for(lambda: len(sent) > before, 120, "direct delivery")
    if not got:
        failures.append("direct delivery")
    else:
        item = sent[-1]
        local = Path(item["path"])
        doc = (item["result"].get("document") or {})
        print(f"   sent: name={doc.get('file_name')} size={doc.get('file_size')} "
              f"message_id={item['result'].get('message_id')}")
        print(f"   local sha256: {sha256_file(local)}")
        fp, data = fetch_back(bot.tg, cfg, doc.get("file_id", ""))
        print(f"   fetched back from Telegram: {fp} bytes={len(data)}")
        print(f"   telegram sha256: {hashlib.sha256(data).hexdigest()}")
        if hashlib.sha256(data).hexdigest() != sha256_file(local):
            failures.append("direct sha256 mismatch")
        print("   caption:", item["caption"][:160].replace("\n", " | "))

    print("\n=== 3) YouTube: format picker (requirement 2) ===")
    update(YT_URL)
    state = wait_for(lambda: bot.states.get(CHAT) and bot.states[CHAT].stage == "await_format",
                     240, "yt-dlp probe + picker")
    if not state:
        failures.append("youtube probe")
        print("   bot texts so far:", [t[:60] for t in texts[-2:]])
    else:
        st = bot.states[CHAT]
        print(f"   probe ok: title={st.info.title!r} uploader={st.info.uploader!r} "
              f"duration={st.info.duration_s} hint={human_size(st.info.filesize_hint)}")
        print(f"   picker text: {texts[-1][:200].replace(chr(10), ' | ')}")
        print("   token:", st.token)

        print("\n=== 4) audio mp3 128k via callback (requirement 2) ===")
        before = len(sent)
        bot.on_callback({"id": "cb1", "from": {"id": CHAT},
                         "data": f"aud:mp3:128:{st.token}",
                         "message": {"message_id": 99, "chat": {"id": CHAT}}})
        got = wait_for(lambda: len(sent) > before, 420, "mp3 delivery")
        if not got:
            failures.append("mp3 delivery")
        else:
            item = sent[-1]
            local = Path(item["path"])
            audio = item["result"].get("audio") or item["result"].get("document") or {}
            probe = ffprobe_stream(local, cfg)
            print(f"   sent: {local.name} size={local.stat().st_size} "
                  f"message_id={item['result'].get('message_id')}")
            print(f"   ffprobe: {probe}")
            print(f"   local sha256: {sha256_file(local)}")
            fp, data = fetch_back(bot.tg, cfg, audio.get("file_id", ""))
            print(f"   fetched back: bytes={len(data)} sha256={hashlib.sha256(data).hexdigest()}")
            if hashlib.sha256(data).hexdigest() != sha256_file(local):
                failures.append("mp3 sha256 mismatch")
            if probe.get("codec_name") != "mp3" or probe.get("bit_rate") != "128000":
                failures.append(f"mp3 probe wrong: {probe}")
            print("   caption:", item["caption"][:160].replace("\n", " | "))

    print("\n=== 5) failure matrix (requirement 6) ===")
    for label, payload in [
        ("instagram", "https://www.instagram.com/reel/Cxyz123/"),
        ("not a url", "سلام"),
        ("over-cap-ish bogus host", "http://127.0.0.1/secret.zip"),
        ("bad scheme", "ftp://example.com/f.zip"),
    ]:
        mark = len(texts)
        update(payload)
        wait_for(lambda: len(texts) > mark, 20, f"reply to {label}", poll=1)
        print(f"   {label:24s} -> {texts[-1][:90].replace(chr(10), ' | ') if len(texts) > mark else 'NO REPLY'}")
        if len(texts) <= mark:
            failures.append(f"matrix:{label}")

    print("\n=== 6) expiry: delete the file, then press ارسال مجدد ===")
    if sent and tokens:
        stats = bot.reg.stats()
        print("   registry:", stats)
        row = bot.reg.get(tokens[-1])
        if row is not None:
            row.path.unlink()
            print(f"   deleted {row.path}; reason now: {bot.reg.expire_reason(row.token)}")
            mark = len(texts)
            bot.on_callback({"id": "cb2", "from": {"id": CHAT}, "data": row.token,
                             "message": {"message_id": 100, "chat": {"id": CHAT}}})
            wait_for(lambda: len(texts) > mark, 20, "expired reply", poll=1)
            if len(texts) > mark:
                print("   reply:", texts[-1][:120].replace("\n", " | "))
            else:
                failures.append("expiry reply")

    print("\nRESULT:", "ALL LIVE CHECKS PASSED" if not failures else f"FAILURES: {failures}")
    return 1 if failures else 0


def _last_token(bot) -> str:
    tokens = [str(rec_token) for rec_token in getattr(bot, "_tokens", [])]
    return tokens[-1] if tokens else ""


if __name__ == "__main__":
    raise SystemExit(main())
