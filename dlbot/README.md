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

Required repo secret: `TELEGRAM_API_HASH` (api_id 147006 is hard-coded, the hash
is not). Without it step 1 skips itself and the bot falls back to the public Bot
API (50 MB cap).

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
