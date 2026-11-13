"""Telegram client over the LOCAL Bot API server (spec section 4.7).

Plain stdlib HTTP.  When the Bot API server runs in ``--local`` mode on the same
host as the bot (native Windows binary, or a Linux container with the bot root
bind-mounted) it accepts an absolute path in the file field, so a 500 MB video
is sent without a second copy.  If the server cannot see the path (dockerised
server without a bind mount) we fall back to a *streamed* multipart upload that
never holds the file in memory.
"""
from __future__ import annotations

import contextlib
import http.client
import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, BinaryIO, Iterator

from config import Config

IS_WINDOWS = os.name == "nt"


class TelegramError(RuntimeError):
    def __init__(self, message: str, *, code: int = 0, retry_after: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


def _is_loopback(root: str) -> bool:
    host = urllib.parse.urlsplit(root).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1", "0.0.0.0")


class TelegramClient:
    def __init__(self, cfg: Config, *, timeout: int = 70) -> None:
        self.cfg = cfg
        self.timeout = timeout
        self.base = f"{cfg.api_root}/bot{cfg.bot_token}"
        split = urllib.parse.urlsplit(cfg.api_root)
        self._host = split.hostname or "127.0.0.1"
        self._port = split.port or (443 if split.scheme == "https" else 80)
        self._secure = split.scheme == "https"

    # ---- low level -----------------------------------------------------
    def _endpoint(self, method: str) -> str:
        return f"{self.base}/{method}"

    def _connection(self, timeout: int) -> http.client.HTTPConnection:
        if self._secure:
            return http.client.HTTPSConnection(self._host, self._port, timeout=timeout)
        return http.client.HTTPConnection(self._host, self._port, timeout=timeout)

    def call(self, method: str, params: dict[str, Any] | None = None, *,
             timeout: int | None = None, files: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        timeout = timeout or self.timeout
        if files:
            payload = self._post_multipart(method, params, files, timeout)
        else:
            request = urllib.request.Request(
                self._endpoint(method), data=json.dumps(params).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                payload = _error_payload(exc)
            except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
                raise TelegramError(f"network: {exc}", code=0) from exc
        if not payload.get("ok"):
            raise TelegramError(str(payload.get("description", "api error")),
                                code=int(payload.get("error_code", 0)),
                                retry_after=int((payload.get("parameters") or {}).get("retry_after", 0)))
        return payload

    def _post_multipart(self, method: str, params: dict[str, Any],
                        files: dict[str, Any], timeout: int) -> dict[str, Any]:
        """Stream the file body with a known Content-Length (no RAM copy)."""
        boundary = uuid.uuid4().hex
        head = bytearray()
        for key, value in params.items():
            if value is None or isinstance(value, (dict, list)):
                value = json.dumps(value)
            if not isinstance(value, (str, int, float)):
                continue
            head += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n"
                     f"{value}\r\n").encode("utf-8")
        total_file = 0
        for key, path in files.items():
            path = Path(path)
            if not path.exists():
                raise TelegramError(f"file missing: {path}")
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            head += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"; "
                     f"filename=\"{path.name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode("utf-8")
            total_file += path.stat().st_size
        tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
        content_length = len(head) + total_file + len(tail)

        conn = self._connection(timeout)
        try:
            conn.putrequest("POST", urllib.parse.urlsplit(self._endpoint(method)).path)
            conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
            conn.putheader("Content-Length", str(content_length))
            conn.endheaders()
            conn.send(bytes(head))
            for path in files.values():
                with open(path, "rb") as handle:
                    while True:
                        block = handle.read(1 << 20)
                        if not block:
                            break
                        conn.send(block)
            conn.send(tail)
            response = conn.getresponse()
            raw = response.read()
        except (OSError, http.client.HTTPException) as exc:
            raise TelegramError(f"network: {exc}", code=0) from exc
        finally:
            with contextlib.suppress(Exception):
                conn.close()
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            raise TelegramError(f"bad response: {raw[:200]!r}", code=response.status) from exc
        if not payload.get("ok"):
            raise TelegramError(str(payload.get("description", f"HTTP {response.status}")),
                                code=int(payload.get("error_code", response.status)),
                                retry_after=int((payload.get("parameters") or {}).get("retry_after", 0)))
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

    # ---- sending files (requirement 3) ---------------------------------
    def send_file(self, chat_id: int, path: Path, *, kind: str, caption: str,
                  title: str = "", duration_s: int | None = None,
                  keyboard: Any = None, retries: int = 3,
                  progress_cb: Any = None) -> dict[str, Any]:
        """kind in {video, audio, document}.

        Tries, in order: ``file://<abs>`` local path, the bare absolute path,
        then a streamed multipart upload.  Works for a native local Bot API
        server on Windows (both path forms) and for a dockerised one on Linux.
        """
        path = Path(path)
        if not path.exists():
            raise TelegramError(f"file missing: {path}")
        method = {"video": "sendVideo", "audio": "sendAudio", "document": "sendDocument"}[kind]
        field = {"video": "video", "audio": "audio", "document": "document"}[kind]

        base_params: dict[str, Any] = {"chat_id": chat_id, "caption": caption,
                                       "parse_mode": "HTML"}
        if kind == "video":
            base_params["supports_streaming"] = True
        if title:
            base_params["title"] = title[:128]
            if kind == "document":
                base_params["file_name"] = _file_name(path, title)
        if duration_s and kind in ("audio", "video"):
            base_params["duration"] = int(duration_s)
        if keyboard is not None:
            base_params["reply_markup"] = {"inline_keyboard": keyboard}

        absolute = str(path.resolve())
        attempts: list[tuple[str, dict[str, Any] | None]] = []
        if _is_loopback(self.cfg.api_root):
            attempts.append((f"file://{absolute}", None))
            attempts.append((absolute, None))
        attempts.append(("bytes", {field: path}))

        last_error: TelegramError | None = None
        for attempt, (label, files) in enumerate(attempts, start=1):
            params = dict(base_params)
            if label != "bytes":
                params[field] = label
            for retry in range(1, retries + 1):
                try:
                    result = self.call(method, params, files=files, timeout=1800)["result"]
                    if progress_cb:
                        progress_cb(100)
                    return result
                except TelegramError as exc:
                    last_error = exc
                    # A 400 on a local path means "the server cannot see this
                    # path" -> try the next (cheaper -> more expensive) form.
                    if label != "bytes" and exc.code == 400:
                        break
                    if exc.code in (429, 500, 502, 503, 504) or exc.code == 0:
                        wait = min(exc.retry_after or (2 ** retry), 30)
                        if progress_cb:
                            progress_cb(attempt, {"retry": wait, "max": retries, "code": exc.code})
                        time.sleep(wait)
                        continue
                    if label == "bytes":
                        raise
                    break
        raise last_error or TelegramError("upload failed")

    def send_video_path(self, chat_id: int, path: Path, **kw: Any) -> dict[str, Any]:
        return self.send_file(chat_id, path, kind="video", **kw)

    def send_document_path(self, chat_id: int, path: Path, **kw: Any) -> dict[str, Any]:
        return self.send_file(chat_id, path, kind="document", **kw)

    def send_audio_path(self, chat_id: int, path: Path, **kw: Any) -> dict[str, Any]:
        return self.send_file(chat_id, path, kind="audio", **kw)


def _error_payload(exc: urllib.error.HTTPError) -> dict[str, Any]:
    raw = ""
    with contextlib.suppress(Exception):
        raw = exc.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except Exception:
        return {"ok": False, "error_code": exc.code, "description": raw or f"HTTP {exc.code}"}


def _file_name(path: Path, title: str) -> str:
    from re import sub

    safe = sub(r"[^\w\-. \u0600-\u06FF]", "_", title).strip() or path.stem
    return f"{safe[:80]}{path.suffix}"


def _multipart(params: dict[str, Any], files: dict[str, Any], boundary: str) -> bytes:
    """Legacy in-memory multipart encoder (small payloads only)."""
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
