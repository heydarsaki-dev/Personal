"""Configuration, lifetime math and formatting helpers for the download bot.

Stdlib only: the bot must run on a bare GitHub Actions runner with no pip install.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30), name="Asia/Tehran")
UTC = timezone.utc

FA_DIGITS = str.maketrans("0123456789", "\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9")


class MissingConfig(RuntimeError):
    """Raised when a required environment variable is absent."""


@dataclass(frozen=True)
class Config:
    bot_token: str
    api_root: str = "http://127.0.0.1:8081"
    admin_id: int = 180329538
    root: Path = Path("/home/runner/dlbot")
    ytdlp: Path = Path("/home/runner/.local/bin/yt-dlp")
    node_bin: Path = Path("/opt/hostedtoolcache/node/24.22.2/x64/bin/node")
    ffmpeg_dir: Path = Path("/home/runner/.hermes/tools/ffmpeg-9.0.1-linux-x64/bin")
    cookies: Path = Path("/home/runner/.hermes/secrets/youtube_cookies.txt")
    pot_url: str = "http://127.0.0.1:4416"
    pot_script: Path = Path("/home/runner/bgutil-ytdlp-pot-provider/server/build/main.js")
    max_file_mb: int = 500
    max_height: int = 1080
    max_duration_s: int = 7200
    ttl_s: int = 3600
    max_concurrent: int = 2
    per_user_per_hour: int = 10
    min_remaining_s: int = 600
    ttl_clamp_margin_s: int = 300
    job_timeout_minutes: int = 335
    job_start_epoch: int = 0
    job_timeout_s: int = 900
    tg_upload_limit_mb: int = 2000
    max_queue: int = 3

    # ---- derived paths -------------------------------------------------
    @property
    def files_dir(self) -> Path:
        return self.root / "files"

    @property
    def tmp_dir(self) -> Path:
        return self.root / "tmp"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def db_path(self) -> Path:
        return self.root / "registry.sqlite3"

    @property
    def ffmpeg(self) -> Path:
        return self.ffmpeg_dir / "ffmpeg"

    @property
    def ffprobe(self) -> Path:
        return self.ffmpeg_dir / "ffprobe"

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024

    def ytdlp_argv(self) -> list[str]:
        """yt-dlp lives at an absolute path on the runner; fall back to PATH / module."""
        import shutil
        import sys as _sys

        if self.ytdlp.exists():
            return [str(self.ytdlp)]
        found = shutil.which("yt-dlp")
        if found:
            return [found]
        return [_sys.executable, "-m", "yt_dlp"]


def _env_path(env: Mapping[str, str], key: str, default: Path) -> Path:
    raw = env.get(key)
    return Path(raw) if raw else default


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Build a Config from the environment. Never logs or returns the token value."""
    env = os.environ if env is None else env
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token or ":" not in token:
        raise MissingConfig("TELEGRAM_BOT_TOKEN is missing or malformed")

    def _int(key: str, default: int) -> int:
        raw = env.get(key)
        try:
            return int(raw) if raw not in (None, "") else default
        except ValueError:
            return default

    return Config(
        bot_token=token,
        api_root=env.get("TELEGRAM_BOT_API_ROOT", "http://127.0.0.1:8081").rstrip("/"),
        admin_id=_int("DLBOT_ADMIN_ID", 180329538),
        root=_env_path(env, "DLBOT_ROOT", Path("/home/runner/dlbot")),
        ytdlp=_env_path(env, "DLBOT_YTDLP", Path("/home/runner/.local/bin/yt-dlp")),
        cookies=_env_path(env, "DLBOT_COOKIES", Path("/home/runner/.hermes/secrets/youtube_cookies.txt")),
        ffmpeg_dir=_env_path(
            env,
            "DLBOT_FFMPEG_DIR",
            Path("/home/runner/.hermes/tools/ffmpeg-9.0.1-linux-x64/bin"),
        ),
        max_file_mb=_int("DLBOT_MAX_FILE_MB", 500),
        max_height=_int("DLBOT_MAX_HEIGHT", 1080),
        ttl_s=_int("DLBOT_TTL_S", 3600),
        max_concurrent=_int("DLBOT_MAX_CONCURRENT", 2),
        per_user_per_hour=_int("DLBOT_PER_USER_PER_HOUR", 10),
        job_timeout_minutes=_int("JOB_TIMEOUT_MINUTES", 335),
        job_start_epoch=_int("JOB_START_EPOCH", 0),
    )


# ---- lifetime math (requirement 3) -------------------------------------
def remaining_seconds(cfg: Config, now: float | None = None) -> int:
    now = time.time() if now is None else now
    if not cfg.job_start_epoch:
        return cfg.job_timeout_minutes * 60
    return int(max(0, cfg.job_timeout_minutes * 60 - (now - cfg.job_start_epoch)))


def shutdown_at(cfg: Config) -> datetime:
    start = cfg.job_start_epoch or int(time.time())
    return datetime.fromtimestamp(start + cfg.job_timeout_minutes * 60, tz=UTC)


def shutdown_utc(cfg: Config) -> datetime:
    return shutdown_at(cfg)


def shutdown_tehran(cfg: Config) -> datetime:
    return shutdown_at(cfg).astimezone(TEHRAN_TZ)


def effective_ttl(cfg: Config, now: float | None = None) -> int:
    """TTL clamped so the file never outlives the runner by more than the margin."""
    remain = remaining_seconds(cfg, now)
    return int(max(60, min(cfg.ttl_s, remain - cfg.ttl_clamp_margin_s)))


def ttl_clamped(cfg: Config, now: float | None = None) -> bool:
    return effective_ttl(cfg, now) < cfg.ttl_s


def can_start_job(cfg: Config, now: float | None = None) -> bool:
    return remaining_seconds(cfg, now) >= cfg.min_remaining_s


# ---- formatting --------------------------------------------------------
def fa_digits(text: object) -> str:
    return str(text).translate(FA_DIGITS)


def human_size(n_bytes: int | None) -> str:
    if not n_bytes or n_bytes < 0:
        return "?"
    mb = n_bytes / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.2f} GB"
    if mb >= 1:
        return f"{mb:.1f} MB"
    return f"{mb:.2f} MB"


def human_duration(seconds: int | float | None) -> str:
    if seconds is None:
        return "?"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def human_speed(bps: float | None) -> str:
    if not bps or bps <= 0:
        return "?"
    if bps >= 1024 * 1024:
        return f"{bps / (1024 * 1024):.1f} MiB/s"
    if bps >= 1024:
        return f"{bps / 1024:.0f} KiB/s"
    return f"{bps:.0f} B/s"


def progress_bar(pct: float | None, width: int = 12) -> str:
    if pct is None:
        return "\u2591" * width
    pct = max(0.0, min(100.0, pct))
    filled = int(round(width * pct / 100.0))
    return "\u2588" * filled + "\u2591" * (width - filled)


def utc_hhmm(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%H:%M")


def tehran_hhmm(dt: datetime) -> str:
    return dt.astimezone(TEHRAN_TZ).strftime("%H:%M")


def free_bytes(path: Path) -> int:
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize
    except OSError:
        return -1
