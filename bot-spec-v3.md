# bot-spec-v3.md — Telegram download bot (Persian UI, GitHub Actions host)

Prep-run deliverable for `telegram-youtube-bot-plan-v3.md` (v3 supersedes v2).
Author: Developer, 2026-11-13 ~04:15 UTC. Docs-only pass: **no code executed, no server started,
no workflow file touched**. Every item below is either (a) evidence from the v2/v3 plan, (b) a
literal command, or (c) proposed design. Nothing here has been run end-to-end yet — see §6.

Scope of this file, per the brief:
1. complete Persian string table (every UI message, with emojis)
2. exact yt-dlp command lines (mandatory cookie/JS recipe, format ladder, ffmpeg mp3 conversions)
3. local telegram-bot-api server recipe + the 3 workflow YAML steps to add LATER
4. proposed bot module layout with function signatures

Legend for evidence tags:
`[E]` = verified on this runner earlier today (v2 §C/v3 §0), `[D]` = documented by Telegram/tdlib,
`[V]` = must be verified live on the next node.

---

## 0. Environment facts this spec is built on

| Fact | Value | Tag |
|---|---|---|
| yt-dlp | `/home/runner/.local/bin/yt-dlp`, version 2026.10.26 (re-checked 04:09 UTC) | [E] |
| node (for `--js-runtimes`) | `/opt/hostedtoolcache/node/24.22.2/x64/bin/node` (v24.22.2) | [E] |
| ffmpeg/ffprobe | `/home/runner/.hermes/tools/ffmpeg-9.0.1-linux-x64/bin/{ffmpeg,ffprobe}` — **not in PATH** | [E] |
| cookies | `/home/runner/.hermes/secrets/youtube_cookies.txt` (2728 B, 03:54 UTC) | [E] |
| bgutil POT provider | `127.0.0.1:4416`, started from `/home/runner/bgutil-ytdlp-pot-provider/server/build/main.js` | [E] |
| bot token | `TELEGRAM_BOT_TOKEN` (workflow secret) → `@heydar_downloader_bot` | [E] |
| admin chat id | `180329538` | [E] |
| job budget | `JOB_TIMEOUT_MINUTES=335`, `JOB_START_EPOCH` from "Record job start"; keep-alive capped by `RUN_MINUTES=350` | [E] |
| docker | `/usr/bin/docker` present; **no telegram-bot-api image cached locally** | [E] |
| api_id | `147006` | [E] (per v3 Q1) |
| api_hash | **value not present anywhere in this workspace** — must be delivered as a repo secret (proposed name `TELEGRAM_API_HASH`) | [V] |

Owner-confirmed target: `@heydar_downloader_bot`. The bot token is distinct from the Hermes
gateway token; the bot must run under `http://127.0.0.1:8081/bot<token>/...`, never the public API.

---

## 1. Persian string table (`strings.py`)

Rules
- All user-visible text is Persian, HTML `parse_mode` (bold only, no MarkdownV2 escaping bugs).
- Latin digits are converted to Persian digits at render time (`fa_digits()`, §4.1): the templates below
  are written with ASCII placeholders and are converted automatically.
- Every placeholder is documented in the `{}` name. `{lt}` = the lifetime line (§1.2).
- Never a bare link: the reply to a successful job is always the file itself.

### 1.1 Message keys

| key | Persian text |
|---|---|
| `welcome` | `🎬 <b>سلام {name}! به دانلودر خوش آمدی</b>\n\n🔗 یک لینک یوتیوب یا یک لینک مستقیم (http/https) بفرست؛ من <b>خودِ فایل</b> را همینجا در چت برایت میفرستم، نه لینک.\n\n⚙️ دستورها: /help • /status • /cancel\n\n{lt}` |
| `help` | `📖 <b>راهنما — چه کاری میتوانم انجام دهم؟</b>\n\n🎥 <b>لینک یوتیوب</b> → منوی انتخاب فرمت باز میشود:\n • 📹 ویدیو تا {max_p}p (mp4)\n • 🎵 فقط صدا: m4a اصلی (بدون تبدیل) یا mp3 با {rates} kbps\n • 🪶 فشردهسازی ویدیو به {compress_p}\n\n🌐 <b>لینک مستقیم</b> → هر آدرس http/https که به یک فایل ختم شود، بدون هیچ تغییری بهصورت «سند» ارسال میشود (سقف {max_mb} مگابایت).\n\n🚫 <b>پشتیبانی نمیشود:</b> اینستاگرام، تیکتاک، توییتر و هر سایتی که کوکی لازم دارد.\n\n📦 <b>سقفها:</b> حجم {max_mb} مگابایت • زمان ویدیو {max_h} ساعت • {max_p}p • {concurrent} دانلود همزمان • {per_hour} دانلود در ساعت به ازای هر کاربر\n\n⏳ <b>عمر فایل:</b> هر فایل حدود {ttl} دقیقه بعد از ارسال، یا با ریاستارت سرور، پاک میشود.\n\n⚠️ سرور تقریباً هر ۶ ساعت یکبار ریاستارت میشود و در آن بازه بات ۱۰ تا ۲۵ دقیقه آفلاین است. این طبیعی است.\n\n{lt}` |
| `status` | `📊 <b>وضعیت سرور</b>\n\n{lt}\n🚦 دانلودهای در حال اجرا: <b>{active}</b> از {concurrent}\n📥 دانلودهای شما در یک ساعت گذشته: <b>{used}</b> از {per_hour}\n💾 فایلهای آماده روی سرور: <b>{files}</b> ({used_mb} مگابایت)\n🧩 نسخه yt-dlp: <code>{ytdlp_ver}</code>\n🖥 میزبان: GitHub Actions (موقت)\n✅ بات فعال است` |
| `lifetime_full` | `⏳ زمان باقیمانده تا ریاستارت سرور: <b>{mm} دقیقه</b>\n🕓 خاموشی: {utc} UTC — {tehran} به وقت تهران` |
| `lifetime_short` | `⏳ این فایل تا حدود <b>{mm} دقیقه</b> دیگر روی سرور میماند؛ بعد از ریاستارت پاک میشود.` |
| `lifetime_clamped` | `⏳ عمر این فایل به {mm} دقیقه کاهش یافت چون سرور بهزودی ریاستارت میشود.` |
| `format_picker` | `🎬 <b>{title}</b>\n\n👤 {uploader}\n⏱ مدت: {duration}\n📦 حجم تقریبی: {size}\n\nچه فرمتی میخواهی؟` |
| `btn_video` | `📹 ویدیو (تا {max_p}p)` |
| `btn_audio` | `🎵 فقط صدا` |
| `btn_cancel` | `❌ انصراف` |
| `audio_menu` | `🎵 <b>فرمت صوتی را انتخاب کن</b>\n\n🎧 <b>m4a</b> — صدای اصل ویدیو، بدون تبدیل و بدون افت کیفیت (پیشنهاد ما)\n💽 <b>mp3</b> — تبدیل با ffmpeg، حجم کمتر و پخش روی همه دستگاهها\n\n📦 تخمین حجم: {size}\n{lt}` |
| `btn_audio_m4a` | `🎧 m4a اصل (بدون تبدیل)` |
| `btn_mp3_64` | `💽 mp3 ۶۴ kbps (کوچکترین)` |
| `btn_mp3_128` | `🎵 mp3 ۱۲۸ kbps (استاندارد)` |
| `btn_mp3_192` | `🎶 mp3 ۱۹۲ kbps (کیفیت خوب)` |
| `btn_mp3_320` | `💎 mp3 ۳۲۰ kbps (بهترین)` |
| `btn_back` | `⬅️ بازگشت` |
| `compress_menu` | `🪶 <b>فشردهسازی ویدیو</b>\n\nکیفیت مورد نظر را انتخاب کن (حجم کم میشود ولی کیفیت پایین میآید):` |
| `btn_compress_480` | `🪶 ۴۸۰p — کمحجم` |
| `btn_compress_720` | `⚖️ ۷۲۰p — متعادل` |
| `btn_no_compress` | `🎬 {max_p}p — بدون فشردهسازی` |
| `queued` | `🕒 درخواستت در صف قرار گرفت (نوبت: {pos}). بهمحض آزاد شدن، دانلود شروع میشود.\n{lt}` |
| `progress_download` | `⏬ <b>در حال دانلود…</b>\n{bar} {pct}%\n📦 {done} از {total} مگابایت\n🚀 سرعت: {speed} • ⏱ باقیمانده: {eta}\n🎬 {title}` |
| `progress_convert` | `🎛 <b>در حال تبدیل صدا…</b>\n💽 mp3 {kbps} kbps\n🎬 {title}` |
| `progress_upload` | `⬆️ <b>در حال ارسال فایل به تلگرام…</b> {pct}%\n📦 {size} مگابایت` |
| `caption_file` | `🎬 {title}\n📦 {size} • ⏱ {duration} • {quality}\n{lt_short}` |
| `btn_resend` | `🔁 ارسال مجدد فایل` |
| `resend_ok` | `🔁 فایل دوباره ارسال شد.\n{lt_short}` |
| `expired` | `⌛ <b>این فایل منقضی شده است</b>\n\nفایل پاک شده یا سرور ریاستارت شده است. فایلهای من روی این سرور موقتاند و بعد از هر ریاستارت از بین میروند.\n\n🔄 لینک را دوباره بفرست تا از نو دانلود کنم.\n{lt}` |
| `cancelled` | `🔕 درخواست لغو شد. هر وقت خواستی دوباره لینک بفرست.` |
| `nothing_to_cancel` | `🤷 درخواست فعالی برای لغو کردن نداری.` |
| `kill_done` | `🛑 بات و دانلودهای در حال اجرا متوقف شدند.\n🕓 {time} UTC` |
| `kill_denied` | `⛔️ این دستور فقط برای مدیر است.\nℹ️ راهنما: /help` |
| `admin_alert` | `🚨 <b>هشدار مدیر</b>\n\n📌 {title}\n📝 {detail}\n🆔 کد: <code>{code}</code>\n🕓 {time} UTC` |

### 1.2 Placeholder formats

- `{lt}` → `strings.render("lifetime_full")` : two lines, e.g.
  `⏳ زمان باقیمانده تا ریاستارت سرور: ۱۴۳ دقیقه` / `🕓 خاموشی: 04:36 UTC — 08:06 به وقت تهران`
- `{lt_short}` → `lifetime_short`, single line for captions (captions should stay < 1024 chars).
- `{mm}` = `remaining_seconds() // 60`, `{utc}` = `HH:MM` UTC of `JOB_START_EPOCH + 335*60`,
  `{tehran}` = same instant in UTC+3:30 (`Asia/Tehran`, no DST since 2022).
- `{duration}` = `HH:MM:SS`, `{size}`/`{done}`/`{total}` = `12.4 MB`/`1.2 GB` (`human_mb()`),
  `{speed}` = `3.4 MiB/s`, `{eta}` = `00:42`, `{bar}` = 12-char bar `████████░░░░` (pct-driven).
- `{quality}` = `1080p • mp4` for video, `128 kbps • mp3` or `m4a (اصل)` for audio.
- `{rates}` = `۶۴/۱۲۸/۱۹۲/۳۲۰`, `{compress_p}` = `۴۸۰p یا ۷۲۰p`.
- Defaults come from `Config` (§4.1): `max_mb=500`, `max_p=1080`, `max_h=2`, `ttl=60`,
  `concurrent=2`, `per_hour=10`.

### 1.3 Error cases (one message per failure, bot must stay alive)

| key | trigger | Persian text |
|---|---|---|
| `err_not_url` | text is not a URL | `🤔 این یک لینک نیست.\n\nیک آدرس http/https یا یک لینک یوتیوب بفرست.\nℹ️ راهنما: /help` |
| `err_bad_scheme` | `file://`, `ftp://`, junk scheme | `⛔️ این آدرس مجاز نیست.\nفقط لینکهای http و https پذیرفته میشوند.` |
| `err_blocked_host` | SSRF guard hit (loopback/private/link-local/metadata) | `⛔️ این آدرس به شبکه داخلی اشاره میکند و برای امنیت سرور مجاز نیست.` |
| `err_unsupported_site` | instagram/tiktok/twitter/… | `🚫 <b>این سایت پشتیبانی نمیشود</b>\n\nبرای اینستاگرام، تیکتاک، توییتر و سایتهای مشابه کوکی نداریم.\n✅ فقط یوتیوب و لینکهای مستقیم (http/https) پشتیبانی میشوند.\n\nℹ️ /help` |
| `err_too_big` | > `max_mb` (precheck or actual) | `📦 <b>فایل بزرگتر از سقف است</b>\n\nحجم: {size} • سقف: {max_mb} مگابایت\n💡 کیفیت پایینتر یا فرمت صوتی را انتخاب کن.` |
| `err_too_long` | duration > `max_h` | `⏱ <b>ویدیو خیلی طولانی است</b>\n\nمدت: {duration} • سقف: {max_h} ساعت\n💡 برای ویدیوهای طولانی فقط حالت صوتی را امتحان کن.` |
| `err_tg_limit` | file > Bot API server cap (>2000 MB) or fallback to public API (50 MB) | `📦 تلگرام این حجم را نمیپذیرد.\n\nحجم فایل: {size} • سقف ارسال: {limit} مگابایت\n💡 یک کیفیت پایینتر انتخاب کن.` |
| `err_private` | "Private video" / "This video is unavailable" | `🔒 این ویدیو خصوصی، حذفشده یا در دسترس نیست.` |
| `err_members` | "members-only" / "Join this channel" | `🔐 این ویدیو فقط برای اعضای کانال است و قابل دانلود نیست.` |
| `err_age` | "Sign in to confirm your age" | `🔞 این ویدیو محدودیت سنی دارد و بدون ورود به حساب قابل دانلود نیست.` |
| `err_botcheck` | "Sign in to confirm you're not a bot" | `🤖 یوتیوب درخواست ما را «ربات» تشخیص داد (کوکیها منقضی شدهاند).\n\n📨 به مدیر اطلاع داده شد؛ لطفاً کمی بعد دوباره تلاش کن.` |
| `err_geo` | "not available in your country" | `🌍 این ویدیو در کشور سرور ما در دسترس نیست.` |
| `err_sabr` | HTTP 403 after format selection on SABR-only renditions | `⚠️ یوتیوب فرمت این ویدیو را تغییر داده (SABR) و فعلاً دانلود نمیشود.\n\n💡 با «🎵 فقط صدا» امتحان کن — معمولاً کار میکند.` |
| `err_no_formats` | "No video formats found" / ladder exhausted | `😕 فرمت مناسبی برای این ویدیو پیدا نشد.\n\n💡 کیفیت پایینتر یا حالت صوتی را امتحان کن.` |
| `err_cookies_missing` | cookies file absent/unreadable | `🍪 فایل کوکی یوتیوب در دسترس نیست؛ دانلود از یوتیوب موقتاً کار نمیکند.\n\n📨 به مدیر اطلاع داده شد.` |
| `err_dl_failed` | generic yt-dlp/curl failure | `❌ <b>دانلود ناموفق بود</b>\n\n📝 دلیل: {reason}\n🔄 دوباره تلاش کن؛ اگر تکرار شد به مدیر اطلاع بده.` |
| `err_dl_timeout` | per-job timeout hit | `⌛ دانلود در زمان مجاز ({minutes} دقیقه) تمام نشد و لغو شد.\n💡 ویدیوی کوتاهتر یا کیفیت پایینتر را امتحان کن.` |
| `err_upload` | sendDocument/sendVideo failed after retries | `❌ <b>ارسال فایل به تلگرام ممکن نشد</b>\n\n📝 دلیل: {reason}\n🆔 کد: {code}\n📨 به مدیر اطلاع داده شد.` |
| `err_upload_retry` | transient 429/5xx (informational edit) | `🔄 ارسال ناموفق بود (تلاش {n} از {max})؛ تلاش مجدد تا {seconds} ثانیه دیگر…` |
| `err_botapi_down` | `getMe` via 127.0.0.1:8081 fails | `🔧 سرور داخلی تلگرام بالا نیست؛ فعلاً امکان ارسال فایل بزرگ نیست.\n\n📨 به مدیر اطلاع داده شد؛ لطفاً چند دقیقه بعد دوباره تلاش کن.` |
| `err_no_time` | < 10 min of job budget left | `⏳ <b>سرور در حال ریاستارت است</b>\n\nزمان باقیمانده ({mm} دقیقه) برای دانلود کافی نیست.\n🔄 لطفاً بعد از ریاستارت (~۵ ساعت دیگر) دوباره لینک را بفرست.` |
| `err_rate_limit` | > `per_hour` per user per hour | `🚦 <b>محدودیت تعداد دانلود</b>\n\nسقف: {per_hour} دانلود در ساعت برای هر کاربر.\n⏳ دوباره تلاش کن تا {retry_in} دقیقه دیگر.` |
| `err_user_busy` | user already has a running job | `⚠️ همین حالا یک دانلود فعال داری.\n⏳ صبر کن تا تمام شود، یا /cancel را بزن.` |
| `err_queue_full` | `max_concurrent` busy and queue > 3 | `⏳ <b>صف پر است</b>\n\n{concurrent} دانلود در حال اجرا و {queued} درخواست در انتظار.\n🔄 چند دقیقه بعد دوباره تلاش کن.` |
| `err_disk_full` | free space < 2× `max_mb` | `💾 فضای دیسک سرور پر شده است؛ فعلاً دانلود ممکن نیست.\n\n📨 به مدیر اطلاع داده شد.` |
| `err_internal` | unhandled exception | `❌ خطای غیرمنتظرهای رخ داد.\n\n🆔 کد خطا: <code>{code}</code>\n📨 خطا برای مدیر ثبت شد.\n🔄 دوباره تلاش کن یا /cancel را بزن.` |
| `err_stale_callback` | callback on a dead/old message | `⌛ این پیام قدیمی شده است. لینک را دوباره بفرست.` |

Also (not errors, but fixed replies): `unknown_command` =
`🤷 این دستور را نمیشناسم.\nℹ️ دستورها: /start • /help • /status • /cancel`.

### 1.4 Inline keyboard payloads

```
format picker : [[("btn_video","fmt:video")], [("btn_audio","fmt:audio")], [("btn_cancel","fmt:cancel")]]
audio menu    : [[("btn_audio_m4a","aud:m4a")],
                 [("btn_mp3_64","aud:mp3:64"), ("btn_mp3_128","aud:mp3:128")],
                 [("btn_mp3_192","aud:mp3:192"), ("btn_mp3_320","aud:mp3:320")],
                 [("btn_back","fmt:back")], [("btn_cancel","fmt:cancel")]]
compress menu : [[("btn_compress_480","vid:480"), ("btn_compress_720","vid:720")],
                 [("btn_no_compress","vid:best")], [("btn_back","aud:back")]]
delivered file: [[("btn_resend","<token>")]]          # D3: callback_data == file token (uuid4 hex, 32 chars)
```
`callback_data` must stay ≤ 64 bytes → token is 32 hex chars, prefixes are ≤ 8 bytes. ✓

---

## 2. Download commands

### 2.1 MANDATORY common prefix (all YouTube calls)

```bash
YTDLP=/home/runner/.local/bin/yt-dlp
FFMPEG=/home/runner/.hermes/tools/ffmpeg-9.0.1-linux-x64/bin/ffmpeg
FFPROBE=/home/runner/.hermes/tools/ffmpeg-9.0.1-linux-x64/bin/ffprobe
COOKIES=/home/runner/.hermes/secrets/youtube_cookies.txt
BOTROOT=/home/runner/dlbot                      # DL_DIR=$BOTROOT/files, TMP_DIR=$BOTROOT/tmp
```

```
"$YTDLP"
  --cookies "$COOKIES"
  --js-runtimes node
  --remote-components ejs:github
  --ffmpeg-location "$FFMPEG"
  --no-playlist --no-warnings --newline --no-progress
  --retries 3 --fragment-retries 3 --socket-timeout 20
  --paths "home:$TMP_DIR" --paths "temp:$TMP_DIR"
  --output "%(id)s.%(ext)s"
  --print after_move:filepath          # last stdout line = final absolute path
```
`--cookies … --js-runtimes node --remote-components ejs:github` are **mandatory**: [E] the same
cookies fail with *"Sign in to confirm you're not a bot"* without the two JS flags. `--ffmpeg-location`
must be the **absolute** path: ffmpeg is not in PATH ([E]).
Do **not** use `--extractor-args youtube:player_client=ios` — [E] it ends in "No video formats found".

POT provider: before every job, probe `http://127.0.0.1:4416/ping`; if dark, start
`node /home/runner/bgutil-ytdlp-pot-provider/server/build/main.js` in the background [E].

### 2.2 Precheck (metadata + size, no download) — gates the ladder

```bash
"$YTDLP" <common> --skip-download --dump-single-json \
  --print "%(id)s|%(title)s|%(uploader)s|%(duration)s|%(filesize_approx)s|%(height)s|%(vcodec)s|%(acodec)s|%(ext)s|%(availability)s" \
  -- "$URL"
```
`--dump-single-json` also gives `thumbnails[]` for the thumbnail; `filesize_approx` is a hint only —
the ladder + `err_too_big` after the real download is authoritative.

### 2.3 Video format ladder (`[V]` on the next node)

Try in order; stop at the first success. Each rung is a separate invocation with the §2.1 prefix.
`CAP=${MAX_MB}M` (500 MB), `H=${MAX_HEIGHT}` (1080), `HC=${HEIGHT_CAP}` (user choice: 480/720/1080).

1. **Progressive mp4 ≤ cap** (no merge — cheapest, survives SABR better):
   `-f "best[height<=${HC}][ext=mp4][filesize<=${CAP}]/best[height<=${HC}][ext=mp4]/18/b[height<=360][ext=mp4]"`
2. **Video-only + audio merge** (the classic path):
   `-f "bv*[height<=${HC}][ext=mp4]+ba[ext=m4a]/bv*[height<=${HC}]+ba/b[height<=${HC}]" --merge-output-format mp4`
   (plus `--postprocessor-args "Merger:-c copy -movflags +faststart"` is *not* used — keep yt-dlp defaults.)
3. **Audio-only fallback** (also the automatic rescue when rungs 1–2 return SABR/403):
   `-f "bestaudio[ext=m4a]/bestaudio/best"` — if the user asked for video, deliver the audio and say so
   in the caption with `err_sabr`/`err_no_formats`-style explanation. This is a *success* path, not an error.
4. Retry rung 2 once with an alternate client **only after** 1–3 failed:
   `-f "bv*[height<=${HC}]+ba/b[height<=${HC}]" --extractor-args "youtube:player_client=web_safari,tv"`

Per-job hard timeout: `timeout 900 "$YTDLP" …` (15 min) → `err_dl_timeout`. Kill the whole process
group on cancel/SIGTERM (`os.killpg`).

### 2.4 Audio: original m4a (no re-encode)

```bash
"$YTDLP" <common> -f "bestaudio[ext=m4a]/bestaudio" --extract-audio --audio-format m4a --audio-quality 0 -- "$URL"
```
If the source is not m4a (opus/webm), `--audio-format m4a` re-muxes; when remux is impossible, fall
back to the next rung and tell the user (caption quality = `m4a`). No ffmpeg re-encode for m4a. [V]

### 2.5 mp3 conversion — exact ffmpeg commands (64/128/192/320 kbps)

Download the *best* audio stream once (`-f "bestaudio/best"`, keeping the original file), then convert:

```bash
# 64 kbps  — smallest, voice/podcast
"$FFMPEG" -hide_banner -nostdin -y -i "$SRC" -vn -sn -dn -map_metadata 0 \
  -c:a libmp3lame -b:a 64k -ar 44100 -ac 2 -f mp3 "$DST.mp3"

# 128 kbps — standard
"$FFMPEG" -hide_banner -nostdin -y -i "$SRC" -vn -sn -dn -map_metadata 0 \
  -c:a libmp3lame -b:a 128k -ar 44100 -ac 2 -f mp3 "$DST.mp3"

# 192 kbps — good
"$FFMPEG" -hide_banner -nostdin -y -i "$SRC" -vn -sn -dn -map_metadata 0 \
  -c:a libmp3lame -b:a 192k -ar 44100 -ac 2 -f mp3 "$DST.mp3"

# 320 kbps — best (requires 44.1 kHz stereo source; if the source is mono, drop -ac 2)
"$FFMPEG" -hide_banner -nostdin -y -i "$SRC" -vn -sn -dn -map_metadata 0 \
  -c:a libmp3lame -b:a 320k -ar 44100 -ac 2 -f mp3 "$DST.mp3"
```
Verification command (V3 evidence):
```bash
"$FFPROBE" -v error -select_streams a:0 -show_entries stream=codec_name,bit_rate,sample_rate,channels \
  -of default=nw=1 "$DST.mp3"
```
Equivalent yt-dlp-only alternative (if we ever want a single process):
`--extract-audio --audio-format mp3 --audio-quality ${KBPS}K --postprocessor-args "ffmpeg:-b:a ${KBPS}k"`.
The direct ffmpeg call above is preferred: it keeps the original audio file (re-usable for a second
quality without re-downloading) and gives an exact, greppable command for the spec.

### 2.6 Non-YouTube direct link (`curl` + SSRF guards)

Pre-flight in Python: parse scheme (http/https only), resolve host, reject if **any** resolved address
is loopback/private/link-local/CGNAT/metadata (`127.0.0.0/8`, `10/8`, `172.16/12`, `192.168/16`,
`169.254/16`, `::1`, `fc00::/7`, `fe80::/10`, `100.64/10`), cap redirects, re-validate each hop.

```bash
curl -fSL --proto '=http,https' --max-redirs 3 --max-filesize $(( MAX_MB * 1024 * 1024 )) \
  --connect-timeout 15 --retry 2 --retry-delay 3 --speed-time 60 --speed-limit 10000 \
  -A 'Mozilla/5.0 (compatible; heydar-downloader-bot/1.0)' \
  -o "$TMP_DIR/$TOKEN.bin" -- "$URL"
```
`file`: keep the server-suggested extension only when it matches `^[A-Za-z0-9]{1,10}$` (else `.bin`),
send as **document**, no re-encode (V4: sha256 must be identical). Instagram/TikTok/Twitter hosts are
rejected up front with `err_unsupported_site` [D7].

### 2.7 Failure mapping (`classify_error(stderr)`)

| stderr fragment | key |
|---|---|
| `Sign in to confirm you're not a bot` | `err_botcheck` (+ admin alert, + `install_cookies.sh` hint) |
| `Sign in to confirm your age` / `age-restricted` | `err_age` |
| `Private video` / `video is unavailable` / `removed` | `err_private` |
| `members-only` / `Join this channel` | `err_members` |
| `not available in your country` | `err_geo` |
| `HTTP Error 403` (after format selection) / `SABR` | `err_sabr` |
| `No video formats found` | `err_no_formats` |
| `Unable to download webpage` / DNS / TLS | `err_dl_failed` |
| cookies file missing | `err_cookies_missing` |

---

## 3. Local Telegram Bot API server

### 3.1 Why it is not optional

| | public Bot API | local `telegram-bot-api --local` |
|---|---|---|
| upload from a file | **50 MB** | **2000 MB** |
| send by URL | 20 MB | — |
| download a file the bot receives | 20 MB | no limit |
| how a file is handed to the API | multipart upload, re-encrypted through a DC | **local path** (`video=/home/runner/dlbot/files/x.mp4`) — the server reads the file off the same disk, no upload copy |
[D] core.telegram.org/bots/api#sending-files + tdlib/telegram-bot-api README. Consequence: a 500 MB
video (D5 cap) is deliverable *only* with the local server; on the public API every file >50 MB would
have to be refused with `err_tg_limit`. `--local` also means no multipart cost for a 500 MB file
(no double disk usage, no upload time), which matters on a 335-minute ephemeral job (R1).

### 3.2 Install (step 1 of the workflow, runs at node start)

Docker (preferred if the image pulls cleanly — unverified which tag exists, check at first run):
```bash
docker pull aiogram/telegram-bot-api:latest        # community image; [V] confirm it exists/pulls
```
Build from source (fallback, ~2–3 min, cached; [V] not yet run):
```bash
sudo apt-get install -y --no-install-recommends cmake g++ make zlib1g-dev libssl-dev libboost-dev 2>/dev/null || true
git clone --depth 1 https://github.com/tdlib/telegram-bot-api.git /home/runner/telegram-bot-api
cmake -S /home/runner/telegram-bot-api -B /home/runner/telegram-bot-api/build \
      -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/home/runner/.hermes/tools/telegram-bot-api
cmake --build /home/runner/telegram-bot-api/build --target install -j"$(nproc)"
```
Binary: `/home/runner/.hermes/tools/telegram-bot-api/bin/telegram-bot-api`.

### 3.3 Run

```bash
mkdir -p /home/runner/dlbot/tgbotapi /home/runner/dlbot/files /home/runner/dlbot/logs
/home/runner/.hermes/tools/telegram-bot-api/bin/telegram-bot-api \
  --api-id=147006 \
  --api-hash="$TELEGRAM_API_HASH" \
  --local \
  --http-address=127.0.0.1 --http-port=8081 \
  --dir=/home/runner/dlbot/tgbotapi \
  --temp-dir=/home/runner/dlbot/tmp \
  --log=/home/runner/dlbot/logs/botapi.log \
  >> /home/runner/dlbot/logs/botapi.stdout 2>&1 &
echo $! > /home/runner/dlbot/botapi.pid
```
Bot then talks to `http://127.0.0.1:8081/bot${TELEGRAM_BOT_TOKEN}/<method>` (`getMe`, `getUpdates`,
`sendVideo`, `sendDocument`, …) — same API surface, and the file field accepts an absolute path.
`api_id=147006` is given; **`api_hash` is not in this workspace** — it must be added as the repo
secret `TELEGRAM_API_HASH` before the workflow step can work [V]. Never echo either to the job log.
Bind to `127.0.0.1` only: port 8081 must not be published.

### 3.4 Workflow YAML — the 3 steps to add LATER (not added now)

**Placement (important):** the two *start* steps go **immediately BEFORE the existing `Keep alive`
step**; the *stop* step goes **immediately before the existing `Back up state to GitHub` step**
(line ~1088). Reason: steps after `Keep alive` only execute once the keep-alive window has ended, so a
bot started there would live for seconds. Nothing else in the `Server` job may change.

```yaml
      # ---- 1) install + start local Bot API server (insert BEFORE "Keep alive") ----
      - name: Start local Telegram Bot API server
        if: always()
        env:
          TELEGRAM_API_ID: "147006"
          TELEGRAM_API_HASH: ${{ secrets.TELEGRAM_API_HASH }}
        run: |
          set -euo pipefail
          BOTROOT=/home/runner/dlbot
          mkdir -p "$BOTROOT"/{files,tmp,logs,tgbotapi}
          BIN=/home/runner/.hermes/tools/telegram-bot-api/bin/telegram-bot-api
          if [ ! -x "$BIN" ]; then
            git clone --depth 1 https://github.com/tdlib/telegram-bot-api.git /home/runner/telegram-bot-api
            cmake -S /home/runner/telegram-bot-api -B /home/runner/telegram-bot-api/build \
                  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/home/runner/.hermes/tools/telegram-bot-api
            cmake --build /home/runner/telegram-bot-api/build --target install -j"$(nproc)"
          fi
          pkill -f telegram-bot-api || true
          nohup "$BIN" --api-id="$TELEGRAM_API_ID" --api-hash="$TELEGRAM_API_HASH" --local \
            --http-address=127.0.0.1 --http-port=8081 \
            --dir="$BOTROOT/tgbotapi" --temp-dir="$BOTROOT/tmp" \
            --log="$BOTROOT/logs/botapi.log" >>"$BOTROOT/logs/botapi.stdout" 2>&1 &
          echo $! > "$BOTROOT/botapi.pid"
          for i in $(seq 1 30); do
            curl -fsS --max-time 3 -o /dev/null \
              "http://127.0.0.1:8081/bot${TELEGRAM_BOT_TOKEN}/getMe" && break || sleep 2
          done
          curl -fsS --max-time 5 "http://127.0.0.1:8081/bot${TELEGRAM_BOT_TOKEN}/getMe" \
            | sed -E 's/("token"|"id":[0-9]+)/[redacted]/g' || echo "bot API server not answering yet"

      # ---- 2) start the bot (insert BEFORE "Keep alive") ----
      - name: Start Telegram download bot
        if: always()
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_BOT_API_ROOT: http://127.0.0.1:8081
          DLBOT_ADMIN_ID: "180329538"
        run: |
          set -euo pipefail
          BOTROOT=/home/runner/dlbot
          pkill -f "$BOTROOT/bot.py" || true      # R9: never leave a second poller (Telegram 409)
          sleep 1
          cd "$BOTROOT"
          nohup python3 -u "$BOTROOT/bot.py" >>"$BOTROOT/logs/bot.log" 2>&1 &
          echo $! > "$BOTROOT/bot.pid"
          sleep 5
          kill -0 "$(cat "$BOTROOT/bot.pid")" && echo "bot alive pid $(cat "$BOTROOT/bot.pid")"

      # ---- 3) stop both (insert immediately BEFORE "Back up state to GitHub", if: always()) ----
      - name: Stop Telegram download bot and Bot API server
        if: always()
        run: |
          set +e
          BOTROOT=/home/runner/dlbot
          [ -f "$BOTROOT/bot.pid" ]     && kill -TERM "$(cat "$BOTROOT/bot.pid")"
          [ -f "$BOTROOT/botapi.pid" ]  && kill -TERM "$(cat "$BOTROOT/botapi.pid")"
          sleep 3
          pkill -f "$BOTROOT/bot.py" || true
          pkill -f 'telegram-bot-api' || true
          rm -f "$BOTROOT/bot.pid" "$BOTROOT/botapi.pid"
          echo "bot + bot API server stopped"
```
Notes: no secret value is written to the log (only lengths/status are surfaced); the bot never needs
inbound connectivity (long polling), so no tailscale/Cloudflare change is required.

---

## 4. Proposed module layout

Root: `/home/runner/dlbot/` (files are backed up by the running Hermes state restore only if placed
there explicitly — the code must live in the repo/workflow, not in `work/`).

```
/home/runner/dlbot/
  bot.py            # entrypoint: config -> registry -> sweeper -> poller -> handlers
  config.py         # Config + env loading + lifetime math + fa_digits
  strings.py        # §1 string table + render() + keyboard builders
  tg.py             # TelegramClient (local Bot API over HTTP, retries, local-path uploads)
  poller.py         # getUpdates long-poll loop, offset persistence, single-instance guard
  classify.py       # URL classifier + SSRF validation
  downloader.py     # yt-dlp ladder, generic curl path, ffmpeg mp3, error mapping
  registry.py       # TTL file registry (SQLite) + sweeper + startup cleanup
  jobs.py           # JobQueue: per-chat state, concurrency, per-user rate limit, cancel
  botapictl.py      # local telegram-bot-api health check / (re)start
  files/  tmp/  logs/  tgbotapi/
```

### 4.1 `config.py`

```python
@dataclass(frozen=True)
class Config:
    bot_token: str
    api_root: str = "http://127.0.0.1:8081"
    admin_id: int = 180329538
    root: Path = Path("/home/runner/dlbot")
    ytdlp: Path = Path("/home/runner/.local/bin/yt-dlp")
    ffmpeg_dir: Path = Path("/home/runner/.hermes/tools/ffmpeg-9.0.1-linux-x64/bin")
    cookies: Path = Path("/home/runner/.hermes/secrets/youtube_cookies.txt")
    pot_url: str = "http://127.0.0.1:4416"
    max_file_mb: int = 500
    max_height: int = 1080
    max_duration_s: int = 7200
    ttl_s: int = 3600
    max_concurrent: int = 2
    per_user_per_hour: int = 10
    min_remaining_s: int = 600          # D4: refuse new jobs below this
    ttl_clamp_margin_s: int = 300       # D4: TTL <= remaining - 5 min
    job_timeout_minutes: int = 335
    job_start_epoch: int = 0
    job_timeout_s: int = 900            # per-download timeout

def load_config(env: Mapping[str, str] = os.environ) -> Config: ...
    # raises MissingConfig if TELEGRAM_BOT_TOKEN is empty; JOB_START_EPOCH from the
    # "Record job start" step; NEVER logs the token.
def remaining_seconds(cfg: Config, now: float | None = None) -> int: ...
    # max(0, cfg.job_timeout_minutes*60 - (now - cfg.job_start_epoch))
def shutdown_utc(cfg: Config) -> datetime: ...          # job_start_epoch + job_timeout_minutes*60
def shutdown_tehran(cfg: Config) -> datetime: ...       # same instant, tz +03:30
def effective_ttl(cfg: Config, now: float | None = None) -> int: ...
    # min(cfg.ttl_s, remaining_seconds(now) - cfg.ttl_clamp_margin_s), floored at 60
def can_start_job(cfg: Config, now: float | None = None) -> bool: ...   # remaining >= min_remaining_s
def fa_digits(text: str) -> str: ...                    # 0-9 -> ۰-۹
def human_size(n_bytes: int | None) -> str: ...         # "12.4 MB" / "1.2 GB" / "?"
def human_duration(seconds: int | None) -> str: ...     # "01:23:45"
def human_speed(bps: float) -> str: ...
def progress_bar(pct: float, width: int = 12) -> str: ...
```

### 4.2 `strings.py`

```python
MSG: dict[str, str] = { ... }                 # every key from §1.1 and §1.3, ASCII placeholders
def render(key: str, /, *, fa: bool = True, **kw: object) -> str: ...   # format + fa_digits
def lifetime_line(cfg: Config, now: float | None = None, *, short: bool = False) -> str: ...
    # render("lifetime_short"/"lifetime_full", mm=remaining//60, ...)
def kb_format(*, token: str) -> list[list[dict[str, str]]]: ...
def kb_audio(*, token: str) -> list[list[dict[str, str]]]: ...
def kb_compress(*, token: str) -> list[list[dict[str, str]]]: ...
def kb_resend(*, token: str) -> list[list[dict[str, str]]]: ...
def cross_check() -> None: ...                # asserts every key referenced in code exists in MSG
```

### 4.3 `poller.py`

```python
@dataclass
class ChatState:                     # per-chat, in memory only (D2)
    token: str | None = None                       # uuid4 hex of the pending job
    stage: Literal["idle","await_format","await_audio","compressing"] = "idle"
    url: str | None = None
    info: "MediaInfo | None" = None
    job_id: str | None = None

class Poller:
    def __init__(self, cfg: Config, tg: "TelegramClient",
                 on_update: Callable[[dict[str, object]], None]) -> None: ...
    def run_forever(self, stop: threading.Event) -> None: ...
        # getUpdates loop: offset = last_update_id+1, timeout=25, limit=100,
        # allowed_updates=["message","callback_query"], drop_pending_updates on FIRST poll
        # only, backoff 1->30 s on network errors, stop.is_set() -> send final /kill notice
    def _get_updates(self, offset: int, timeout: int = 25) -> list[dict[str, object]]: ...
    def _dispatch(self, upd: dict[str, object]) -> None: ...   # -> on_update, wrapped in try/except
    @staticmethod
    def kill_previous_pollers() -> int: ...
        # R9: pkill -f "/home/runner/dlbot/bot.py" excluding os.getpid(); avoids Telegram 409
    def startup_sweep(self) -> None: ...     # registry.startup_cleanup() then delete await states
```

### 4.4 `classify.py`

```python
class UrlKind(StrEnum):
    YOUTUBE = "youtube"
    DIRECT = "direct"
    UNSUPPORTED_SITE = "unsupported_site"
    BLOCKED = "blocked"
    NOT_URL = "not_url"

@dataclass(frozen=True)
class UrlInfo:
    kind: UrlKind
    url: str = ""
    host: str = ""
    video_id: str | None = None
    reason: str | None = None         # -> strings key, e.g. "err_bad_scheme"

YT_HOSTS = {"youtube.com","www.youtube.com","m.youtube.com","music.youtube.com",
            "youtu.be","www.youtu.be","youtube-nocookie.com","www.youtube-nocookie.com"}
BLOCKED_SITES = {"instagram.com","tiktok.com","twitter.com","x.com","facebook.com",
                 "fb.watch","vimeo.com","spotify.com"}   # D7: no cookies -> Persian refusal

def extract_youtube_id(text: str) -> str | None: ...
    # watch?v=, youtu.be/<id>, /shorts/<id>, /embed/<id>, /live/<id>; id regex ^[A-Za-z0-9_-]{11}$
def classify(text: str) -> UrlInfo: ...
    # single entry point; strips whitespace/zero-width, handles "url + extra words"
def is_private_host(host: str) -> bool: ...                # literal IP check
def resolve_is_safe(host: str, *, resolver=socket.getaddrinfo) -> tuple[bool, str]: ...
    # every A/AAAA must fall outside loopback/private/link-local/CGNAT/metadata
def validate_direct_url(url: str, *, resolver=socket.getaddrinfo) -> UrlInfo: ...
    # scheme http/https only, hostname required, re-validated on every redirect hop by the caller
def unsupported_site_message(info: UrlInfo) -> str: ...
```

### 4.5 `downloader.py`

```python
@dataclass
class MediaInfo:
    video_id: str; title: str; uploader: str = ""
    duration_s: int | None = None; filesize_hint: int | None = None
    height: int | None = None; vcodec: str | None = None; acodec: str | None = None
    ext: str = "mp4"; thumb_url: str | None = None

@dataclass(frozen=True)
class Choice:
    kind: Literal["video","audio"]
    height: int | None = None            # 480/720/1080
    audio_format: Literal["m4a","mp3"] = "m4a"
    mp3_kbps: int | None = None          # 64/128/192/320

@dataclass
class DownloadResult:
    path: Path; info: MediaInfo; choice: Choice
    sha256: str; size: int; audio_bitrate_kbps: int | None = None
    degraded_from_video: bool = False    # ladder fell back to audio-only

class YtDlpError(RuntimeError):
    def __init__(self, key: str, *, detail: str = "", code: str = "") -> None: ...

def ytdlp_base(cfg: Config, tmp_dir: Path) -> list[str]: ...
    # §2.1 prefix (cookie recipe included, absolutely no shortcut)
def add_common_flags(cfg: Config) -> list[str]: ...
def probe(url: str, cfg: Config, *, timeout_s: int = 120) -> MediaInfo: ...
def ensure_pot_provider(cfg: Config) -> bool: ...      # ping :4416, start node server if dark
def ensure_cookies(cfg: Config) -> None: ...           # -> err_cookies_missing
def build_video_ladder(cfg: Config, choice: Choice, info: MediaInfo) -> list[list[str]]: ...
    # [progressive<=cap, bv*+ba, audio-only fallback, alternate-client retry]  (§2.3)
def build_audio_argv(cfg: Config, choice: Choice, tmp_dir: Path, url: str) -> list[str]: ...
def download_youtube(url: str, choice: Choice, cfg: Config, *,
                     progress_cb: Callable[[float | None, str], None] | None = None,
                     cancel: threading.Event | None = None) -> DownloadResult: ...
    # runs each ladder rung under `timeout job_timeout_s`, parses --newline progress (%/size/speed/eta),
    # on failure calls classify_error(stderr); converts to mp3 when choice.mp3_kbps set via to_mp3()
def download_direct(url: str, cfg: Config, *,
                    progress_cb: Callable[[float | None, str], None] | None = None) -> DownloadResult: ...
    # §2.6 curl; random token filename; extension sanitised; sha256 computed; never re-encoded
def to_mp3(src: Path, dst: Path, kbps: int, cfg: Config, *, timeout_s: int = 1800) -> Path: ...
    # the exact §2.5 ffmpeg command for 64/128/192/320
def ffprobe_stream(path: Path, cfg: Config) -> dict[str, str]: ...
def probe_audio_bitrate_kbps(path: Path, cfg: Config) -> int | None: ...
def classify_error(stderr: str) -> str: ...            # §2.7 table -> strings key
def cleanup(tmp_path: Path) -> None: ...
def sha256_file(path: Path, *, chunk: int = 1 << 20) -> str: ...
```

### 4.6 `registry.py`

```python
@dataclass
class FileRecord:
    token: str; chat_id: int; message_id: int | None; path: Path
    size: int; sha256: str; created_at: float; expires_at: float
    kind: Literal["video","audio","document"]; title: str
    quality: str = ""; duration_s: int | None = None; thumb: Path | None = None

class Registry:
    def __init__(self, db_path: Path, ttl_s: int, *, ttl_provider: Callable[[], int] | None = None) -> None: ...
        # ttl_provider = config.effective_ttl -> TTL clamp (D4) applied at insert time
    def init_schema(self) -> None: ...
        # table files(token PK, chat_id, message_id, path, size, sha256, created_at, expires_at,
        #            kind, title, quality, duration_s); WAL mode; index on expires_at
    def add(self, rec: FileRecord) -> None: ...
    def get(self, token: str) -> FileRecord | None: ...
    def is_alive(self, token: str) -> bool: ...        # row exists AND file exists AND now < expires_at
    def mark_sent(self, token: str, chat_id: int, message_id: int) -> None: ...
    def drop(self, token: str) -> None: ...            # row + file (after successful send we KEEP the file until TTL)
    def sweep(self) -> int: ...                        # returns count removed: expired rows or vanished files
    def startup_cleanup(self) -> int: ...
        # D2/R2: delete every file in files/ at boot (a restart means all old tokens are dead)
        # and delete all rows; the bot then honestly answers «expired» for every old button.
    def stats(self) -> dict[str, int]: ...             # {"files": n, "used_bytes": ...}
    def reserve_token(self) -> str: ...                # secrets.token_hex(16)

def run_sweeper(reg: Registry, stop: threading.Event, *, interval_s: int = 60) -> None: ...
    # daemon thread; also re-checks disk free space -> err_disk_full guard
def rss_alert(cfg: Config, tg: "TelegramClient", title: str, detail: str, code: str = "") -> None: ...
    # admin_alert template to cfg.admin_id only, never to the user
```

### 4.7 `tg.py` (sender)

```python
class TelegramClient:
    def __init__(self, token: str, *, api_root: str = "http://127.0.0.1:8081",
                 local_mode: bool = True, timeout: float = 120.0,
                 max_retries: int = 4) -> None: ...
    # --- transport ---
    def call(self, method: str, *, data: dict[str, object] | None = None,
             files: dict[str, tuple[str, BinaryIO]] | None = None) -> dict[str, object]: ...
        # POST {api_root}/bot{token}/{method}; JSON when files is None, multipart otherwise;
        # on 429: honour parameters.retry_after then retry (max_retries, exponential cap 30 s);
        # on 5xx/network: exponential backoff; never logs the token (URL is redacted before logging)
    def _url(self, method: str) -> str: ...
    def get_me(self) -> dict[str, object]: ...
    def health(self) -> bool: ...                       # getMe -> False == err_botapi_down
    # --- messages ---
    def send_message(self, chat_id: int, text: str, *, keyboard: list | None = None,
                     parse_mode: str | None = "HTML", reply_to: int | None = None,
                     disable_notification: bool = False) -> int: ...
    def edit_message_text(self, chat_id: int, message_id: int, text: str, *,
                          keyboard: list | None = None, parse_mode: str | None = "HTML") -> None: ...
        # ignores "message is not modified"
    def delete_message(self, chat_id: int, message_id: int) -> None: ...
    def send_chat_action(self, chat_id: int, action: str = "upload_document") -> None: ...
    def answer_callback_query(self, callback_id: str, *, text: str | None = None,
                             show_alert: bool = False) -> None: ...
    # --- file delivery (§3.1 local-path path) ---
    def send_document(self, chat_id: int, path: Path, *, caption: str | None = None,
                      keyboard: list | None = None, reply_to: int | None = None,
                      progress_cb: Callable[[float], None] | None = None,
                      filename: str | None = None) -> int: ...
        # local_mode=True  -> data["document"] = str(path.resolve())   # zero-copy, <=2000 MB
        # local_mode=False -> files={"document": (filename or path.name, open(path,"rb"))}  # 50 MB cap
    def send_video(self, chat_id: int, path: Path, *, caption: str | None = None,
                   duration: int | None = None, width: int | None = None, height: int | None = None,
                   thumb: Path | None = None, supports_streaming: bool = True,
                   keyboard: list | None = None, reply_to: int | None = None,
                   progress_cb: Callable[[float], None] | None = None) -> int: ...
        # same local-path rule; falls back to send_document when Telegram rejects the video metadata
        # (err_upload -> document retry, then give up with err_upload)
    def send_audio(self, chat_id: int, path: Path, *, caption: str | None = None,
                   title: str | None = None, performer: str | None = None,
                   duration: int | None = None, keyboard: list | None = None) -> int: ...
    def deliver(self, chat_id: int, res: "DownloadResult", caption: str,
                *, keyboard: list | None = None,
                progress_cb: Callable[[float], None] | None = None) -> int: ...
        # the only place M4 lives: picks sendAudio/sendVideo/sendDocument by res.choice,
        # caps at 2000 MB (local) / 50 MB (public), converts upload failures to err_upload
    # --- incoming files (for re-send / admin tools) ---
    def get_file(self, file_id: str) -> dict[str, object]: ...
    def download_file(self, file_path: str, dst: Path) -> Path: ...
```

### 4.8 `jobs.py` + `bot.py` wiring

```python
@dataclass
class Job:
    job_id: str; chat_id: int; url: str; choice: Choice
    created_at: float; cancel: threading.Event; stage: str = "queued"
    result: DownloadResult | None = None; error_key: str | None = None

class JobQueue:
    def __init__(self, cfg: Config, tg: TelegramClient, reg: Registry, states: dict[int, ChatState]) -> None: ...
    def submit(self, chat_id: int, url: str, choice: Choice) -> Job: ...
        # enforce: min_remaining_s, per-user/hour, one job per user, max_concurrent (+queue<=3),
        # disk free; raises RateLimited(retry_in)/QueueFull/NoTimeBudget/UserBusy
    def _run(self, job: Job) -> None: ...
        # thread per job (max_concurrent), progress edits throttled to 1/2 s, upserts registry on success
    def cancel(self, chat_id: int) -> bool: ...
    def active_count(self) -> int: ...
    def user_downloads_last_hour(self, chat_id: int) -> int: ...
    def shutdown(self, grace_s: int = 20) -> None: ...   # SIGTERM handler: cancel jobs, cleanup tmp

# handlers (pure functions over an Update, easy to unit-test)
def on_message(cfg, tg, reg, states, queue, msg: dict) -> None: ...
def on_callback(cfg, tg, reg, states, queue, cb: dict) -> None: ...
    # fmt:video|audio|cancel|back, aud:m4a|mp3:<kbps>|back, vid:480|720|best, <32-hex-token>
def on_command(cfg, tg, reg, states, queue, chat_id: int, text: str) -> bool: ...
    # /start /help /status /cancel /kill(admin only)
def main() -> int: ...
    # load_config -> Registry.init_schema + startup_cleanup -> telegram client health ->
    # Poller.kill_previous_pollers -> sweeper thread -> poller.run_forever -> finally queue.shutdown
```

---

## 5. Open items that block real implementation (do NOT guess)

1. `api_hash` for api_id 147006 — not present on this node; needed as secret `TELEGRAM_API_HASH` for
   §3.3. Everything in §3 is otherwise ready.
2. Q1 decision (local Bot API server vs Telethon) — §3 assumes the local server.
3. Q2: nobody has ever pressed `/start` on `@heydar_downloader_bot` (`getChat(180329538)` → "chat not
   found" [E]) — a live test needs a human.
4. Which docker tag of telegram-bot-api actually pulls (fallback = source build in §3.2). [V]

## 6. Verification status (honest)

Ran in this prep run (evidence): `date -u`; `ls` of `/home/runner/.hermes/secrets` and
`/home/runner/.hermes/tools`; `yt-dlp --version` → 2026.10.26; `ffmpeg -version` → n9.0.1;
`node --version` → v24.22.2; `docker images`; repo/permission check for `heydarsaki-dev/Personal`
with `$HERMES_BACKUP_TOKEN` (404 on branch `dlbot-spec` → it does not exist yet; `main` is default).

**Not run / unverified — must be proven on the next node:** the local Bot API server (build, run,
`--local` local-path upload), every yt-dlp rung of the ladder with these exact flags, the ffmpeg mp3
commands, the generic-download path, any actual send into a Telegram chat, the workflow steps in §3.4
(they were never added to `server.yml` — forbidden in this run), and the whole Persian UI rendering.

Constraints respected in this run: no edit to `.github/workflows/server.yml` or any existing workflow;
no commit to `main`; nothing touched in `/home/runner/serve` or its nginx.conf; no server started;
no large download; no big install.
