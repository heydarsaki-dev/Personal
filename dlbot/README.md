# dlbot — Persian Telegram download bot (GitHub Actions host)

One bot (`@heydar_downloader_bot`) that downloads a YouTube video or any direct
`http(s)` file link and **sends the file itself into the originating chat**
(video / audio / document — never a bare link). The whole UI is Persian with
HELP/status strings rendered in HTML (bold only).

## How it runs

`.github/workflows/server.yml` (existing `Server` job) gained three steps:

1. **Start local Telegram Bot API server** — `aiogram/telegram-bot-api` on
   `127.0.0.1:8081` with `TELEGRAM_LOCAL=1` (docker fallback: build tdlib from
   source). Lifts the bot upload cap from 50 MB to 2000 MB and lets the bot hand
   the API an **absolute local path** instead of a multipart copy.
2. **Start Telegram download bot** — clones this repo into `/home/runner/dlbot/src`,
   copies `dlbot/*.py` to `/home/runner/dlbot`, then `python3 -u bot.py` (long
   polling; no inbound connectivity, tailscale/Cloudflare untouched).
3. **Stop Telegram download bot and Bot API server** — `if: always()`, placed
   immediately before `Back up state to GitHub`; also removes `files/`, `tmp/`.

Steps 1–3 are `continue-on-error: true`: they can never take down v2ray,
OmniRoute or the Hermes gateway.

Required repo secrets (already stored): `TELEGRAM_DL_BOT_TOKEN` (the downloader
bot — the job-level `TELEGRAM_BOT_TOKEN` is the Hermes gateway bot and must not
be used here), `TELEGRAM_API_HASH`, `TELEGRAM_API_ID`. Without the API hash step 1
skips itself and the bot falls back to the public Bot API (50 MB cap).

## Verified live (2026-11-13, this node)

| check | result |
|---|---|
| local Bot API server, `/getMe` on 127.0.0.1:8081 | ok → @heydar_downloader_bot |
| direct link → document in chat | delivered (msg 16, 6034 B), sha256 recorded |
| YouTube picker → video mp4 | delivered (msg 36, 629 172 B, api file_size match) |
| YouTube picker → mp3 128 kbps | delivered (msg 18, 305 153 B), ffprobe `128000 bps` |
| 256 MB mp4 through the local server | delivered (msg 38, 268 514 852 B) — >50 MB cap gone |
| failure matrix | Instagram / not-a-url / SSRF / bad scheme / over-cap / cookies-missing all Persian, bot alive |
| expiry button after deleting the file | «منقضی شده» reply |
| /help, /status (lifetime line), /cancel, /kill | ok; /kill refused for a non-admin, stops the bot for the admin |

Not verified live: the format picker's video-quality submenu (480/720), the
queue under real load, and a real runner restart mid-download. Drivers for the
live runs live in `dlbot/tests/`.

## Layout

| file | role |
|---|---|
| `bot.py` | entrypoint: registry → sweeper → poller → handlers |
| `config.py` | `Config`, lifetime math (`remaining = 335*60 − (now − JOB_START_EPOCH)`), Persian digits, size/duration/bar formatting |
| `strings.py` | full Persian string table + `render()` + inline keyboards |
| `classify.py` | URL classifier (YouTube / direct / unsupported site / blocked / not-a-url) + SSRF guards |
| `downloader.py` | yt-dlp format ladder, generic curl path, ffmpeg mp3, error mapping |
| `registry.py` | SQLite TTL registry + sweeper + restart cleanup |
| `jobs.py` | concurrency cap 2, queue, per-user 10/hour quota, cancel |
| `tg.py` | Telegram client over the local Bot API (local-path uploads, 429 retry) |
| `poller.py` | `getUpdates` long polling, single-instance guard (R9) |
| `selftest.py` | offline test suite — `python3 selftest.py` (exit 0 = all passed) |

## Facts baked in (from the v3 plan / bot-spec)

- yt-dlp recipe: `--cookies <secret> --js-runtimes node --remote-components ejs:github
  --ffmpeg-location <abs>` — without the two JS flags the same cookies hit
  “Sign in to confirm you’re not a bot”.
- ffmpeg/ffprobe live only at `~/.hermes/tools/ffmpeg-9.0.1-linux-x64/bin` (not in PATH).
- Everything is ephemeral: node dies at `JOB_START_EPOCH + 335 min`, files TTL 60 min
  (clamped to remaining − 5 min), new jobs refused with < 10 min left, all records
  wiped at startup (expired buttons answer «منقضی شده»).

## Test

```bash
python3 dlbot/selftest.py          # offline: strings, lifetime, classify, registry TTL, queue quotas
```
