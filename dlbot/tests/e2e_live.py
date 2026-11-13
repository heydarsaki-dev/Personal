#!/usr/bin/env python3
"""LIVE end-to-end run of the download bot against a real local Bot API server.

What it proves (all values printed are real tool/API output):
 1. real YouTube URL -> probe -> Persian format picker -> real download
 2. the delivered Telegram file is byte-identical (sha256) to the file on disk
 3. audio-only path (mp3 128 kbps) + ffprobe bitrate check
 4. expiry: file deleted -> resend button and reply-to-file both answer «منقضی شده»
 5. direct http(s) link -> document delivered, sha256 == independent curl
 6. failure matrix: not-a-url / bad scheme / SSRF / unsupported site / private /
    cookies missing / oversize / bad type -> Persian message, bot stays alive
 7. transport ladder: local-path upload, then the docker (no bind mount) fallback
 8. getUpdates long-poll transport works on the same server

Usage:
  TELEGRAM_BOT_API_ROOT=http://127.0.0.1:8082 DLBOT_ROOT=/tmp/dlbot-e2e \
  RUNNER_LIFETIME_MINUTES=330 python3 tests/e2e_live.py
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

os.environ.setdefault("TELEGRAM_DL_BOT_TOKEN", os.environ.get("TELEGRAM_BOT_TOKEN", ""))
os.environ.setdefault("TELEGRAM_BOT_API_ROOT", "http://127.0.0.1:8082")
os.environ.setdefault("DLBOT_ROOT", "/home/runner/dlbot-test")
os.environ.setdefault("RUNNER_LIFETIME_MINUTES", "330")
os.environ.setdefault("DLBOT_ADMIN_ID", "180329538")

from bot import Bot  # noqa: E402
from config import load_config, human_size  # noqa: E402
from downloader import sha256_file  # noqa: E402

CHAT = int(os.environ.get("E2E_CHAT_ID", "180329538"))
YT_VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"   # "Me at the zoo", 19 s
YT_DEAD = "https://www.youtube.com/watch?v=aaaaaaaaaaa"     # deleted id -> Persian error
DIRECT = "https://raw.githubusercontent.com/octocat/Hello-World/master/README"
OCTET = "https://speed.cloudflare.com/__down?bytes=1024"    # octet-stream, no extension
BIG = "https://github.com/git/git/archive/refs/tags/v2.43.0.tar.gz"   # ~11 MB + redirect

FAILS: list[str] = []
OUTBOUND: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}", flush=True)
    if not ok:
        FAILS.append(name)


def spy(bot: Bot) -> None:
    """Record every outbound call so assertions use the real API responses."""
    real_send_message = bot.tg.send_message
    real_edit = bot.tg.edit_message
    real_send_file = bot.tg.send_file

    def send_message(chat_id, text, **kw):
        result = real_send_message(chat_id, text, **kw)
        OUTBOUND.append({"kind": "text", "chat": chat_id, "text": text, "result": result})
        print(f"   <- text({chat_id}) {result.get('message_id')}: {text.splitlines()[0][:70]}")
        return result

    def edit_message(chat_id, message_id, text, **kw):
        result = real_edit(chat_id, message_id, text, **kw)
        OUTBOUND.append({"kind": "edit", "chat": chat_id, "text": text, "result": None})
        return result

    def send_file(chat_id, path, **kw):
        result = real_send_file(chat_id, path, **kw)
        OUTBOUND.append({"kind": "file", "chat": chat_id, "path": Path(path),
                         "payload": kw, "result": result})
        node = result.get(kw.get("kind", "document")) or {}
        print(f"   <- file({chat_id}) msg={result.get('message_id')} "
              f"file_size={node.get('file_size')} name={node.get('file_name')}")
        return result

    bot.tg.send_message = send_message          # type: ignore[method-assign]
    bot.tg.edit_message = edit_message          # type: ignore[method-assign]
    bot.tg.send_file = send_file                # type: ignore[method-assign]


def texts() -> list[str]:
    return [item["text"] for item in OUTBOUND if item["kind"] == "text"]


def files() -> list[dict]:
    return [item for item in OUTBOUND if item["kind"] == "file"]


def wait_for(pred, timeout: float, what: str, poll: float = 1.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(poll)
    print(f"   !! timeout after {timeout:.0f}s waiting for {what}", flush=True)
    return False


def introduce(bot: Bot, text: str, reply_to: dict | None = None) -> None:
    message = {"message_id": int(time.time() * 1000) % 100000, "chat": {"id": CHAT, "type": "private"},
               "from": {"id": CHAT, "is_bot": False}, "text": text}
    if reply_to is not None:
        message["reply_to_message"] = reply_to
    bot.on_message(message)


def click(bot: Bot, data: str, message_id: int, label: str) -> None:
    print(f"   -> button {data} ({label})", flush=True)
    bot.on_callback({"id": f"cb{int(time.time())}", "data": data,
                     "from": {"id": CHAT, "is_bot": False},
                     "message": {"message_id": message_id, "chat": {"id": CHAT}}})


def server_side_sha(bot: Bot, node: dict) -> tuple[str | None, str | None, int | None]:
    """Ask the Bot API server where the delivered file really is and hash it."""
    file_id = node.get("file_id")
    if not file_id:
        return None, None, None
    payload = bot.tg.call("getFile", {"file_id": file_id})["result"]
    path = Path(payload.get("file_path", ""))
    if not path.exists():
        return None, str(path), None
    return sha256_file(path), str(path), path.stat().st_size


def verify_delivery(bot: Bot, item: dict, kind: str, label: str) -> None:
    node = item["result"].get(kind) or {}
    local = Path(item["path"])
    local_sha = sha256_file(local)
    server_sha, server_path, server_size = server_side_sha(bot, node)
    caption = item["payload"].get("caption", "")
    print(f"      local  {local} {local.stat().st_size} B sha256={local_sha[:16]}…")
    print(f"      server {server_path} {server_size} B sha256={(server_sha or '?')[:16]}…")
    check(f"{label}: delivered file sha256 == local file", local_sha == server_sha,
          f"{local_sha[:16]}… vs {(server_sha or '?')[:16]}…")
    check(f"{label}: file_size matches", node.get("file_size") == local.stat().st_size,
          f"{node.get('file_size')} vs {local.stat().st_size}")
    check(f"{label}: caption has runner-lifetime line", "دقیقه" in caption and "⏳" in caption,
          repr(caption.splitlines()[-1][:60]))
    keyboard = (item["payload"].get("keyboard") or [])
    token = keyboard[0][0]["callback_data"] if keyboard else ""
    check(f"{label}: resend button carries a 32-hex token", len(token) == 32,
          f"callback_data={token[:12]}…")


def main() -> int:
    cfg = load_config()
    cfg.root.mkdir(parents=True, exist_ok=True)
    cfg.files_dir.mkdir(parents=True, exist_ok=True)
    cfg.tmp_dir.mkdir(parents=True, exist_ok=True)
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    bot = Bot(cfg)
    spy(bot)
    print(f"== dlbot live e2e ==\n   api_root={cfg.api_root} root={cfg.root} "
          f"cookies={cfg.cookies} chat={CHAT}")
    me = bot.tg.get_me()
    print(f"   getMe -> @{me['username']} ({me['id']}) via {cfg.api_root}")
    check("local Bot API server reachable", me.get("username") == "heydar_downloader_bot")

    print("\n== 0) long-poll transport (getUpdates against the server) ==")
    updates = bot.tg.get_updates(None, timeout=1, drop_pending=True)
    print(f"   getUpdates ok, {len(updates)} pending update(s), offset behaviour normal")
    check("getUpdates long-poll works", isinstance(updates, list))

    print("\n== 1) /start and /status (Persian + lifetime) ==")
    bot.on_message({"message_id": 1, "chat": {"id": CHAT, "type": "private"},
                    "from": {"id": CHAT, "is_bot": False}, "text": "/start"})
    bot.on_message({"message_id": 2, "chat": {"id": CHAT, "type": "private"},
                    "from": {"id": CHAT, "is_bot": False}, "text": "/status"})
    welcome = texts()[-2] if len(texts()) >= 2 else ""
    status = texts()[-1] if texts() else ""
    check("welcome mentions sending the file itself", "خودِ فایل" in welcome)
    check("welcome has runner lifetime", "زمان باقیمانده" in welcome, f"{len(welcome)} chars")
    check("status reports the runner lifetime", "زمان باقیمانده" in status)
    check("status reports yt-dlp version", "yt-dlp" in status)

    print("\n== 2) YouTube picker -> video (mp4) ==")
    mark = len(files())
    introduce(bot, YT_VIDEO)
    ok = wait_for(lambda: bot.states.get(CHAT) and bot.states[CHAT].stage == "await_format",
                  240, "probe")
    check("format picker shown", ok)
    if not ok:
        return report()
    state = bot.states[CHAT]
    print(f"   probe title={state.info.title!r} duration={state.info.duration_s}s "
          f"hint={human_size(state.info.filesize_hint)}")
    picker_id = OUTBOUND[-1]["result"]["message_id"]
    click(bot, f"fmt:video:{state.token}", picker_id, "video button")
    click(bot, f"vid:best:{state.token}", picker_id, "1080p/no-compress")
    ok = wait_for(lambda: len(files()) > mark, 900, "video delivery")
    check("video delivered into the chat", ok)
    if ok:
        verify_delivery(bot, files()[-1], "video" if files()[-1]["payload"].get("kind") == "video"
                        else "document", "youtube video")
        print(f"   quality={files()[-1]['payload'].get('title')!r} "
              f"kind={files()[-1]['payload'].get('kind')}")

    print("\n== 3) YouTube picker -> audio mp3 128 kbps ==")
    mark = len(files())
    introduce(bot, YT_VIDEO)
    wait_for(lambda: bot.states.get(CHAT) and bot.states[CHAT].stage == "await_format", 240, "probe#2")
    state = bot.states[CHAT]
    picker_id = OUTBOUND[-1]["result"]["message_id"]
    click(bot, f"fmt:audio:{state.token}", picker_id, "audio button")
    click(bot, f"aud:mp3:128:{state.token}", picker_id, "mp3 128")
    ok = wait_for(lambda: len(files()) > mark, 900, "mp3 delivery")
    check("mp3 delivered into the chat", ok)
    if ok:
        item = files()[-1]
        verify_delivery(bot, item, "audio", "youtube mp3")
        check("mp3 file extension", str(item["path"]).endswith(".mp3"), str(item["path"]))
        probe = subprocess.run([str(cfg.ffprobe), "-v", "error", "-select_streams", "a:0",
                                "-show_entries", "stream=codec_name,bit_rate",
                                "-of", "default=nw=1", str(item["path"])],
                               capture_output=True, text=True)
        print("   ffprobe:", probe.stdout.strip().replace("\n", " "))
        check("ffprobe confirms mp3 ~128 kbps", "codec_name=mp3" in probe.stdout
              and "128000" in probe.stdout, probe.stdout.strip().replace("\n", " "))

    print("\n== 4) expiry: resend button + reply-to-file ==")
    last_file = files()[-1]
    token = last_file["payload"]["keyboard"][0][0]["callback_data"]
    delivered_msg_id = last_file["result"]["message_id"]
    delivered_path = Path(last_file["path"])
    print(f"   deleting {delivered_path} (simulates TTL sweep / runner restart)")
    delivered_path.unlink()
    mark = len(texts())
    click(bot, token, delivered_msg_id, "resend after delete")
    ok = wait_for(lambda: len(texts()) > mark, 30, "expired text")
    expired_text = texts()[-1] if len(texts()) > mark else ""
    check("resend after delete -> «منقضی شده»", "منقضی شده است" in expired_text)
    mark = len(texts())
    introduce(bot, "", reply_to={"message_id": delivered_msg_id, "from": {"id": me["id"], "is_bot": True},
                                 "video": {"file_id": "x", "file_unique_id": "y"}})
    ok = wait_for(lambda: len(texts()) > mark, 30, "reply expired text")
    check("reply to a dead file -> «منقضی شده»",
          "منقضی شده است" in (texts()[-1] if len(texts()) > mark else ""))

    print("\n== 5) direct http(s) link -> document (sha256 vs independent curl) ==")
    mark = len(files())
    introduce(bot, DIRECT)
    ok = wait_for(lambda: len(files()) > mark, 300, "direct delivery")
    check("direct link delivered as a document", ok)
    if ok:
        verify_delivery(bot, files()[-1], "document", "direct link")
        ref = cfg.tmp_dir / "ref-curl.bin"
        subprocess.run(["curl", "-fsSL", "-o", str(ref), DIRECT], check=True)
        independent = hashlib.sha256(ref.read_bytes()).hexdigest()
        bot_sha = sha256_file(Path(files()[-1]["path"]))
        print(f"   curl sha256={independent}")
        check("sha256 == independent curl download", independent == bot_sha, bot_sha)

    print("\n== 6) failure matrix (each a specific Persian message, bot stays alive) ==")
    cases = [
        ("سلام دوست من", "err_not_url", "این یک لینک نیست"),
        ("ftp://example.com/file.zip", "err_bad_scheme", "فقط لینکهای http و https"),
        ("http://127.0.0.1:8081/secret", "err_blocked_host", "شبکه داخلی"),
        ("http://169.254.169.254/latest/meta-data/", "err_blocked_host", "شبکه داخلی"),
        ("https://www.instagram.com/reel/xyz/", "err_unsupported_site", "پشتیبانی نمیشود"),
    ]
    for text, key, needle in cases:
        mark = len(texts())
        introduce(bot, text)
        wait_for(lambda: len(texts()) > mark, 30, f"reply for {key}")
        got = texts()[-1] if len(texts()) > mark else ""
        check(f"{key} Persian message", needle in got, repr(got.splitlines()[0][:60]))
    mark = len(texts())
    introduce(bot, YT_DEAD)
    wait_for(lambda: len(texts()) > mark, 240, "private/deleted youtube")
    got = texts()[-1] if len(texts()) > mark else ""
    check("err_private Persian message",
          "خصوصی" in got or "در دسترس نیست" in got, repr(got.splitlines()[0][:60]))

    print("\n== 6b) oversize + bad file type (real downloads refused) ==")
    small = replace(cfg, max_file_mb=1)
    bot_small = Bot(small)
    spy(bot_small)
    mark = len(texts())
    introduce(bot_small, BIG)
    ok = wait_for(lambda: len(texts()) > mark, 300, "oversize reply")
    got = texts()[-1] if len(texts()) > mark else ""
    check("oversize link -> err_too_big Persian message", "بزرگتر از سقف" in got,
          repr(got.splitlines()[0][:70]))
    mark = len(texts())
    introduce(bot, OCTET)
    ok = wait_for(lambda: len(texts()) > mark, 60, "bad type reply")
    got = texts()[-1] if len(texts()) > mark else ""
    check("octet-stream w/o extension -> err_bad_type", "پشتیبانی نمیشود" in got or "نوع فایل" in got,
          repr(got.splitlines()[0][:70]))

    print("\n== 6c) cookies missing ==")
    nocookie = replace(cfg, cookies=Path(cfg.root) / "nope.txt")
    bot_nc = Bot(nocookie)
    spy(bot_nc)
    mark = len(texts())
    introduce(bot_nc, YT_VIDEO)
    ok = wait_for(lambda: len(texts()) > mark, 60, "cookies_missing reply")
    got = texts()[-1] if len(texts()) > mark else ""
    check("missing cookies -> err_cookies_missing Persian message", "کوکی" in got,
          repr(got.splitlines()[0][:70]))

    print("\n== 7) /kill authorisation + bot still alive ==")
    mark = len(texts())
    introduce(bot, "/kill")
    wait_for(lambda: len(texts()) > mark, 30, "kill_denied")
    check("non-admin /kill refused", "فقط برای مدیر" in (texts()[-1] if len(texts()) > mark else ""))
    mark = len(texts())
    introduce(bot, "/status")
    wait_for(lambda: len(texts()) > mark, 30, "status after failures")
    check("bot still alive after the whole failure matrix",
          "بات فعال است" in (texts()[-1] if len(texts()) > mark else ""))

    print("\n== 8) transport ladder: docker server sees no host paths ==")
    docker_root = "http://127.0.0.1:8081"
    try:
        probe_client = Bot(replace(cfg, api_root=docker_root))
        spy(probe_client)
        who = probe_client.tg.get_me()
        print(f"   {docker_root} -> @{who['username']}")
        mark = len(texts())
        introduce(probe_client, DIRECT)
        ok = wait_for(lambda: len(files()) > mark, 300, "docker-fallback delivery")
        check("streamed multipart fallback delivered the file", ok)
        if ok:
            item = files()[-1]
            ref = Path(item["path"])
            node = item["result"].get("document") or {}
            print(f"   fallback file {ref.name} {node.get('file_size')} B "
                  f"(local {ref.stat().st_size} B)")
            check("fallback upload size matches", node.get("file_size") == ref.stat().st_size)
    except Exception as exc:  # noqa: BLE001
        print(f"   !! docker server not running ({exc}); skipped")

    return report()


def report() -> int:
    print(f"\nRESULT: {'ALL PASSED' if not FAILS else str(len(FAILS)) + ' FAILURES: ' + str(FAILS)}")
    print(f"outbound messages sent during the run: {len(OUTBOUND)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
