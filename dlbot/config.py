"""Configuration, lifetime math and formatting helpers for the download bot.

Stdlib only: the bot must run on a bare GitHub Actions runner (Windows or Linux)
with no pip install.

Environment contract (task spec)
--------------------------------
TELEGRAM_BOT_API_ROOT    base URL of the Bot API (default http://127.0.0.1:8081)
TELEGRAM_DL_BOT_TOKEN    bot token (fallback: TELEGRAM_BOT_TOKEN, DLBOT_TOKEN)
DLBOT_ADMIN_ID           admin user id for /kill + alerts (default 180329538)
DLBOT_ROOT               bot root dir; files/ tmp/ logs/ live under it
                         (default %USERPROFILE%\\dlbot on Windows, /home/runner/dlbot)
YT_COOKIES_FILE          yt-dlp cookie file (fallback DLBOT_COOKIES,
                         default ~/.hermes/secrets/youtube_cookies.txt)
RUNNER_LIFETIME_MINUTES  remaining runner lifetime in minutes at bot start
                         (preferred). Fallback: JOB_TIMEOUT_MINUTES - elapsed
                         since JOB_START_EPOCH.
DLBOT_YTDLP / DLBOT_FFMPEG_DIR / DLBOT_MAX_FILE_MB / DLBOT_TTL_S ... overrides.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30), name="Asia/Tehran")
UTC = timezone.utc

IS_WINDOWS = os.name == "nt"

FA_DIGITS = str.maketrans("0123456789", "\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9")


class MissingConfig(RuntimeError):
    """Raised when a required environment variable is absent."""


def _exe(name: str) -> str:
    return f"{name}.exe" if IS_WINDOWS else name


def default_root() -> Path:
    env_root = os.environ.get("DLBOT_ROOT")
    if env_root:
        return Path(os.path.expanduser(os.path.expandvars(env_root)))
    if IS_WINDOWS:
        home = os.environ.get("USERPROFILE") or str(Path.home())
        return Path(home) / "dlbot"
    return Path("/home/runner/dlbot")


def default_cookies() -> Path:
    return Path.home() / ".hermes" / "secrets" / "youtube_cookies.txt"


def _find_binary(name: str, extra: tuple[str, ...] = ()) -> Path | None:
    """First existing candidate: PATH lookup, then a few known install dirs."""
    found = shutil.which(name) or shutil.which(_exe(name))
    if found:
        return Path(found)
    for candidate in extra:
        path = Path(candidate) / _exe(name)
        if path.exists():
            return path
    return None


@dataclass(frozen=True)
class Config:
    bot_token: str
    api_root: str = "http://127.0.0.1:8081"
    admin_id: int = 180329538
    root: Path = field(default_factory=default_root)
    ytdlp: Path | None = None
    node_bin: Path | None = None
    ffmpeg_dir: Path | None = None
    cookies: Path = field(default_factory=default_cookies)
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
    # RUNNER_LIFETIME_MINUTES: remaining runner life (seconds) measured at
    # lifetime_started_at. 0 = fall back to JOB_START_EPOCH math.
    lifetime_override_s: int = 0
    lifetime_started_at: float = 0.0

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
    def ffmpeg(self) -> Path | None:
        """Absolute ffmpeg path (not on PATH on the Linux runner)."""
        if self.ffmpeg_dir:
            candidate = Path(self.ffmpeg_dir)
            if candidate.is_file():
                return candidate
            exe = candidate / _exe("ffmpeg")
            if exe.exists():
                return exe
        return _find_binary("ffmpeg")

    @property
    def ffprobe(self) -> Path | None:
        if self.ffmpeg_dir:
            candidate = Path(self.ffmpeg_dir)
            if candidate.is_dir():
                exe = candidate / _exe("ffprobe")
                if exe.exists():
                    return exe
            elif candidate.name.startswith("ffprobe") and candidate.exists():
                return candidate
        if self.ffmpeg:
            sibling = self.ffmpeg.with_name(_exe("ffprobe"))
            if sibling.exists():
                return sibling
        return _find_binary("ffprobe")

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024

    def ytdlp_argv(self) -> list[str]:
        """yt-dlp lives at an absolute path on the runner; fall back to PATH / module."""
        if self.ytdlp:
            candidate = Path(self.ytdlp)
            if candidate.is_file():
                return [str(candidate)]
            if candidate.is_dir():
                exe = candidate / _exe("yt-dlp")
                if exe.exists():
                    return [str(exe)]
        found = shutil.which("yt-dlp") or shutil.which(_exe("yt-dlp"))
        if found:
            return [str(found)]
        return [sys.executable, "-m", "yt_dlp"]

    def node_argv(self) -> str:
        if self.node_bin and Path(self.node_bin).exists():
            return str(self.node_bin)
        return shutil.which("node") or "node"


def _env_path(env: Mapping[str, str], *keys: str, default: Path | None = None) -> Path | None:
    for key in keys:
        raw = env.get(key)
        if raw:
            return Path(os.path.expanduser(os.path.expandvars(raw)))
    return default


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Build a Config from the environment. Never logs or returns the token value."""
    env = os.environ if env is None else env

    token = ""
    for key in ("TELEGRAM_DL_BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "DLBOT_TOKEN"):
        token = (env.get(key) or "").strip()
        if token:
            break
    if not token or ":" not in token:
        raise MissingConfig(
            "no bot token: set TELEGRAM_DL_BOT_TOKEN (or TELEGRAM_BOT_TOKEN)")

    def _int(key: str, default: int) -> int:
        raw = env.get(key)
        if raw in (None, ""):
            return default
        try:
            return int(float(str(raw).strip().strip('"').strip("'")))
        except ValueError:
            return default

    ffmpeg_dir = _env_path(env, "DLBOT_FFMPEG_DIR")
    if ffmpeg_dir is None:
        # The Linux workflow keeps ffmpeg outside PATH; on Windows it is on PATH.
        for guess in (
            "/home/runner/.hermes/tools/ffmpeg-9.0.1-linux-x64/bin",
            str(Path.home() / ".hermes" / "tools"),
        ):
            candidate = Path(guess)
            if (candidate / _exe("ffmpeg")).exists():
                ffmpeg_dir = candidate
                break
    if ffmpeg_dir is None:
        detected = _find_binary("ffmpeg")
        ffmpeg_dir = detected.parent if detected else None

    ytdlp = _env_path(env, "DLBOT_YTDLP", default=None)
    if ytdlp is None:
        ytdlp = _find_binary("yt-dlp", ("/home/runner/.local/bin",))

    node_bin = _env_path(env, "DLBOT_NODE", default=None)

    lifetime_minutes = _int("RUNNER_LIFETIME_MINUTES", -1)
    override_s = 0
    started_at = 0.0
    if lifetime_minutes >= 0:
        override_s = lifetime_minutes * 60
        started_at = time.time()

    return Config(
        bot_token=token,
        api_root=(env.get("TELEGRAM_BOT_API_ROOT") or "http://127.0.0.1:8081").rstrip("/"),
        admin_id=_int("DLBOT_ADMIN_ID", 180329538),
        root=_env_path(env, "DLBOT_ROOT", default=default_root()) or default_root(),
        ytdlp=ytdlp,
        node_bin=node_bin,
        ffmpeg_dir=ffmpeg_dir,
        cookies=_env_path(env, "YT_COOKIES_FILE", "DLBOT_COOKIES",
                          default=default_cookies()) or default_cookies(),
        pot_url=env.get("DLBOT_POT_URL", "http://127.0.0.1:4416"),
        pot_script=_env_path(env, "DLBOT_POT_SCRIPT",
                             default=Path("/home/runner/bgutil-ytdlp-pot-provider/server/build/main.js"))
        or Path("/home/runner/bgutil-ytdlp-pot-provider/server/build/main.js"),
        max_file_mb=_int("DLBOT_MAX_FILE_MB", 500),
        max_height=_int("DLBOT_MAX_HEIGHT", 1080),
        max_duration_s=_int("DLBOT_MAX_DURATION_S", 7200),
        ttl_s=_int("DLBOT_TTL_S", 3600),
        max_concurrent=_int("DLBOT_MAX_CONCURRENT", 2),
        per_user_per_hour=_int("DLBOT_PER_USER_PER_HOUR", 10),
        job_timeout_minutes=_int("JOB_TIMEOUT_MINUTES", 335),
        job_start_epoch=_int("JOB_START_EPOCH", 0),
        job_timeout_s=_int("DLBOT_JOB_TIMEOUT_S", 900),
        tg_upload_limit_mb=_int("DLBOT_TG_UPLOAD_LIMIT_MB", 2000),
        max_queue=_int("DLBOT_MAX_QUEUE", 3),
        lifetime_override_s=override_s,
        lifetime_started_at=started_at,
    )


# ---- lifetime math (requirement 4) -------------------------------------
def remaining_seconds(cfg: Config, now: float | None = None) -> int:
    """Seconds until the GitHub Actions runner is shut down."""
    now = time.time() if now is None else now
    if cfg.lifetime_override_s:
        return int(max(0, cfg.lifetime_override_s - (now - cfg.lifetime_started_at)))
    if not cfg.job_start_epoch:
        return cfg.job_timeout_minutes * 60
    return int(max(0, cfg.job_timeout_minutes * 60 - (now - cfg.job_start_epoch)))


def lifetime_start_epoch(cfg: Config, now: float | None = None) -> float:
    """Epoch at which the runner started caring about the budget."""
    if cfg.lifetime_override_s:
        started = cfg.lifetime_started_at or (time.time() if now is None else now)
        return started
    return float(cfg.job_start_epoch or (time.time() if now is None else now))


def shutdown_at(cfg: Config, now: float | None = None) -> datetime:
    if cfg.lifetime_override_s:
        return datetime.fromtimestamp(lifetime_start_epoch(cfg, now) + cfg.lifetime_override_s, tz=UTC)
    start = cfg.job_start_epoch or int(time.time() if now is None else now)
    return datetime.fromtimestamp(start + cfg.job_timeout_minutes * 60, tz=UTC)


def shutdown_utc(cfg: Config, now: float | None = None) -> datetime:
    return shutdown_at(cfg, now)


def shutdown_tehran(cfg: Config, now: float | None = None) -> datetime:
    return shutdown_at(cfg, now).astimezone(TEHRAN_TZ)


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
    """Free space in bytes; -1 when unknown (never raise on any platform)."""
    try:
        st = os.statvfs(path)              # POSIX
        return st.f_bavail * st.f_frsize
    except (AttributeError, OSError):
        pass
    try:
        return shutil.disk_usage(str(path)).free   # Windows
    except OSError:
        return -1
