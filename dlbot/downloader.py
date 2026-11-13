"""Download pipeline: yt-dlp ladder, generic curl path, ffmpeg mp3 (spec section 4.5)."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from config import Config
from classify import UrlInfo, UrlKind, validate_direct_url


class YtDlpError(RuntimeError):
    def __init__(self, key: str, *, detail: str = "", code: str = "") -> None:
        super().__init__(f"{key}: {detail[:400]}")
        self.key = key
        self.detail = detail
        self.code = code


@dataclass
class MediaInfo:
    video_id: str = ""
    title: str = ""
    uploader: str = ""
    duration_s: int | None = None
    filesize_hint: int | None = None
    height: int | None = None
    vcodec: str | None = None
    acodec: str | None = None
    ext: str = "mp4"
    thumb_url: str | None = None
    availability: str | None = None

    @property
    def audio_size_hint(self) -> int | None:
        return self.filesize_hint


@dataclass(frozen=True)
class Choice:
    kind: Literal["video", "audio"]
    height: int | None = None
    audio_format: Literal["m4a", "mp3"] = "m4a"
    mp3_kbps: int | None = None

    @property
    def quality_label(self) -> str:
        if self.kind == "video":
            return f"{self.height or 1080}p • mp4"
        if self.audio_format == "mp3":
            return f"{self.mp3_kbps} kbps • mp3"
        return "m4a (اصل)"


@dataclass
class DownloadResult:
    path: Path
    info: MediaInfo
    choice: Choice
    sha256: str = ""
    size: int = 0
    audio_bitrate_kbps: int | None = None
    degraded_from_video: bool = False
    notes: list[str] = field(default_factory=list)


# ---- helpers -----------------------------------------------------------
def sha256_file(path: Path, *, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def cleanup(path: Path) -> None:
    try:
        if path and path.exists():
            path.unlink()
    except OSError:
        pass


def ensure_cookies(cfg: Config) -> None:
    if not cfg.cookies.exists() or cfg.cookies.stat().st_size == 0:
        raise YtDlpError("err_cookies_missing", detail=f"missing {cfg.cookies}")


def ensure_binaries(cfg: Config, *, need_ffmpeg: bool = True) -> None:
    """Fail fast and loudly instead of raising a raw FileNotFoundError."""
    argv = cfg.ytdlp_argv()
    if not argv[0].endswith(".py") and not Path(argv[0]).exists():
        raise YtDlpError("err_dl_failed", detail=f"yt-dlp not installed at {argv[0]}")
    if need_ffmpeg and not cfg.ffmpeg.exists():
        raise YtDlpError("err_dl_failed", detail=f"ffmpeg not installed at {cfg.ffmpeg}")


def ensure_pot_provider(cfg: Config) -> bool:
    """Ping the bgutil POT provider on :4416 and start it if dark (spec 2.1)."""
    def ping() -> bool:
        try:
            with urllib.request.urlopen(cfg.pot_url + "/ping", timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    if ping():
        return True
    script = cfg.pot_script
    if not script.exists():
        return False
    try:
        subprocess.Popen(
            [shutil.which("node") or "node", str(script)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return False
    for _ in range(15):
        if ping():
            return True
        time.sleep(2)
    return False


def add_common_flags(cfg: Config) -> list[str]:
    """The MANDATORY recipe: cookies + node JS runtime + EJS components (spec 2.1)."""
    return [
        "--cookies", str(cfg.cookies),
        "--js-runtimes", "node",
        "--remote-components", "ejs:github",
        "--ffmpeg-location", str(cfg.ffmpeg),
        "--no-playlist", "--no-warnings", "--no-progress",
        "--retries", "3", "--fragment-retries", "3", "--socket-timeout", "20",
    ]


def ytdlp_base(cfg: Config, tmp_dir: Path) -> list[str]:
    return [
        *cfg.ytdlp_argv(),
        *add_common_flags(cfg),
        "--newline",
        "--paths", f"home:{tmp_dir}",
        "--paths", f"temp:{tmp_dir}",
        "--output", "%(id)s.%(ext)s",
    ]


def _run(argv: list[str], *, timeout_s: int, cancel: threading.Event | None = None,
         on_line: Callable[[str], None] | None = None, cwd: Path | None = None) -> tuple[int, str]:
    """Run a command in its own process group; kill the group on timeout/cancel."""
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        cwd=str(cwd) if cwd else None, start_new_session=True,
    )
    lines: list[str] = []
    deadline = time.time() + timeout_s
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line.rstrip("\n"))
            if on_line:
                on_line(line.rstrip("\n"))
            if cancel is not None and cancel.is_set():
                _kill_group(proc)
                raise YtDlpError("err_cancelled", detail="cancelled by user")
            if time.time() > deadline:
                _kill_group(proc)
                raise YtDlpError("err_dl_timeout", detail=f"timeout {timeout_s}s")
        proc.wait(timeout=30)
    except YtDlpError:
        raise
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        raise YtDlpError("err_dl_timeout", detail=f"timeout {timeout_s}s")
    finally:
        with contextlib.suppress(Exception):
            if proc.stdout:
                proc.stdout.close()
    return proc.returncode or 0, "\n".join(lines)


def _kill_group(proc: subprocess.Popen) -> None:
    with contextlib.suppress(Exception):
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        time.sleep(1)
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


def classify_error(stderr: str) -> str:
    text = (stderr or "").lower()
    table = [
        ("sign in to confirm you're not a bot", "err_botcheck"),
        ("confirm you\u2019re not a bot", "err_botcheck"),
        ("sign in to confirm your age", "err_age"),
        ("age-restricted", "err_age"),
        ("private video", "err_private"),
        ("video is unavailable", "err_private"),
        ("video unavailable", "err_private"),
        ("has been removed", "err_private"),
        ("removed by the uploader", "err_private"),
        ("members-only", "err_members"),
        ("join this channel", "err_members"),
        ("not available in your country", "err_geo"),
        ("no video formats found", "err_no_formats"),
        ("requested format is not available", "err_no_formats"),
        ("sabr", "err_sabr"),
        ("http error 403", "err_sabr"),
        ("unable to download webpage", "err_dl_failed"),
        ("failed to resolve", "err_dl_failed"),
        ("temporary failure in name resolution", "err_dl_failed"),
        ("timed out", "err_dl_failed"),
    ]
    for needle, key in table:
        if needle in text:
            return key
    return "err_dl_failed"


# ---- YouTube -----------------------------------------------------------
def probe(url: str, cfg: Config, *, timeout_s: int = 120) -> MediaInfo:
    ensure_cookies(cfg)
    ensure_binaries(cfg, need_ffmpeg=False)
    argv = [
        *ytdlp_base(cfg, cfg.tmp_dir),
        "--skip-download", "--dump-single-json",
        "--", url,
    ]
    code, out = _run(argv, timeout_s=timeout_s)
    payload = None
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if payload is None:
        key = classify_error(out)
        raise YtDlpError(key, detail=out[-800:])
    thumbs = payload.get("thumbnails") or []
    return MediaInfo(
        video_id=payload.get("id") or "",
        title=payload.get("title") or "",
        uploader=payload.get("uploader") or payload.get("channel") or "",
        duration_s=int(payload["duration"]) if payload.get("duration") else None,
        filesize_hint=payload.get("filesize_approx") or payload.get("filesize") or None,
        height=payload.get("height") or None,
        vcodec=payload.get("vcodec") or None,
        acodec=payload.get("acodec") or None,
        ext=payload.get("ext") or "mp4",
        thumb_url=(thumbs[-1].get("url") if thumbs else None),
        availability=payload.get("availability"),
    )


def build_video_ladder(cfg: Config, choice: Choice, info: MediaInfo | None = None) -> list[list[str]]:
    """Spec 2.3: progressive <=cap, bv*+ba merge, audio-only rescue, alternate clients."""
    cap = f"{cfg.max_file_mb}M"
    height_cap = min(choice.height or cfg.max_height, cfg.max_height)
    return [
        ["-f", (f"best[height<={height_cap}][ext=mp4][filesize<={cap}]/"
                f"best[height<={height_cap}][ext=mp4]/18/b[height<=360][ext=mp4]")],
        ["-f", (f"bv*[height<={height_cap}][ext=mp4]+ba[ext=m4a]/"
                f"bv*[height<={height_cap}]+ba/b[height<={height_cap}]"),
         "--merge-output-format", "mp4"],
        ["-f", "bestaudio[ext=m4a]/bestaudio/best"],
        ["-f", (f"bv*[height<={height_cap}]+ba/b[height<={height_cap}]"),
         "--extractor-args", "youtube:player_client=web_safari,tv"],
    ]


def build_audio_argv(cfg: Config, choice: Choice, url: str) -> list[str]:
    if choice.audio_format == "mp3":
        # best audio once; the mp3 conversion is a separate ffmpeg call (spec 2.5)
        return [*ytdlp_base(cfg, cfg.tmp_dir), "-f", "bestaudio/best", "--", url]
    return [
        *ytdlp_base(cfg, cfg.tmp_dir),
        "-f", "bestaudio[ext=m4a]/bestaudio",
        "--extract-audio", "--audio-format", "m4a", "--audio-quality", "0",
        "--", url,
    ]


def to_mp3(src: Path, dst: Path, kbps: int, cfg: Config, *, timeout_s: int = 1800) -> Path:
    if kbps not in (64, 128, 192, 320):
        raise ValueError("unsupported mp3 bitrate")
    argv = [
        str(cfg.ffmpeg), "-hide_banner", "-nostdin", "-y", "-i", str(src),
        "-vn", "-sn", "-dn", "-map_metadata", "0",
        "-c:a", "libmp3lame", "-b:a", f"{kbps}k", "-ar", "44100", "-ac", "2",
        "-f", "mp3", str(dst),
    ]
    code, out = _run(argv, timeout_s=timeout_s)
    if code != 0 or not dst.exists() or dst.stat().st_size == 0:
        raise YtDlpError("err_dl_failed", detail=out[-600:] or "ffmpeg mp3 conversion failed")
    return dst


def ffprobe_stream(path: Path, cfg: Config) -> dict[str, str]:
    argv = [
        str(cfg.ffprobe), "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name,bit_rate,sample_rate,channels",
        "-of", "default=nw=1", str(path),
    ]
    code, out = _run(argv, timeout_s=60)
    if code != 0:
        return {}
    result: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def probe_audio_bitrate_kbps(path: Path, cfg: Config) -> int | None:
    info = ffprobe_stream(path, cfg)
    raw = info.get("bit_rate")
    if raw and raw.isdigit():
        return int(raw) // 1000
    return None


def _parse_progress(line: str, total: int | None,
                    cb: Callable[[float | None, str], None] | None) -> None:
    """yt-dlp --newline progress: '[download]  12.3% of 10.00MiB at 1.2MiB/s ETA 00:12'."""
    if cb is None or "[download]" not in line:
        return
    match = re.search(r"(\d+(?:\.\d+)?)%", line)
    if not match:
        return
    pct = float(match.group(1))
    speed = ""
    eta = ""
    sm = re.search(r"at\s+([\d.]+\w+/s)", line)
    if sm:
        speed = sm.group(1)
    em = re.search(r"ETA\s+(\d+:\d+(?::\d+)?)", line)
    if em:
        eta = em.group(1)
    detail = json.dumps({"speed": speed, "eta": eta, "total": total or 0})
    cb(pct, detail)


def download_youtube(url: str, choice: Choice, cfg: Config, *,
                     progress_cb: Callable[[float | None, str], None] | None = None,
                     cancel: threading.Event | None = None,
                     info: MediaInfo | None = None) -> DownloadResult:
    ensure_cookies(cfg)
    ensure_binaries(cfg, need_ffmpeg=True)
    ensure_pot_provider(cfg)
    cfg.tmp_dir.mkdir(parents=True, exist_ok=True)
    if info is None:
        info = probe(url, cfg)
    total = info.filesize_hint

    if choice.kind == "audio":
        argv = build_audio_argv(cfg, choice, url)
        code, out = _run(argv, timeout_s=cfg.job_timeout_s, cancel=cancel,
                         on_line=lambda line: _parse_progress(line, total, progress_cb))
        if code != 0:
            raise YtDlpError(classify_error(out), detail=out[-800:])
        produced = _newest_file(cfg.tmp_dir)
        if produced is None:
            raise YtDlpError("err_dl_failed", detail="no output file produced")
        final = produced
        if choice.audio_format == "mp3":
            dst = produced.with_suffix(".mp3")
            if progress_cb:
                progress_cb(None, json.dumps({"stage": "convert", "kbps": choice.mp3_kbps}))
            final = to_mp3(produced, dst, int(choice.mp3_kbps or 128), cfg)
            cleanup(produced)
        result = _finish(final, info, choice, cfg)
        result.audio_bitrate_kbps = probe_audio_bitrate_kbps(final, cfg)
        return result

    last_error = "err_dl_failed"
    last_detail = ""
    for index, extra in enumerate(build_video_ladder(cfg, choice, info)):
        rung_is_audio_only = index == 2
        argv = [*ytdlp_base(cfg, cfg.tmp_dir), *extra, "--", url]
        code, out = _run(argv, timeout_s=cfg.job_timeout_s, cancel=cancel,
                         on_line=lambda line: _parse_progress(line, total, progress_cb))
        if code != 0:
            last_error = classify_error(out)
            last_detail = out[-800:]
            continue
        produced = _newest_file(cfg.tmp_dir)
        if produced is None:
            last_error = "err_dl_failed"
            last_detail = "no output file produced"
            continue
        result = _finish(produced, info, choice, cfg)
        if rung_is_audio_only or produced.suffix.lower() in (".m4a", ".webm", ".opus"):
            result.degraded_from_video = True
            result.notes.append("err_degraded_audio")
        return result
    raise YtDlpError(last_error, detail=last_detail)


def _newest_file(directory: Path) -> Path | None:
    candidates = [p for p in directory.iterdir() if p.is_file() and not p.name.endswith(".part")]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _finish(path: Path, info: MediaInfo, choice: Choice, cfg: Config) -> DownloadResult:
    size = path.stat().st_size
    return DownloadResult(
        path=path, info=info, choice=choice, sha256=sha256_file(path), size=size,
    )


# ---- direct links ------------------------------------------------------
def download_direct(url: str, cfg: Config, *,
                    progress_cb: Callable[[float | None, str], None] | None = None,
                    token: str | None = None) -> DownloadResult:
    info = validate_direct_url(url)
    if info.kind is UrlKind.UNSUPPORTED_SITE:
        raise YtDlpError("err_unsupported_site", detail=info.host)
    if info.kind is UrlKind.BLOCKED:
        raise YtDlpError(info.reason or "err_blocked_host", detail=url)
    if info.kind is not UrlKind.DIRECT:
        raise YtDlpError(info.reason or "err_not_url", detail=url)

    cfg.tmp_dir.mkdir(parents=True, exist_ok=True)
    token = token or __import__("secrets").token_hex(8)
    ext = _extension_from_url(url)
    target = cfg.tmp_dir / f"{token}.{ext}"
    argv = [
        "curl", "-fSL", "--proto", "=http,https", "--max-redirs", "3",
        "--max-filesize", str(cfg.max_file_bytes),
        "--connect-timeout", "15", "--retry", "2", "--retry-delay", "3",
        "--speed-time", "60", "--speed-limit", "10000",
        "-A", "Mozilla/5.0 (compatible; heydar-downloader-bot/1.0)",
        "-o", str(target), "--", url,
    ]
    code, out = _run(argv, timeout_s=min(cfg.job_timeout_s, 1800))
    if code != 0 or not target.exists() or target.stat().st_size == 0:
        cleanup(target)
        raise YtDlpError(_curl_error(out), detail=out[-600:])
    size = target.stat().st_size
    if size > cfg.max_file_bytes:
        cleanup(target)
        raise YtDlpError("err_too_big", detail=f"{size} bytes")
    media = MediaInfo(video_id="", title=target.name, uploader=info.host, ext=ext)
    return DownloadResult(
        path=target, info=media, choice=Choice(kind="audio", audio_format="m4a"),
        sha256=sha256_file(target), size=size,
    )


def _curl_error(out: str) -> str:
    text = (out or "").lower()
    if "maximum file size exceeded" in text:
        return "err_too_big"
    if "403" in text:
        return "err_dl_failed"
    if "not resolve host" in text or "connection" in text:
        return "err_dl_failed"
    return "err_dl_failed"


def _extension_from_url(url: str) -> str:
    from urllib.parse import urlsplit

    name = Path(urlsplit(url).path).name
    if "." in name:
        ext = name.rsplit(".", 1)[-1].lower()
        if re.fullmatch(r"[a-z0-9]{1,10}", ext):
            return ext
    return "bin"


def disk_ok(cfg: Config) -> bool:
    from config import free_bytes

    free = free_bytes(cfg.root if cfg.root.exists() else Path("/"))
    return free < 0 or free > 2 * cfg.max_file_bytes
