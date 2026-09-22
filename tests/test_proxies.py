"""A SOCKS proxy is used before the Webshare one -- but only if it is really there.

The point of probing rather than trusting SOCKS_PROXY_URL: a tunnel that is
configured but not currently listening (Tor not started, the SSH -D session
gone) would otherwise be tried on every request. In transcripts.py, where the
proxy is the primary path rather than a retry, that would fail the whole run.
"""
import os
import socket
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, REPO)

import proxies


class _FakeSocket:
    """Enough of a socket for the SOCKS5 greeting."""

    def __init__(self, reply=b"\x05\x00"):
        self.reply = reply
        self.sent = b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def settimeout(self, _):
        pass

    def sendall(self, data):
        self.sent += data

    def recv(self, _n):
        return self.reply


def _connects(sock, record=None):
    def create_connection(address, timeout=None):
        if record is not None:
            record.append((address, timeout))
        if isinstance(sock, Exception):
            raise sock
        return sock
    return create_connection


class TestNormalisation:
    def test_a_bare_host_and_port_is_read_as_socks5h(self):
        assert proxies.normalize_socks_url("127.0.0.1:9050") == "socks5h://127.0.0.1:9050"

    def test_a_missing_port_falls_back_to_1080(self):
        assert proxies.normalize_socks_url("socks5://localhost") == "socks5://localhost:1080"

    def test_credentials_survive(self):
        assert proxies.normalize_socks_url("socks5://u:p@h:9") == "socks5://u:p@h:9"

    def test_an_http_url_is_not_a_socks_proxy(self):
        assert proxies.normalize_socks_url("http://proxy.example:80") is None

    def test_an_empty_value_is_no_proxy(self):
        assert proxies.normalize_socks_url("") is None
        assert proxies.normalize_socks_url("   ") is None


class TestProbe:
    def test_a_socks5_handshake_makes_the_proxy_usable(self, monkeypatch):
        monkeypatch.setenv("SOCKS_PROXY_URL", "socks5h://127.0.0.1:9050")
        monkeypatch.setattr(socket, "create_connection", _connects(_FakeSocket()))
        assert proxies.socks_proxy_url() == "socks5h://127.0.0.1:9050"

    def test_a_refused_port_means_no_socks_proxy(self, monkeypatch):
        monkeypatch.setenv("SOCKS_PROXY_URL", "socks5h://127.0.0.1:9050")
        monkeypatch.setattr(socket, "create_connection", _connects(ConnectionRefusedError()))
        assert proxies.socks_proxy_url() is None

    def test_a_timeout_means_no_socks_proxy(self, monkeypatch):
        monkeypatch.setenv("SOCKS_PROXY_URL", "socks5h://127.0.0.1:9050")
        monkeypatch.setattr(socket, "create_connection", _connects(socket.timeout()))
        assert proxies.socks_proxy_url() is None

    def test_a_listener_that_is_not_a_proxy_is_rejected(self, monkeypatch):
        # An HTTP server on the port answers something -- but not 0x05.
        monkeypatch.setenv("SOCKS_PROXY_URL", "socks5h://127.0.0.1:9050")
        monkeypatch.setattr(socket, "create_connection", _connects(_FakeSocket(b"HT")))
        assert proxies.socks_proxy_url() is None

    def test_a_proxy_refusing_every_auth_method_is_rejected(self, monkeypatch):
        monkeypatch.setenv("SOCKS_PROXY_URL", "socks5h://127.0.0.1:9050")
        monkeypatch.setattr(socket, "create_connection", _connects(_FakeSocket(b"\x05\xff")))
        assert proxies.socks_proxy_url() is None

    def test_credentials_are_offered_as_a_method(self, monkeypatch):
        sock = _FakeSocket(b"\x05\x02")
        monkeypatch.setenv("SOCKS_PROXY_URL", "socks5h://user:pw@127.0.0.1:9050")
        monkeypatch.setattr(socket, "create_connection", _connects(sock))
        assert proxies.socks_proxy_url() is not None
        assert sock.sent == b"\x05\x02\x00\x02"

    def test_socks4_has_no_handshake_so_an_open_port_is_enough(self, monkeypatch):
        sock = _FakeSocket(b"")
        monkeypatch.setenv("SOCKS_PROXY_URL", "socks4://127.0.0.1:1080")
        monkeypatch.setattr(socket, "create_connection", _connects(sock))
        assert proxies.socks_proxy_url() == "socks4://127.0.0.1:1080"
        assert sock.sent == b""

    def test_the_verdict_is_probed_once_per_process(self, monkeypatch):
        calls = []
        monkeypatch.setenv("SOCKS_PROXY_URL", "socks5h://127.0.0.1:9050")
        monkeypatch.setattr(socket, "create_connection", _connects(_FakeSocket(), record=calls))
        for _ in range(3):
            proxies.socks_proxy_url()
        assert len(calls) == 1

    def test_nothing_is_probed_when_no_socks_proxy_is_configured(self, monkeypatch):
        calls = []
        monkeypatch.setattr(socket, "create_connection", _connects(_FakeSocket(), record=calls))
        assert proxies.socks_proxy_url() is None
        assert calls == []

    def test_a_missing_pysocks_makes_the_proxy_unusable(self, monkeypatch):
        # requests raises InvalidSchema -- a ValueError, which neither
        # youtube-transcript-api nor transcripts._fetch_original catches -- so an
        # answering tunnel without PySocks would end the collect run in a
        # traceback instead of a failed video.
        monkeypatch.setenv("SOCKS_PROXY_URL", "socks5h://127.0.0.1:9050")
        monkeypatch.setattr(socket, "create_connection", _connects(_FakeSocket()))
        monkeypatch.setitem(sys.modules, "socks", None)
        assert proxies.socks_proxy_url() is None

    def test_a_misconfigured_value_is_only_reported_once(self, monkeypatch, capsys):
        monkeypatch.setenv("SOCKS_PROXY_URL", "http://not-a-socks-proxy:80")
        for _ in range(5):
            assert proxies.socks_proxy_url() is None
        assert capsys.readouterr().err.count("kein SOCKS-Schema") == 1

    def test_no_proxy_skips_the_probe_entirely(self, monkeypatch):
        calls = []
        monkeypatch.setenv("SOCKS_PROXY_URL", "socks5h://127.0.0.1:9050")
        monkeypatch.setattr(socket, "create_connection", _connects(_FakeSocket(), record=calls))
        assert proxies.socks_proxy_url(no_proxy=True) is None
        assert calls == []


class TestChain:
    def test_socks_comes_before_webshare(self, monkeypatch):
        monkeypatch.setenv("SOCKS_PROXY_URL", "socks5h://127.0.0.1:9050")
        monkeypatch.setenv("WEBSHARE_PROXY_URL", "http://u:p@proxy.example:80")
        monkeypatch.setattr(socket, "create_connection", _connects(_FakeSocket()))
        assert proxies.proxy_chain() == [
            "socks5h://127.0.0.1:9050",
            "http://u:p@proxy.example:80",
        ]

    def test_an_unreachable_socks_proxy_leaves_webshare_alone(self, monkeypatch):
        monkeypatch.setenv("SOCKS_PROXY_URL", "socks5h://127.0.0.1:9050")
        monkeypatch.setenv("WEBSHARE_PROXY_URL", "http://u:p@proxy.example:80")
        monkeypatch.setattr(socket, "create_connection", _connects(ConnectionRefusedError()))
        assert proxies.proxy_chain() == ["http://u:p@proxy.example:80"]

    def test_no_proxy_empties_the_chain(self, monkeypatch):
        monkeypatch.setenv("SOCKS_PROXY_URL", "socks5h://127.0.0.1:9050")
        monkeypatch.setenv("WEBSHARE_PROXY_URL", "http://u:p@proxy.example:80")
        monkeypatch.setattr(socket, "create_connection", _connects(_FakeSocket()))
        assert proxies.proxy_chain(no_proxy=True) == []

    def test_nothing_configured_is_an_empty_chain(self):
        assert proxies.proxy_chain() == []


class TestRedaction:
    def test_the_password_never_reaches_the_log(self):
        line = proxies.redact("http://user:hunter2@proxy.example:80")
        assert "hunter2" not in line
        assert "user@proxy.example:80" in line

    def test_describe_names_both_proxies(self, monkeypatch):
        monkeypatch.setenv("SOCKS_PROXY_URL", "socks5h://127.0.0.1:9050")
        monkeypatch.setenv("WEBSHARE_PROXY_URL", "http://u:secret@proxy.example:80")
        monkeypatch.setattr(socket, "create_connection", _connects(_FakeSocket()))
        lines = proxies.describe()
        assert any(l.startswith("SOCKS:") and "aktiv" in l for l in lines)
        assert any(l.startswith("Webshare:") for l in lines)
        assert not any("secret" in l for l in lines)
