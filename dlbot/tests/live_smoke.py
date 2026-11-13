#!/usr/bin/env python3
"""Live smoke test (bounded): real direct-link download, real sha256 compare,
real Telegram round-trips against the LOCAL Bot API server on 127.0.0.1:8081."""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DLBOT = Path("/home/runner/.hermes/company/work/personal/dlbot")
sys.path.insert(0, str(DLBOT))

from config import Config, load_config  # noqa: E402
from downloader import download_direct, sha256_file  # noqa: E402
from registry import FileRecord, Registry  # noqa: E402
from tg import TelegramClient, TelegramError  # noqa: E402

URL = "https://raw.githubusercontent.com/octocat/Hello-World/master/README"


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="dlbot-live-"))
    cfg = Config(bot_token=load_config().bot_token, root=root,
                 api_root="http://127.0.0.1:8081")
    for d in (cfg.root, cfg.files_dir, cfg.tmp_dir, cfg.logs_dir):
        d.mkdir(parents=True, exist_ok=True)
    print(f"== root {root} ==")

    print("\n== 1) real direct-link download through dlbot.downloader ==")
    t0 = time.time()
    result = download_direct(URL, cfg)
    print(f"downloaded {result.path} size={result.size} sha256={result.sha256} "
          f"in {time.time() - t0:.2f}s")

    print("\n== 2) independent verification with curl + sha256sum ==")
    ref = root / "ref.bin"
    subprocess.run(["curl", "-fsSL", "-o", str(ref), URL], check=True)
    ref_sum = subprocess.check_output(["sha256sum", str(ref)]).decode().split()[0]
    dl_sum = sha256_file(result.path)
    print(f"curl sha256sum : {ref_sum}")
    print(f"dlbot sha256  : {dl_sum}")
    print("MATCH" if ref_sum == dl_sum else "MISMATCH")

    print("\n== 3) registry insert + resend lookup ==")
    reg = Registry(cfg.db_path, 3600, ttl_provider=lambda: 120)
    rec = FileRecord(token="a" * 32, chat_id=180329538, path=result.path, size=result.size,
                     sha256=dl_sum, created_at=time.time(), expires_at=time.time() + reg.ttl(),
                     kind="document", title="README")
    reg.add(rec)
    print("is_alive:", reg.is_alive(rec.token), "stats:", reg.stats())
    rec.path.unlink()
    print("after deleting the file -> alive:", reg.is_alive(rec.token),
          "reason:", reg.expire_reason(rec.token))

    print("\n== 4) Telegram round-trip via LOCAL Bot API server (127.0.0.1:8081) ==")
    tg = TelegramClient(cfg)
    me = tg.get_me()
    print("getMe:", {k: me.get(k) for k in ("id", "username", "first_name", "is_bot")})
    for target in (180329538, 180329538):
        try:
            chat = tg.call("getChat", {"chat_id": target})["result"]
            print(f"getChat({target}):", {k: chat.get(k) for k in ("id", "type", "username")})
        except TelegramError as exc:
            print(f"getChat({target}) FAILED: code={exc.code} description={exc}")
        break
    payload = root / "probe.txt"
    payload.write_text("dlbot transport probe\n", encoding="utf-8")
    try:
        sent = tg.send_file(180329538, payload, kind="document", caption="probe")
        print("sendDocument OK:", sent.get("message_id"))
    except TelegramError as exc:
        print(f"sendDocument FAILED (expected until a human presses /start): "
              f"code={exc.code} description={exc}")

    print("\n== 5) cleanup ==")
    shutil.rmtree(root, ignore_errors=True)
    print("removed", root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
