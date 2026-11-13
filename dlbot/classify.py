"""URL classification + SSRF validation (spec section 4.4)."""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable
from urllib.parse import unquote, urlsplit


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
    reason: str | None = None


YT_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com",
}
BLOCKED_SITES = {
    "instagram.com", "www.instagram.com", "instagr.am", "tiktok.com", "www.tiktok.com",
    "vm.tiktok.com", "twitter.com", "www.twitter.com", "x.com", "www.x.com",
    "facebook.com", "www.facebook.com", "fb.watch", "vimeo.com", "www.vimeo.com",
    "spotify.com", "open.spotify.com", "threads.net", "www.threads.net",
}
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
ANY_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s<>\"']+")
YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
PRIVATE_NETS = [
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
        "172.16.0.0/12", "192.0.0.0/24", "192.168.0.0/16", "198.18.0.0/15",
        "224.0.0.0/4", "240.0.0.0/4", "::1/128", "fc00::/7", "fe80::/10", "::/128",
    )
]


def _clean(text: str) -> str:
    return (text or "").replace("\u200c", "").replace("\u200f", "").replace("\u200e", "").strip()


def extract_youtube_id(text: str) -> str | None:
    """watch?v=, youtu.be/<id>, /shorts/<id>, /embed/<id>, /live/<id>."""
    raw = _clean(text)
    match = URL_RE.search(raw)
    candidate = match.group(0) if match else raw
    if not candidate.lower().startswith(("http://", "https://")):
        candidate = "https://" + candidate
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if host not in YT_HOSTS:
        return None
    path = unquote(parts.path or "")
    if host.endswith("youtu.be") and host != "www.youtube.com":
        vid = path.strip("/").split("/")[0]
        return vid if YT_ID_RE.match(vid) else None
    if path == "/watch":
        for pair in (parts.query or "").split("&"):
            if pair.startswith("v="):
                vid = pair[2:13]
                if YT_ID_RE.match(vid):
                    return vid
        return None
    for prefix in ("/shorts/", "/embed/", "/live/", "/v/"):
        if path.startswith(prefix):
            vid = path[len(prefix):].split("/")[0]
            if YT_ID_RE.match(vid):
                return vid
    return None


def is_private_host(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr in net for net in PRIVATE_NETS)


def resolve_is_safe(host: str, *, resolver: Callable[..., list] = socket.getaddrinfo) -> tuple[bool, str]:
    """Every A/AAAA record must fall outside loopback/private/link-local/CGNAT/metadata."""
    try:
        infos = resolver(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        return False, f"dns: {exc}"
    if not infos:
        return False, "dns: no addresses"
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr.split("%")[0])
        except ValueError:
            return False, f"unparsable address {addr}"
        if any(ip in net for net in PRIVATE_NETS):
            return False, f"blocked address {addr}"
    return True, ""


def validate_direct_url(url: str, *, resolver: Callable[..., list] = socket.getaddrinfo) -> UrlInfo:
    try:
        parts = urlsplit(url)
    except ValueError:
        return UrlInfo(UrlKind.NOT_URL, url=url, reason="err_not_url")
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        return UrlInfo(UrlKind.BLOCKED, url=url, reason="err_bad_scheme")
    host = (parts.hostname or "").lower()
    if not host:
        return UrlInfo(UrlKind.NOT_URL, url=url, reason="err_not_url")
    if is_private_host(host):
        return UrlInfo(UrlKind.BLOCKED, url=url, host=host, reason="err_blocked_host")
    safe, why = resolve_is_safe(host, resolver=resolver)
    if not safe:
        if why.startswith("dns:"):
            return UrlInfo(UrlKind.DIRECT, url=url, host=host, reason="err_dl_failed")
        return UrlInfo(UrlKind.BLOCKED, url=url, host=host, reason="err_blocked_host")
    return UrlInfo(UrlKind.DIRECT, url=url, host=host)


def classify(text: str, *, resolver: Callable[..., list] = socket.getaddrinfo) -> UrlInfo:
    """Single entry point for every user-supplied text."""
    raw = _clean(text)
    if not raw:
        return UrlInfo(UrlKind.NOT_URL, reason="err_not_url")
    match = URL_RE.search(raw)
    if not match:
        # bare "youtu.be/<id>" without a scheme is still a YouTube link
        vid = extract_youtube_id(raw)
        if vid:
            return UrlInfo(UrlKind.YOUTUBE, url="https://www.youtube.com/watch?v=" + vid,
                           host="youtu.be", video_id=vid)
        other = ANY_SCHEME_RE.search(raw)
        if other:
            return UrlInfo(UrlKind.BLOCKED, url=other.group(0), reason="err_bad_scheme")
        return UrlInfo(UrlKind.NOT_URL, reason="err_not_url")
    url = match.group(0).rstrip(").,;،»\"'")
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return UrlInfo(UrlKind.NOT_URL, url=url, reason="err_not_url")
    if host in YT_HOSTS:
        vid = extract_youtube_id(url)
        if vid:
            return UrlInfo(UrlKind.YOUTUBE, url=url, host=host, video_id=vid)
        return UrlInfo(UrlKind.UNSUPPORTED_SITE, url=url, host=host,
                       reason="err_unsupported_site")
    if host in BLOCKED_SITES or any(host.endswith("." + h) for h in BLOCKED_SITES):
        return UrlInfo(UrlKind.UNSUPPORTED_SITE, url=url, host=host, reason="err_unsupported_site")
    return validate_direct_url(url, resolver=resolver)
