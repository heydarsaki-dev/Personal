#!/usr/bin/env python3
"""Entrypoint: config -> registry -> sweeper -> poller -> handlers.

Persian UI, files delivered into the originating chat, local Bot API server on
127.0.0.1:8081 (500 MB-class uploads, send by local path).
"""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import strings  # noqa: E402
from classify import UrlInfo, UrlKind, classify  # noqa: E402
from config import (  # noqa: E402
    Config, can_start_job, effective_ttl, human_duration, human_size, human_speed,
    load_config, progress_bar, remaining_seconds, ttl_clamped,
)
from downloader import (  # noqa: E402
    Choice, DownloadResult, MediaInfo, YtDlpError, disk_ok, download_direct,
    download_youtube, probe,
)
from jobs import ChatState, JobQueue, JobRejected  # noqa: E402
from poller import Poller  # noqa: E402
from registry import FileRecord, Registry, admin_alert, run_sweeper  # noqa: E402
from tg import TelegramClient, TelegramError  # noqa: E402

PROGRESS_MIN_INTERVAL = 4.0
UPLOAD_LIMIT_MB = 2000


class Bot:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.tg = TelegramClient(cfg)
        self.reg = Registry(cfg.db_path, cfg.ttl_s, ttl_provider=lambda: effective_ttl(cfg))
        self.jobs = JobQueue(cfg)
        self.states: dict[int, ChatState] = {}
        self.states_lock = threading.RLock()
        self.stop = threading.Event()
        self.started_at = time.time()

    # ---- helpers -------------------------------------------------------
    def state(self, chat_id: int) -> ChatState:
        with self.states_lock:
            return self.states.setdefault(chat_id, ChatState())

    def send(self, chat_id: int, text: str, *, keyboard: Any = None) -> dict[str, Any]:
        return self.tg.send_message(chat_id, text, keyboard=keyboard)

    def send_key(self, chat_id: int, key: str, *, keyboard: Any = None, **kw: Any) -> dict[str, Any]:
        return self.send(chat_id, strings.render(key, **kw), keyboard=keyboard)

    def lt(self, *, short: bool = False) -> str:
        return strings.lifetime_line(self.cfg, short=short)

    def ask_admin(self, title: str, detail: str, code: str = "") -> None:
        admin_alert(self.tg, self.cfg, title, detail, code)

    # ---- update dispatch ----------------------------------------------
    def on_update(self, upd: dict[str, Any]) -> None:
        try:
            if "message" in upd:
                self.on_message(upd["message"])
            elif "callback_query" in upd:
                self.on_callback(upd["callback_query"])
        except Exception as exc:  # never kill the poller
            self.ask_admin("خطای پردازش آپدیت", f"{type(exc).__name__}: {exc}", "update")
            traceback.print_exc()

    def on_message(self, msg: dict[str, Any]) -> None:
        chat = msg.get("chat") or {}
        chat_id = int(chat.get("id", 0))
        user_id = int((msg.get("from") or {}).get("id", chat_id))
        text = (msg.get("text") or msg.get("caption") or "").strip()
        if not chat_id or not text:
            return
        if text.startswith("/"):
            self.on_command(chat_id, user_id, text)
            return
        info = classify(text)
        self.handle_url(chat_id, user_id, info)

    def on_command(self, chat_id: int, user_id: int, text: str) -> None:
        command = text.split()[0].split("@")[0].lower()
        if command == "/start":
            first = (text.split(maxsplit=1)[1] if len(text.split()) > 1 else "")
            name = "دوست من"
            self.send_key(chat_id, "welcome", name=name, lt=self.lt())
        elif command == "/help":
            self.send_key(chat_id, "help", lt=self.lt(), **strings.common_kwargs(self.cfg))
        elif command == "/status":
            stats = self.reg.stats()
            self.send_key(
                chat_id, "status", lt=self.lt(),
                active=self.jobs.active_count(), concurrent=self.cfg.max_concurrent,
                used=self.jobs.usage(user_id), per_hour=self.cfg.per_user_per_hour,
                files=stats["files"], used_mb=f"{stats['used_bytes'] / (1024 * 1024):.1f}",
                ytdlp_ver=self.ytdlp_version(),
            )
        elif command == "/cancel":
            self.state(chat_id).reset()
            if self.jobs.cancel_chat(chat_id):
                self.send_key(chat_id, "cancelled")
            else:
                self.send_key(chat_id, "nothing_to_cancel")
        elif command == "/kill":
            if user_id != self.cfg.admin_id and chat_id != self.cfg.admin_id:
                self.send_key(chat_id, "kill_denied")
                return
            self.send_key(chat_id, "kill_done", time=time.strftime("%H:%M", time.gmtime()))
            self.stop.set()
        else:
            self.send_key(chat_id, "unknown_command")

    def ytdlp_version(self) -> str:
        try:
            import subprocess

            out = subprocess.run([*self.cfg.ytdlp_argv(), "--version"], capture_output=True,
                                 text=True, timeout=30)
            return (out.stdout or "").strip() or "?"
        except Exception:
            return "?"

    # ---- URL handling --------------------------------------------------
    def handle_url(self, chat_id: int, user_id: int, info: UrlInfo) -> None:
        if not can_start_job(self.cfg):
            self.send_key(chat_id, "err_no_time",
                          mm=remaining_seconds(self.cfg) // 60)
            return
        if info.kind is UrlKind.NOT_URL:
            self.send_key(chat_id, info.reason or "err_not_url")
            return
        if info.kind is UrlKind.UNSUPPORTED_SITE:
            self.send_key(chat_id, "err_unsupported_site")
            return
        if info.kind is UrlKind.BLOCKED:
            self.send_key(chat_id, info.reason or "err_blocked_host")
            return
        if info.kind is UrlKind.YOUTUBE:
            self.submit_probe(chat_id, user_id, info)
            return
        self.submit_direct(chat_id, user_id, info)

    def _guard_submit(self, chat_id: int, user_id: int, fn: Callable) -> bool:
        if not disk_ok(self.cfg):
            self.send_key(chat_id, "err_disk_full")
            self.ask_admin("دیسک پر شده", "فضای آزاد کمتر از ۲ برابر سقف فایل است", "disk")
            return False
        try:
            position = self.jobs.submit(chat_id, user_id, fn)
        except JobRejected as exc:
            self.send_key(chat_id, exc.key, **exc.kw)
            return False
        if int(position) > 1:
            self.send_key(chat_id, "queued", pos=position, lt=self.lt())
        return True

    # ---- YouTube: probe then format picker -----------------------------
    def submit_probe(self, chat_id: int, user_id: int, info: UrlInfo) -> None:
        cfg = self.cfg

        def run(cancel: threading.Event) -> None:
            try:
                media = probe(info.url, cfg)
            except YtDlpError as exc:
                self.ask_admin("خطای yt-dlp (probe)", f"{exc.detail[:400]}", exc.key)
                self.send_key(chat_id, exc.key)
                return
            except Exception as exc:
                self.send_key(chat_id, "err_internal", code=type(exc).__name__)
                self.ask_admin("خطای داخلی probe", str(exc), "probe")
                return
            if media.duration_s and media.duration_s > cfg.max_duration_s:
                self.send_key(chat_id, "err_too_long",
                              duration=human_duration(media.duration_s),
                              max_h=cfg.max_duration_s // 3600)
                return
            state = self.state(chat_id)
            token = self.reg.reserve_token()
            state.token = token
            state.url = info.url
            state.info = media
            state.stage = "await_format"
            text = strings.render(
                "format_picker", title=media.title or "بدون عنوان",
                uploader=media.uploader or "?", duration=human_duration(media.duration_s),
                size=human_size(media.filesize_hint),
            )
            self.send(chat_id, text, keyboard=strings.kb_format(token=token, cfg=cfg))

        self._guard_submit(chat_id, user_id, run)

    # ---- direct link ---------------------------------------------------
    def submit_direct(self, chat_id: int, user_id: int, info: UrlInfo) -> None:
        cfg = self.cfg
        token = self.reg.reserve_token()

        def run(cancel: threading.Event) -> None:
            progress_id: int | None = None
            try:
                progress_id = self.send(
                    chat_id, strings.render("progress_download", bar=progress_bar(0),
                                            pct=0, done="0", total="?", speed="?", eta="?",
                                            title=info.url[:60])
                )["message_id"]
            except TelegramError:
                progress_id = None
            try:
                result = download_direct(info.url, cfg, token=token)
            except YtDlpError as exc:
                if progress_id:
                    with __import__("contextlib").suppress(Exception):
                        self.tg.edit_message(chat_id, progress_id, strings.render(exc.key, reason=exc.detail[:120]))
                else:
                    self.send_key(chat_id, exc.key, reason=exc.detail[:120])
                self.ask_admin("دانلود لینک مستقیم ناموفق", f"{info.url} :: {exc.detail[:300]}", exc.key)
                return
            except Exception as exc:
                self.send_key(chat_id, "err_internal", code=type(exc).__name__)
                self.ask_admin("خطای داخلی دانلود مستقیم", str(exc), "direct")
                return
            if result.size > cfg.max_file_bytes:
                self.send_key(chat_id, "err_too_big", size=human_size(result.size),
                              max_mb=cfg.max_file_mb)
                return
            self.deliver(chat_id, result, token, info.host, progress_id=progress_id)

        if not self._guard_submit(chat_id, user_id, run):
            return

    # ---- callbacks -----------------------------------------------------
    def on_callback(self, cb: dict[str, Any]) -> None:
        cb_id = str(cb.get("id", ""))
        data = str(cb.get("data") or "")
        msg = cb.get("message") or {}
        chat_id = int((msg.get("chat") or {}).get("id", 0))
        user_id = int((cb.get("from") or {}).get("id", chat_id))
        message_id = int(msg.get("message_id", 0))
        state = self.state(chat_id)
        if not data:
            self.tg.answer_callback(cb_id)
            return
        try:
            if ":" not in data:                       # plain token -> resend (D3)
                self.handle_resend(cb_id, chat_id, data)
                return
            parts = data.split(":")
            head = parts[0]
            tail_token = parts[-1]
            if head == "fmt":
                action = parts[1]
                if action == "audio":
                    self.tg.answer_callback(cb_id)
                    self.show_audio_menu(chat_id, message_id, state)
                elif action == "back":
                    self.tg.answer_callback(cb_id)
                    self.show_format_menu(chat_id, state)
                elif action == "cancel":
                    state.reset()
                    self.tg.answer_callback(cb_id, text=strings.render("cancelled"))
                    self.tg.edit_message(chat_id, message_id, strings.render("cancelled"))
                elif action == "video":
                    self.tg.answer_callback(cb_id)
                    if state.url is None:
                        self.tg.edit_message(chat_id, message_id,
                                             strings.render("err_stale_callback"))
                        return
                    self.tg.edit_message(
                        chat_id, message_id, strings.render("compress_menu"),
                        keyboard=strings.kb_compress(token=tail_token, cfg=self.cfg),
                    )
                else:
                    self.tg.answer_callback(cb_id, text=strings.render("err_stale_callback"))
                return
            if head == "aud":
                action = parts[1]
                if action == "back":
                    self.tg.answer_callback(cb_id)
                    self.show_format_menu(chat_id, state)
                    return
                if state.url is None:
                    self.tg.answer_callback(cb_id, text=strings.render("err_stale_callback"))
                    return
                if action == "m4a":
                    choice = Choice(kind="audio", audio_format="m4a")
                else:
                    choice = Choice(kind="audio", audio_format="mp3",
                                    mp3_kbps=int(parts[2]))
                self.tg.answer_callback(cb_id)
                self.submit_download(chat_id, user_id, state, choice, message_id)
                return
            if head == "vid":
                if state.url is None:
                    self.tg.answer_callback(cb_id, text=strings.render("err_stale_callback"))
                    return
                height = None if parts[1] == "best" else int(parts[1])
                self.tg.answer_callback(cb_id)
                self.submit_download(chat_id, user_id, state,
                                     Choice(kind="video", height=height), message_id)
                return
            self.tg.answer_callback(cb_id, text=strings.render("err_stale_callback"))
        except Exception as exc:
            self.ask_admin("خطای callback", f"{data} :: {exc}", "callback")

    def show_format_menu(self, chat_id: int, state: ChatState) -> None:
        if state.url is None or state.token is None:
            self.send_key(chat_id, "err_stale_callback")
            return
        info = state.info or MediaInfo()
        self.send(chat_id, strings.render(
            "format_picker", title=info.title or "بدون عنوان", uploader=info.uploader or "?",
            duration=human_duration(info.duration_s), size=human_size(info.filesize_hint)),
            keyboard=strings.kb_format(token=state.token, cfg=self.cfg))
        state.stage = "await_format"

    def show_audio_menu(self, chat_id: int, message_id: int, state: ChatState) -> None:
        if state.url is None or state.token is None:
            with __import__("contextlib").suppress(Exception):
                self.tg.edit_message(chat_id, message_id, strings.render("err_stale_callback"))
            return
        info = state.info or MediaInfo()
        text = strings.render("audio_menu", size=human_size(info.filesize_hint), lt=self.lt())
        self.tg.edit_message(chat_id, message_id, text,
                             keyboard=strings.kb_audio(token=state.token))
        state.stage = "await_audio"

    # ---- download + delivery -------------------------------------------
    def submit_download(self, chat_id: int, user_id: int, state: ChatState,
                        choice: Choice, picker_message_id: int | None = None) -> None:
        cfg = self.cfg
        url = state.url
        token = state.token or self.reg.reserve_token()
        info = state.info or MediaInfo()
        if url is None:
            return
        state.stage = "idle"

        def run(cancel: threading.Event) -> None:
            progress_id = picker_message_id
            last_edit = 0.0

            def on_progress(pct: float | None, detail: str) -> None:
                nonlocal last_edit, progress_id
                now = time.time()
                if now - last_edit < PROGRESS_MIN_INTERVAL:
                    return
                last_edit = now
                extra: dict[str, Any] = {}
                try:
                    extra = json.loads(detail or "{}")
                except json.JSONDecodeError:
                    extra = {}
                if extra.get("stage") == "convert":
                    text = strings.render("progress_convert", kbps=extra.get("kbps", ""),
                                          title=info.title[:60])
                else:
                    total_bytes = int(extra.get("total") or info.filesize_hint or 0)
                    done_bytes = int(total_bytes * (pct or 0) / 100) if total_bytes else 0
                    text = strings.render(
                        "progress_download", bar=progress_bar(pct), pct=int(pct or 0),
                        done=f"{done_bytes / (1024 * 1024):.1f}", total=human_size(total_bytes or None),
                        speed=extra.get("speed") or "?", eta=extra.get("eta") or "?",
                        title=info.title[:60],
                    )
                target = progress_id
                if target is None:
                    try:
                        target = self.send(chat_id, text)["message_id"]
                        progress_id = target
                    except TelegramError:
                        return
                else:
                    with __import__("contextlib").suppress(Exception):
                        self.tg.edit_message(chat_id, target, text)

            try:
                self.tg.send_chat_action(chat_id, "upload_video")
                result = download_youtube(url, choice, cfg, progress_cb=on_progress,
                                          cancel=cancel, info=info)
            except YtDlpError as exc:
                if exc.key == "err_cancelled":
                    return
                self.send_key(chat_id, exc.key, reason=exc.detail[:150],
                              minutes=cfg.job_timeout_s // 60)
                if exc.key in ("err_botcheck", "err_cookies_missing"):
                    self.ask_admin("کوکی یوتیوب مشکل دارد", exc.detail[:400], exc.key)
                return
            except Exception as exc:
                self.send_key(chat_id, "err_internal", code=type(exc).__name__)
                self.ask_admin("خطای داخلی دانلود یوتیوب", str(exc), "ytdlp")
                return
            if result.degraded_from_video:
                self.send_key(chat_id, "err_degraded_audio")
            self.deliver(chat_id, result, token, None, progress_id=progress_id)

        self._guard_submit(chat_id, user_id, run)

    def deliver(self, chat_id: int, result: DownloadResult, token: str,
                host: str | None, *, progress_id: int | None = None) -> None:
        cfg = self.cfg
        if result.size > UPLOAD_LIMIT_MB * 1024 * 1024:
            self.send_key(chat_id, "err_tg_limit", size=human_size(result.size),
                          limit=UPLOAD_LIMIT_MB)
            return
        ttl = self.reg.ttl()
        caption_lt = strings.lifetime_line(cfg, short=True)
        if ttl < cfg.ttl_s:
            caption_lt += "\n" + strings.render("lifetime_clamped", mm=ttl // 60)
        title = result.info.title or Path(result.path).name
        caption = strings.render(
            "caption_file", title=title[:120], size=human_size(result.size),
            duration=human_duration(result.info.duration_s or result.choice.mp3_kbps and None or 0),
            quality=result.choice.quality_label, lt_short=caption_lt,
        )
        kind = "document"
        if result.choice.kind == "video":
            kind = "video"
        elif result.path.suffix.lower() in (".m4a", ".mp3", ".opus", ".ogg"):
            kind = "audio"
        record = FileRecord(
            token=token, chat_id=chat_id, path=Path(result.path), size=result.size,
            sha256=result.sha256, created_at=time.time(),
            expires_at=time.time() + ttl, kind=kind, title=title[:200],
            quality=result.choice.quality_label, duration_s=result.info.duration_s,
        )
        self.reg.add(record)
        try:
            sent = self.tg.send_file(
                chat_id, Path(result.path), kind=kind, caption=caption, title=title,
                duration_s=result.info.duration_s, keyboard=strings.kb_resend(token=token),
            )
        except TelegramError as exc:
            self.reg.drop(token, delete_file=False)
            self.send_key(chat_id, "err_upload", reason=str(exc)[:150], code=exc.code)
            self.ask_admin("ارسال فایل به تلگرام ناموفق", f"{title[:80]} :: {exc}", str(exc.code))
            return
        self.reg.mark_sent(token, chat_id, int(sent.get("message_id", 0)))
        if progress_id:
            with __import__("contextlib").suppress(Exception):
                self.tg.edit_message(chat_id, progress_id,
                                     strings.render("caption_file", title=title[:120],
                                                    size=human_size(result.size),
                                                    duration=human_duration(result.info.duration_s),
                                                    quality=result.choice.quality_label,
                                                    lt_short=caption_lt))
        print(f"[send] chat={chat_id} token={token} size={result.size} "
              f"sha256={result.sha256} path={result.path}", flush=True)

    def handle_resend(self, cb_id: str, chat_id: int, token: str) -> None:
        record = self.reg.get(token)
        if record is None or not self.reg.is_alive(token):
            self.tg.answer_callback(cb_id, text=strings.render("expired",
                                                              lt=self.lt()), alert=False)
            try:
                self.send_key(chat_id, "expired", lt=self.lt())
            except TelegramError:
                pass
            return
        self.tg.answer_callback(cb_id, text=strings.render("resend_ok", lt_short=strings.lifetime_line(self.cfg, short=True)))
        caption = strings.render(
            "caption_file", title=record.title[:120], size=human_size(record.size),
            duration=human_duration(record.duration_s), quality=record.quality,
            lt_short=strings.lifetime_line(self.cfg, short=True),
        )
        try:
            self.tg.send_file(chat_id, record.path, kind=record.kind, caption=caption,
                              title=record.title, duration_s=record.duration_s,
                              keyboard=strings.kb_resend(token=token))
        except TelegramError as exc:
            self.send_key(chat_id, "err_upload", reason=str(exc)[:150], code=exc.code)

    # ---- run -----------------------------------------------------------
    def run(self) -> int:
        cfg = self.cfg
        for directory in (cfg.root, cfg.files_dir, cfg.tmp_dir, cfg.logs_dir):
            directory.mkdir(parents=True, exist_ok=True)
        poller = Poller(cfg, self.tg, self.on_update)
        killed = Poller.kill_previous_pollers()
        if killed:
            print(f"[start] killed {killed} previous poller(s)", flush=True)
            time.sleep(2)
        if not poller.acquire_lock():
            print("[start] another poller holds the lock - exiting", flush=True)
            return 3
        print(f"[start] dlbot up: api_root={cfg.api_root} admin={cfg.admin_id}", flush=True)
        try:
            me = self.tg.get_me()
            print(f"[start] getMe ok: @{me.get('username')} id={me.get('id')}", flush=True)
        except TelegramError as exc:
            print(f"[start] WARNING local Bot API server unreachable: {exc}", flush=True)
            if cfg.api_root.startswith("http://127.0.0.1"):
                # fall back to the public API: still works, but the upload cap drops to 50 MB
                from dataclasses import replace

                fallback = replace(cfg, api_root="https://api.telegram.org", tg_upload_limit_mb=50)
                self.cfg = fallback
                self.tg = TelegramClient(fallback)
                poller.tg = self.tg
                try:
                    me = self.tg.get_me()
                    print(f"[start] public API fallback ok: @{me.get('username')}", flush=True)
                except TelegramError as exc2:
                    print(f"[fatal] no Telegram API reachable: {exc2}", flush=True)
                    self.ask_admin("Bot API server / getMe ناموفق", str(exc)[:300], "getme")
                    return 4
        cleaned = self.reg.startup_cleanup(cfg.files_dir, cfg.tmp_dir)
        print(f"[start] startup cleanup removed {cleaned} file(s); "
              f"remaining budget {remaining_seconds(cfg) // 60} min", flush=True)

        sweeper = threading.Thread(target=run_sweeper, daemon=True,
                                   args=(self.reg, self.stop),
                                   kwargs={"files_dir": cfg.files_dir, "tmp_dir": cfg.tmp_dir})
        sweeper.start()
        self.jobs.start()

        def _term(_signum: int, _frame: Any) -> None:
            self.stop.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            with __import__("contextlib").suppress(Exception):
                signal.signal(sig, _term)

        try:
            poller.run_forever(self.stop)
        finally:
            self.jobs.stop()
            self.reg.sweep()
            print("[stop] dlbot stopped", flush=True)
        return 0


def main() -> int:
    try:
        cfg = load_config()
    except Exception as exc:
        print(f"[fatal] config error: {exc}", file=sys.stderr, flush=True)
        return 2
    missing = strings.cross_check()
    if missing:
        print(f"[fatal] missing strings: {missing}", file=sys.stderr, flush=True)
        return 2
    bot = Bot(cfg)
    return bot.run()


if __name__ == "__main__":
    raise SystemExit(main())
