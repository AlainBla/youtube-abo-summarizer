"""Tests for M1: CORS restriction — must not use wildcard origin.

Access-Control-Allow-Origin must reflect only trusted origins:
- BASE_URL (same as server's own origin)
- "null" (file:// pages that send Origin: null)

Foreign origins must receive no ACAO header (or an absent/empty one).
Vary: Origin must always be present so caches do not serve wrong origins.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def test_cors_allows_base_url_origin(client):
    """Requests from the server's own origin must get ACAO matching that origin."""
    r = client.get("/api/whoami", headers={"Origin": "http://testserver"})
    assert r.headers.get("Access-Control-Allow-Origin") == "http://testserver"


def test_cors_blocks_foreign_origin(client):
    """Requests from an untrusted origin must not get a permissive ACAO header."""
    r = client.get("/api/whoami", headers={"Origin": "https://evil.com"})
    acao = r.headers.get("Access-Control-Allow-Origin", "")
    assert acao != "*"
    assert acao != "https://evil.com"


def test_cors_allows_null_origin(client):
    """file:// pages send Origin: null — the sync server must allow them."""
    r = client.get("/api/whoami", headers={"Origin": "null"})
    assert r.headers.get("Access-Control-Allow-Origin") == "null"


def test_cors_vary_header_always_present(client):
    """Vary: Origin must be set so caches do not bleed responses across origins."""
    r = client.get("/api/whoami", headers={"Origin": "http://testserver"})
    vary = r.headers.get("Vary", "")
    assert "Origin" in vary


def test_cors_no_origin_header_no_acao(client):
    """Requests without Origin header do not need an ACAO header."""
    r = client.get("/api/whoami")
    # The important thing is wildcard is never set.
    assert r.headers.get("Access-Control-Allow-Origin", "") != "*"
