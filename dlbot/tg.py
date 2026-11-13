"""Telegram client over the LOCAL Bot API server (spec section 4.7).

Plain stdlib HTTP: the local server accepts an absolute file path in the file
field, so a 500 MB video is sent without a multipart copy.
"""
from __future__ import annotations

import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from config import Config


class TelegramError(RuntimeError):
    def __init__(self, message: str, *, code: int = 0, retry_after: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


class TelegramClient:
    def __init__(self, cfg: Config, *, timeout: int = 70) -> None:
        self.cfg = cfg
        self.timeout = timeout
        self.base = f"{cfg.api_root}/bot{cfg.bot_token}"

    # ---- low level -----------------------------------------------------
    def _endpoint(self, method: str) -> str:
        return f"{self.base}/{method}"

    def call(self, method: str, params: dict[str, Any] | None = None, *,
             timeout: int | None = None, files: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        timeout = timeout or self.timeout
        data = None
        headers = {"Content-Type": "application/json"}
        if files:
            boundary = uuid.uuid4().hex
            body = _multipart(params, files, boundary)
            headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
            data = body
        else:
            data = json.dumps(params).encode("utf-8")
        request = urllib.request.Request(self._endpoint(method), data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = ""
            try:
                raw = exc.read().decode("utf-8", "replace")
            except Exception:
                pass
            retry_after = 0
            description = raw
            try:
                parsed = json.loads(raw)
                description = parsed.get("description", raw)
                retry_after = int((parsed.get("parameters") or {}).get("retry_after", 0))
            except Exception:
                pass
            raise TelegramError(description or f"HTTP {exc.code}", code=exc.code,
                                retry_after=retry_after) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TelegramError(f"network: {exc}", code=0) from exc
        if not payload.get("ok"):
            raise TelegramError(str(payload.get("description", "api error")),
                                code=int(payload.get("error_code", 0)))
        return payload

    def get_me(self) -> dict[str, Any]:
        return self.call("getMe")["result"]

    def health(self) -> bool:
        try:
            self.get_me()
            return True
        except Exception:
            return False

    def get_updates(self, offset: int | None, *, timeout: int = 25,
                    drop_pending: bool = False) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": timeout, "limit": 100,
                                  "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        if drop_pending:
            params["drop_pending_updates"] = True
        payload = self.call("getUpdates", params, timeout=timeout + 20)
        return list(payload.get("result") or [])

    # ---- convenience ---------------------------------------------------
    def send_message(self, chat_id: int, text: str, *, keyboard: Any = None,
                     reply_to: int | None = None, disable_preview: bool = True) -> dict[str, Any]:
        params: dict[str, Any] = {
            "chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": disable_preview},
        }
        if keyboard is not None:
            params["reply_markup"] = {"inline_keyboard": keyboard}
        if reply_to is not None:
            params["reply_parameters"] = {"message_id": reply_to}
        return self.call("sendMessage", params)["result"]

    def edit_message(self, chat_id: int, message_id: int, text: str, *, keyboard: Any = None) -> None:
        params: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id,
                                  "text": text, "parse_mode": "HTML"}
        if keyboard is not None:
            params["reply_markup"] = {"inline_keyboard": keyboard}
        try:
            self.call("editMessageText", params)
        except TelegramError as exc:
            if "not modified" in str(exc).lower():
                return
            raise

    def answer_callback(self, callback_id: str, *, text: str = "", alert: bool = False) -> None:
        try:
            self.call("answerCallbackQuery",
                      {"callback_query_id": callback_id, "text": text[:200], "show_alert": alert})
        except TelegramError:
            return

    def send_chat_action(self, chat_id: int, action: str = "upload_video") -> None:
        try:
            self.call("sendChatAction", {"chat_id": chat_id, "action": action})
        except TelegramError:
            return

    # ---- sending files (requirement 2) ---------------------------------
    def send_file(self, chat_id: int, path: Path, *, kind: str, caption: str,
                  title: str = "", duration_s: int | None = None,
                  keyboard: Any = None, retries: int = 3,
                  progress_cb: Any = None) -> dict[str, Any]:
        """kind in {video, audio, document}; local path is passed as a plain string."""
        path = Path(path)
        if not path.exists():
            raise TelegramError(f"file missing: {path}")
        use_local_path = self.cfg.api_root.startswith("http://127.0.0.1")
        method = {"video": "sendVideo", "audio": "sendAudio", "document": "sendDocument"}[kind]
        field = {"video": "video", "audio": "audio", "document": "document"}[kind]

        last_error: TelegramError | None = None
        for attempt in range(1, retries + 1):
            params: dict[str, Any] = {"chat_id": chat_id, "caption": caption,
                                      "parse_mode": "HTML"}
            files: dict[str, Any] | None = None
            if use_local_path:
                # The local Bot API server needs a file:// URL and the file must live
                # under a path that is bind-mounted into its container (verified).
                params[field] = "file://" + str(path.resolve())
            else:
                files = {field: path}              # public API: multipart upload
            if kind == "video":
                params["supports_streaming"] = True
            if title:
                params["title"] = title[:128]
                if kind == "document":
                    params["file_name"] = _file_name(path, title)
            if duration_s and kind == "audio":
                params["duration"] = int(duration_s)
            if keyboard is not None:
                params["reply_markup"] = {"inline_keyboard": keyboard}
            try:
                result = self.call(method, params, files=files, timeout=1800)["result"]
                if progress_cb:
                    progress_cb(100)
                return result
            except TelegramError as exc:
                last_error = exc
                if exc.code in (429, 500, 502, 503, 504) or exc.code == 0:
                    wait = min(exc.retry_after or (2 ** attempt), 30)
                    if progress_cb:
                        progress_cb(attempt, {"retry": wait, "max": retries, "code": exc.code})
                    time.sleep(wait)
                    continue
                raise
        raise last_error or TelegramError("upload failed")

    def send_video_path(self, chat_id: int, path: Path, **kw: Any) -> dict[str, Any]:
        return self.send_file(chat_id, path, kind="video", **kw)

    def send_document_path(self, chat_id: int, path: Path, **kw: Any) -> dict[str, Any]:
        return self.send_file(chat_id, path, kind="document", **kw)

    def send_audio_path(self, chat_id: int, path: Path, **kw: Any) -> dict[str, Any]:
        return self.send_file(chat_id, path, kind="audio", **kw)


def _file_name(path: Path, title: str) -> str:
    from re import sub

    safe = sub(r"[^\w\-. \u0600-\u06FF]", "_", title).strip() or path.stem
    return f"{safe[:80]}{path.suffix}"


def _multipart(params: dict[str, Any], files: dict[str, Any], boundary: str) -> bytes:
    """Minimal multipart encoder — used only when the public API is in play."""
    out = bytearray()
    for key, value in params.items():
        if value is None or not isinstance(value, (str, int, float)):
            continue
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode()
    for key, path in files.items():
        path = Path(path)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"; "
                f"filename=\"{path.name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode()
        with open(path, "rb") as handle:
            while True:
                block = handle.read(1 << 20)
                if not block:
                    break
                out += block
        out += b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out)
