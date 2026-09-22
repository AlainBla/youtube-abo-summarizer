"""Which proxy to use, and whether the SOCKS one is actually there.

Three modules reach YouTube over the network -- ``transcripts.py``,
``feeds.py`` and ``ytdlp_meta.py`` -- and each of them used to read
``WEBSHARE_PROXY_URL`` on its own. A residential proxy is metered and slow, so
a local SOCKS tunnel (Tor, an SSH ``-D`` forward, a VPN's own listener) is the
better exit whenever one is running: it costs nothing per request and it is on
the same machine.

"Whenever one is running" is the whole problem. A tunnel that is configured in
``.env`` but not currently listening must not be tried, or every fetch pays a
connection refusal before falling back -- and in ``transcripts.py``, where the
proxy is the *primary* path rather than a retry, a dead tunnel would fail the
run outright. So ``socks_proxy_url()`` does not just read the variable, it
probes the port: a TCP connect plus, for SOCKS5, the greeting handshake, so an
unrelated service that happens to hold the port is not mistaken for a proxy.
The verdict is cached per process and logged once.

The order every caller then follows is the chain from ``proxy_chain()``: the
SOCKS proxy first, the Webshare proxy behind it. ``--no-proxy`` empties the
chain, both entries at once.
"""

import os
import socket
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

SOCKS_ENV = "SOCKS_PROXY_URL"
WEBSHARE_ENV = "WEBSHARE_PROXY_URL"

# The probe runs before the first real request of a run, so it has to be short
# enough not to be felt and long enough to survive a loaded machine. A local
# listener answers in microseconds; this budget is for the pathological case.
PROBE_TIMEOUT = float(os.getenv("SOCKS_PROBE_TIMEOUT", "2"))

# socks5h keeps DNS on the proxy side. Resolving youtube.com locally and then
# asking the tunnel for that address defeats half the point of the tunnel, so
# a bare "host:port" is read as socks5h rather than socks5.
_DEFAULT_SCHEME = "socks5h"
_DEFAULT_PORT = 1080

_SOCKS5_SCHEMES = ("socks5", "socks5h")
_SOCKS4_SCHEMES = ("socks4", "socks4a")

# Keyed by the raw environment value, so the whole verdict -- parsed, PySocks
# present, port answering -- is reached once per run and not per video. A test
# (or a run that rewrites the environment) gets a fresh decision for a
# different value instead of inheriting one that was about something else.
_verdict_cache: dict[str, str | None] = {}


def reset_probe_cache() -> None:
    """Forget every probe verdict. For tests and for a long-lived process."""
    _verdict_cache.clear()


def normalize_socks_url(raw: str) -> str | None:
    """Return the configured SOCKS URL in canonical form, or None if unusable.

    Accepts "127.0.0.1:9050" as well as a full URL: a host and a port are the
    only parts that carry meaning, and demanding a scheme for a value that has
    exactly one plausible one only produces silent misconfiguration.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"{_DEFAULT_SCHEME}://{raw}"

    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    if scheme not in _SOCKS5_SCHEMES + _SOCKS4_SCHEMES:
        print(
            f"[proxy] {SOCKS_ENV}: '{scheme}' ist kein SOCKS-Schema — ignoriert.",
            file=sys.stderr,
        )
        return None
    try:
        host, port = parsed.hostname, parsed.port
    except ValueError:
        host, port = None, None
    if not host:
        print(f"[proxy] {SOCKS_ENV}: keine Adresse erkennbar — ignoriert.", file=sys.stderr)
        return None

    auth = ""
    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth += f":{parsed.password}"
        auth += "@"
    return f"{scheme}://{auth}{host}:{port or _DEFAULT_PORT}"


def _greeting(url: str) -> bytes:
    """The SOCKS5 method-selection message to send on the probe."""
    parsed = urlparse(url)
    if parsed.username:
        # Offer "no authentication" and "username/password"; a proxy that
        # wants credentials answers 0x02 rather than 0xFF.
        return b"\x05\x02\x00\x02"
    return b"\x05\x01\x00"


def _probe(url: str) -> bool:
    """True when something at `url` behaves like a SOCKS proxy.

    A plain TCP connect is not enough: any listener on the port would pass it,
    and a proxy that is actually some other service fails every later request
    in a way that reads as YouTube blocking the machine. For SOCKS5 the
    greeting is exchanged, which no unrelated protocol answers by accident.
    SOCKS4 has no such handshake before a connect request, so there the open
    port is all the evidence available.
    """
    parsed = urlparse(url)
    host, port = parsed.hostname, parsed.port or _DEFAULT_PORT
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT) as sock:
            if parsed.scheme.lower() in _SOCKS4_SCHEMES:
                return True
            sock.settimeout(PROBE_TIMEOUT)
            sock.sendall(_greeting(url))
            reply = sock.recv(2)
    except OSError:
        return False
    # 0xFF is "no acceptable method" — the proxy is there but will not talk to us.
    return len(reply) == 2 and reply[0] == 0x05 and reply[1] != 0xFF


def _pysocks_available() -> bool:
    """Whether requests can actually open a SOCKS connection.

    requests delegates SOCKS to PySocks and, without it, raises InvalidSchema
    on the first request -- a ValueError subclass that neither
    youtube-transcript-api nor transcripts._fetch_original catches, so it
    would leave the collect run as a traceback rather than as a failed video.
    A proxy two of the three consumers cannot speak to is not usable, so the
    missing package is part of the verdict rather than a surprise later.
    """
    try:
        import socks  # noqa: F401  (PySocks)
    except ImportError:
        return False
    return True


def _decide(raw: str) -> str | None:
    """The full verdict for one raw SOCKS_PROXY_URL value, printed once."""
    url = normalize_socks_url(raw)
    if url is None:
        return None
    if not _pysocks_available():
        print(
            f"[proxy] SOCKS-Proxy {redact(url)} konfiguriert, aber PySocks fehlt — "
            "übersprungen (pip install -r requirements.txt).",
            file=sys.stderr,
            flush=True,
        )
        return None
    if not _probe(url):
        print(
            f"[proxy] SOCKS-Proxy {redact(url)} antwortet nicht — übersprungen.",
            file=sys.stderr,
            flush=True,
        )
        return None
    print(f"[proxy] SOCKS-Proxy erreichbar: {redact(url)}", flush=True)
    return url


def socks_proxy_url(no_proxy: bool = False) -> str | None:
    """The SOCKS proxy URL if one is configured, usable *and* answering, else None."""
    if no_proxy:
        return None
    raw = os.getenv(SOCKS_ENV, "").strip()
    if not raw:
        return None
    if raw not in _verdict_cache:
        _verdict_cache[raw] = _decide(raw)
    return _verdict_cache[raw]


def webshare_proxy_url(no_proxy: bool = False) -> str | None:
    """The configured Webshare/residential proxy URL, if any. Never probed.

    It is a remote service on a metered plan: a probe would cost a request per
    run to learn something the first real fetch learns anyway.
    """
    if no_proxy:
        return None
    return os.getenv(WEBSHARE_ENV) or None


def proxy_chain(no_proxy: bool = False) -> list[str]:
    """Every usable proxy, best first: SOCKS before Webshare.

    Empty when nothing is configured, when the SOCKS proxy is unreachable and
    no Webshare proxy is set, or whenever `no_proxy` is given.
    """
    if no_proxy:
        return []
    return [url for url in (socks_proxy_url(), webshare_proxy_url()) if url]


def redact(url: str) -> str:
    """The URL without its password, for printing."""
    parsed = urlparse(url)
    if not parsed.hostname:
        return url
    auth = f"{parsed.username}@" if parsed.username else ""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{auth}{parsed.hostname}{port}"


def describe(no_proxy: bool = False) -> list[str]:
    """One human-readable line per configured proxy, for the run's log."""
    lines = []
    raw_socks = normalize_socks_url(os.getenv(SOCKS_ENV, ""))
    if raw_socks:
        if no_proxy:
            state = "IGNORIERT (--no-proxy)"
        else:
            state = "aktiv" if socks_proxy_url() else "nicht erreichbar"
        lines.append(f"SOCKS: {redact(raw_socks)} ({state})")
    webshare = os.getenv(WEBSHARE_ENV)
    if webshare:
        suffix = " (IGNORIERT)" if no_proxy else ""
        lines.append(f"Webshare: {redact(webshare)}{suffix}")
    return lines
