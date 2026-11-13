"""Persian string table + render() + inline keyboard builders (spec section 1)."""
from __future__ import annotations

from typing import Any

from config import Config, fa_digits, effective_ttl, human_duration, human_size
from config import remaining_seconds, shutdown_tehran, shutdown_utc, tehran_hhmm, utc_hhmm

MSG: dict[str, str] = {
    "welcome": (
        "\U0001f3ac <b>سلام {name}! به دانلودر خوش آمدی</b>\n\n"
        "\U0001f517 یک لینک یوتیوب یا یک لینک مستقیم (http/https) بفرست؛ من <b>خودِ فایل</b> را "
        "همین‌جا در چت برایت می‌فرستم، نه لینک.\n\n"
        "\u2699\ufe0f دستورها: /help \u2022 /status \u2022 /cancel\n\n{lt}"
    ),
    "help": (
        "\U0001f4d6 <b>راهنما — چه کاری می‌توانم انجام دهم؟</b>\n\n"
        "\U0001f3a5 <b>لینک یوتیوب</b> \u2192 منوی انتخاب فرمت باز می‌شود:\n"
        " \u2022 \U0001f4f9 ویدیو تا {max_p}p (mp4)\n"
        " \u2022 \U0001f3b5 فقط صدا: m4a اصلی (بدون تبدیل) یا mp3 با {rates} kbps\n"
        " \u2022 \U0001fab6 فشرده‌سازی ویدیو به {compress_p}\n\n"
        "\U0001f310 <b>لینک مستقیم</b> \u2192 هر آدرس http/https که به یک فایل ختم شود، بدون هیچ "
        "تغییری به‌صورت «سند» ارسال می‌شود (سقف {max_mb} مگابایت).\n\n"
        "\U0001f6ab <b>پشتیبانی نمی‌شود:</b> اینستاگرام، تیک‌تاک، توییتر و هر سایتی که کوکی لازم دارد.\n\n"
        "\U0001f4e6 <b>سقف‌ها:</b> حجم {max_mb} مگابایت \u2022 زمان ویدیو {max_h} ساعت \u2022 {max_p}p "
        "\u2022 {concurrent} دانلود همزمان \u2022 {per_hour} دانلود در ساعت به ازای هر کاربر\n\n"
        "\u23f3 <b>عمر فایل:</b> هر فایل حدود {ttl} دقیقه بعد از ارسال، یا با ری‌استارت سرور، پاک می‌شود.\n\n"
        "\u26a0\ufe0f سرور تقریباً هر ۶ ساعت یک‌بار ری‌استارت می‌شود و در آن بازه بات ۱۰ تا ۲۵ دقیقه "
        "آفلاین است. این طبیعی است.\n\n{lt}"
    ),
    "status": (
        "\U0001f4ca <b>وضعیت سرور</b>\n\n{lt}\n"
        "\U0001f6a6 دانلودهای در حال اجرا: <b>{active}</b> از {concurrent}\n"
        "\U0001f4e5 دانلودهای شما در یک ساعت گذشته: <b>{used}</b> از {per_hour}\n"
        "\U0001f4be فایل‌های آماده روی سرور: <b>{files}</b> ({used_mb} مگابایت)\n"
        "\U0001f9e9 نسخه yt-dlp: <code>{ytdlp_ver}</code>\n"
        "\U0001f5a5 میزبان: GitHub Actions (موقت)\n"
        "\u2705 بات فعال است"
    ),
    "lifetime_full": (
        "\u23f3 زمان باقی‌مانده تا ری‌استارت سرور: <b>{mm} دقیقه</b>\n"
        "\U0001f553 خاموشی: {utc} UTC — {tehran} به وقت تهران"
    ),
    "lifetime_short": (
        "\u23f3 این فایل تا حدود <b>{mm} دقیقه</b> دیگر روی سرور می‌ماند؛ بعد از ری‌استارت پاک می‌شود."
    ),
    "lifetime_clamped": (
        "\u23f3 عمر این فایل به {mm} دقیقه کاهش یافت چون سرور به‌زودی ری‌استارت می‌شود."
    ),
    "format_picker": (
        "\U0001f3ac <b>{title}</b>\n\n\U0001f464 {uploader}\n\u23f1 مدت: {duration}\n"
        "\U0001f4e6 حجم تقریبی: {size}\n\nچه فرمتی می‌خواهی؟"
    ),
    "btn_video": "\U0001f4f9 ویدیو (تا {max_p}p)",
    "btn_audio": "\U0001f3b5 فقط صدا",
    "btn_cancel": "\u274c انصراف",
    "audio_menu": (
        "\U0001f3b5 <b>فرمت صوتی را انتخاب کن</b>\n\n"
        "\U0001f3a7 <b>m4a</b> — صدای اصل ویدیو، بدون تبدیل و بدون افت کیفیت (پیشنهاد ما)\n"
        "\U0001f4bd <b>mp3</b> — تبدیل با ffmpeg، حجم کمتر و پخش روی همه دستگاه‌ها\n\n"
        "\U0001f4e6 تخمین حجم: {size}\n{lt}"
    ),
    "btn_audio_m4a": "\U0001f3a7 m4a اصل (بدون تبدیل)",
    "btn_mp3_64": "\U0001f4bd mp3 ۶۴ kbps (کوچک‌ترین)",
    "btn_mp3_128": "\U0001f3b5 mp3 ۱۲۸ kbps (استاندارد)",
    "btn_mp3_192": "\U0001f3b6 mp3 ۱۹۲ kbps (کیفیت خوب)",
    "btn_mp3_320": "\U0001f48e mp3 ۳۲۰ kbps (بهترین)",
    "btn_back": "\u2b05\ufe0f بازگشت",
    "compress_menu": (
        "\U0001fab6 <b>فشرده‌سازی ویدیو</b>\n\nکیفیت مورد نظر را انتخاب کن (حجم کم می‌شود ولی "
        "کیفیت پایین می‌آید):"
    ),
    "btn_compress_480": "\U0001faa4 ۴۸۰p — کم‌حجم",
    "btn_compress_720": "\u2696\ufe0f ۷۲۰p — متعادل",
    "btn_no_compress": "\U0001f3ac {max_p}p — بدون فشرده‌سازی",
    "queued": (
        "\U0001f552 درخواستت در صف قرار گرفت (نوبت: {pos}). به‌محض آزاد شدن، دانلود شروع می‌شود.\n{lt}"
    ),
    "progress_download": (
        "\u2b07 <b>در حال دانلود...</b>\n{bar} {pct}%\n\U0001f4e6 {done} از {total} مگابایت\n"
        "\U0001f680 سرعت: {speed} \u2022 \u23f1 باقی‌مانده: {eta}\n\U0001f3ac {title}"
    ),
    "progress_convert": "\U0001f39b <b>در حال تبدیل صدا...</b>\n\U0001f4bd mp3 {kbps} kbps\n\U0001f3ac {title}",
    "progress_upload": "\u2b06\ufe0f <b>در حال ارسال فایل به تلگرام...</b> {pct}%\n\U0001f4e6 {size} مگابایت",
    "caption_file": "\U0001f3ac {title}\n\U0001f4e6 {size} \u2022 \u23f1 {duration} \u2022 {quality}\n{lt_short}",
    "btn_resend": "\U0001f501 ارسال مجدد فایل",
    "resend_ok": "\U0001f501 فایل دوباره ارسال شد.\n{lt_short}",
    "expired": (
        "\u231b <b>این فایل منقضی شده است</b>\n\nفایل پاک شده یا سرور ری‌استارت شده است. فایل‌های من "
        "روی این سرور موقت‌اند و بعد از هر ری‌استارت از بین می‌روند.\n\n"
        "\U0001f504 لینک را دوباره بفرست تا از نو دانلود کنم.\n{lt}"
    ),
    "cancelled": "\U0001f512 درخواست لغو شد. هر وقت خواستی دوباره لینک بفرست.",
    "nothing_to_cancel": "\U0001f937 درخواست فعالی برای لغو کردن نداری.",
    "kill_done": "\U0001f6d1 بات و دانلودهای در حال اجرا متوقف شدند.\n\U0001f553 {time} UTC",
    "kill_denied": "\u26d4\ufe0f این دستور فقط برای مدیر است.\n\u2139\ufe0f راهنما: /help",
    "admin_alert": (
        "\U0001f6a8 <b>هشدار مدیر</b>\n\n\U0001f4cc {title}\n\U0001f4dd {detail}\n"
        "\U0001f194 کد: <code>{code}</code>\n\U0001f553 {time} UTC"
    ),
    "unknown_command": (
        "\U0001f937 این دستور را نمی‌شناسم.\n\u2139\ufe0f دستورها: /start \u2022 /help \u2022 /status \u2022 /cancel"
    ),
    # ---- errors (section 1.3) ----
    "err_not_url": (
        "\U0001f914 این یک لینک نیست.\n\nیک آدرس http/https یا یک لینک یوتیوب بفرست.\n\u2139\ufe0f راهنما: /help"
    ),
    "err_bad_scheme": "\u26d4\ufe0f این آدرس مجاز نیست.\nفقط لینک‌های http و https پذیرفته می‌شوند.",
    "err_blocked_host": "\u26d4\ufe0f این آدرس به شبکه داخلی اشاره می‌کند و برای امنیت سرور مجاز نیست.",
    "err_unsupported_site": (
        "\U0001f6ab <b>این سایت پشتیبانی نمی‌شود</b>\n\nبرای اینستاگرام، تیک‌تاک، توییتر و سایت‌های "
        "مشابه کوکی نداریم.\n\u2705 فقط یوتیوب و لینک‌های مستقیم (http/https) پشتیبانی می‌شوند.\n\n\u2139\ufe0f /help"
    ),
    "err_too_big": (
        "\U0001f4e6 <b>فایل بزرگ‌تر از سقف است</b>\n\nحجم: {size} \u2022 سقف: {max_mb} مگابایت\n"
        "\U0001f4a1 کیفیت پایین‌تر یا فرمت صوتی را انتخاب کن."
    ),
    "err_too_long": (
        "\u23f1 <b>ویدیو خیلی طولانی است</b>\n\nمدت: {duration} \u2022 سقف: {max_h} ساعت\n"
        "\U0001f4a1 برای ویدیوهای طولانی فقط حالت صوتی را امتحان کن."
    ),
    "err_tg_limit": (
        "\U0001f4e6 تلگرام این حجم را نمی‌پذیرد.\n\nحجم فایل: {size} \u2022 سقف ارسال: {limit} مگابایت\n"
        "\U0001f4a1 یک کیفیت پایین‌تر انتخاب کن."
    ),
    "err_private": "\U0001f512 این ویدیو خصوصی، حذف‌شده یا در دسترس نیست.",
    "err_members": "\U0001f510 این ویدیو فقط برای اعضای کانال است و قابل دانلود نیست.",
    "err_age": "\U0001f51e این ویدیو محدودیت سنی دارد و بدون ورود به حساب قابل دانلود نیست.",
    "err_botcheck": (
        "\U0001f916 یوتیوب درخواست ما را «ربات» تشخیص داد (کوکی‌ها منقضی شده‌اند).\n\n"
        "\U0001f4e8 به مدیر اطلاع داده شد؛ لطفاً کمی بعد دوباره تلاش کن."
    ),
    "err_geo": "\U0001f30d این ویدیو در کشور سرور ما در دسترس نیست.",
    "err_sabr": (
        "\u26a0\ufe0f یوتیوب فرمت این ویدیو را تغییر داده (SABR) و فعلاً دانلود نمی‌شود.\n\n"
        "\U0001f4a1 با «\U0001f3b5 فقط صدا» امتحان کن — معمولاً کار می‌کند."
    ),
    "err_no_formats": (
        "\U0001f615 فرمت مناسبی برای این ویدیو پیدا نشد.\n\n\U0001f4a1 کیفیت پایین‌تر یا حالت صوتی را امتحان کن."
    ),
    "err_cookies_missing": (
        "\U0001f36a فایل کوکی یوتیوب در دسترس نیست؛ دانلود از یوتیوب موقتاً کار نمی‌کند.\n\n"
        "\U0001f4e8 به مدیر اطلاع داده شد."
    ),
    "err_dl_failed": (
        "\u274c <b>دانلود ناموفق بود</b>\n\n\U0001f4dd دلیل: {reason}\n"
        "\U0001f504 دوباره تلاش کن؛ اگر تکرار شد به مدیر اطلاع بده."
    ),
    "err_dl_timeout": (
        "\u231b دانلود در زمان مجاز ({minutes} دقیقه) تمام نشد و لغو شد.\n"
        "\U0001f4a1 ویدیوی کوتاه‌تر یا کیفیت پایین‌تر را امتحان کن."
    ),
    "err_upload": (
        "\u274c <b>ارسال فایل به تلگرام ممکن نشد</b>\n\n\U0001f4dd دلیل: {reason}\n\U0001f194 کد: {code}\n"
        "\U0001f4e8 به مدیر اطلاع داده شد."
    ),
    "err_upload_retry": (
        "\U0001f504 ارسال ناموفق بود (تلاش {n} از {max})؛ تلاش مجدد تا {seconds} ثانیه دیگر..."
    ),
    "err_botapi_down": (
        "\U0001f527 سرور داخلی تلگرام بالا نیست؛ فعلاً امکان ارسال فایل بزرگ نیست.\n\n"
        "\U0001f4e8 به مدیر اطلاع داده شد؛ لطفاً چند دقیقه بعد دوباره تلاش کن."
    ),
    "err_no_time": (
        "\u23f3 <b>سرور در حال ری‌استارت است</b>\n\nزمان باقی‌مانده ({mm} دقیقه) برای دانلود کافی نیست.\n"
        "\U0001f504 لطفاً بعد از ری‌استارت (~۵ ساعت دیگر) دوباره لینک را بفرست."
    ),
    "err_rate_limit": (
        "\U0001f6a6 <b>محدودیت تعداد دانلود</b>\n\nسقف: {per_hour} دانلود در ساعت برای هر کاربر.\n"
        "\u23f3 دوباره تلاش کن تا {retry_in} دقیقه دیگر."
    ),
    "err_user_busy": (
        "\u26a0\ufe0f همین حالا یک دانلود فعال داری.\n\u23f3 صبر کن تا تمام شود، یا /cancel را بزن."
    ),
    "err_queue_full": (
        "\u23f3 <b>صف پر است</b>\n\n{concurrent} دانلود در حال اجرا و {queued} درخواست در انتظار.\n"
        "\U0001f504 چند دقیقه بعد دوباره تلاش کن."
    ),
    "err_disk_full": (
        "\U0001f4be فضای دیسک سرور پر شده است؛ فعلاً دانلود ممکن نیست.\n\n\U0001f4e8 به مدیر اطلاع داده شد."
    ),
    "err_internal": (
        "\u274c خطای غیرمنتظره‌ای رخ داد.\n\n\U0001f194 کد خطا: <code>{code}</code>\n"
        "\U0001f4e8 خطا برای مدیر ثبت شد.\n\U0001f504 دوباره تلاش کن یا /cancel را بزن."
    ),
    "err_stale_callback": "\u231b این پیام قدیمی شده است. لینک را دوباره بفرست.",
    "err_degraded_audio": (
        "\u26a0\ufe0f کیفیت ویدیویی این لینک قابل دانلود نبود؛ بهترین کیفیت صوتی فرستاده شد."
    ),
}


def cross_check() -> list[str]:
    """Return the list of keys referenced in code but missing from MSG (empty = ok)."""
    required = [
        "welcome", "help", "status", "lifetime_full", "lifetime_short", "lifetime_clamped",
        "format_picker", "btn_video", "btn_audio", "btn_cancel", "audio_menu", "btn_audio_m4a",
        "btn_mp3_64", "btn_mp3_128", "btn_mp3_192", "btn_mp3_320", "btn_back", "compress_menu",
        "btn_compress_480", "btn_compress_720", "btn_no_compress", "queued", "progress_download",
        "progress_convert", "progress_upload", "caption_file", "btn_resend", "resend_ok", "expired",
        "cancelled", "nothing_to_cancel", "kill_done", "kill_denied", "admin_alert",
        "unknown_command", "err_not_url", "err_bad_scheme", "err_blocked_host",
        "err_unsupported_site", "err_too_big", "err_too_long", "err_tg_limit", "err_private",
        "err_members", "err_age", "err_botcheck", "err_geo", "err_sabr", "err_no_formats",
        "err_cookies_missing", "err_dl_failed", "err_dl_timeout", "err_upload",
        "err_upload_retry", "err_botapi_down", "err_no_time", "err_rate_limit", "err_user_busy",
        "err_queue_full", "err_disk_full", "err_internal", "err_stale_callback",
    ]
    return [key for key in required if key not in MSG]


def render(key: str, /, *, fa: bool = True, **kw: Any) -> str:
    body = MSG.get(key)
    if body is None:
        return f"[missing string: {key}]"
    try:
        text = body.format(**kw)
    except (KeyError, IndexError):
        text = body
    return fa_digits(text) if fa else text


def lifetime_line(cfg: Config, now: float | None = None, *, short: bool = False) -> str:
    remain = remaining_seconds(cfg, now)
    minutes = remain // 60
    if short:
        return render("lifetime_short", mm=minutes)
    return render(
        "lifetime_full",
        mm=minutes,
        utc=utc_hhmm(shutdown_utc(cfg)),
        tehran=tehran_hhmm(shutdown_tehran(cfg)),
    )


def common_kwargs(cfg: Config) -> dict[str, Any]:
    return {
        "max_mb": cfg.max_file_mb,
        "max_p": cfg.max_height,
        "max_h": cfg.max_duration_s // 3600,
        "rates": "۶۴/۱۲۸/۱۹۲/۳۲۰",
        "compress_p": "۴۸۰p یا ۷۲۰p",
        "concurrent": cfg.max_concurrent,
        "per_hour": cfg.per_user_per_hour,
        "ttl": effective_ttl(cfg) // 60,
    }


# ---- keyboards (section 1.4) -------------------------------------------
def _btn(text: str, data: str) -> dict[str, str]:
    return {"text": text, "callback_data": data}


def kb_format(*, token: str, cfg: Config) -> list[list[dict[str, str]]]:
    return [
        [_btn(render("btn_video", max_p=cfg.max_height), f"fmt:video:{token}")],
        [_btn(render("btn_audio"), f"fmt:audio:{token}")],
        [_btn(render("btn_cancel"), f"fmt:cancel:{token}")],
    ]


def kb_audio(*, token: str) -> list[list[dict[str, str]]]:
    return [
        [_btn(render("btn_audio_m4a"), f"aud:m4a:{token}")],
        [_btn(render("btn_mp3_64"), f"aud:mp3:64:{token}"),
         _btn(render("btn_mp3_128"), f"aud:mp3:128:{token}")],
        [_btn(render("btn_mp3_192"), f"aud:mp3:192:{token}"),
         _btn(render("btn_mp3_320"), f"aud:mp3:320:{token}")],
        [_btn(render("btn_back"), f"fmt:back:{token}")],
        [_btn(render("btn_cancel"), f"fmt:cancel:{token}")],
    ]


def kb_compress(*, token: str, cfg: Config) -> list[list[dict[str, str]]]:
    return [
        [_btn(render("btn_compress_480"), f"vid:480:{token}"),
         _btn(render("btn_compress_720"), f"vid:720:{token}")],
        [_btn(render("btn_no_compress", max_p=cfg.max_height), f"vid:best:{token}")],
        [_btn(render("btn_back"), f"aud:back:{token}")],
    ]


def kb_resend(*, token: str) -> list[list[dict[str, str]]]:
    return [[_btn(render("btn_resend"), token)]]
