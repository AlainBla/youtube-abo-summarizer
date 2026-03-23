"""Tests for H2: SQLite-backed rate limiter — survives in-memory state reset.

The in-memory defaultdict is insufficient for multi-worker deployments.
After clearing _rate_limits (simulating worker restart), the rate limit must
still be enforced because state is persisted in the database.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import sync_server


def test_rate_limit_state_survives_memory_reset(client, mock_smtp, monkeypatch):
    """Rate limit must hold even after in-memory dict is cleared.

    With the in-memory implementation this test fails: after _rate_limits.clear()
    the counter resets and the 4th request gets through.
    With a SQLite-backed implementation the 4th request must still be blocked.
    """
    # Use a monkeypatched time so hits are within the 600-second window.
    fixed_time = 1_000_000.0
    monkeypatch.setattr(sync_server.time, "time", lambda: fixed_time)

    for _ in range(3):
        client.post(
            "/auth/request-link",
            json={"email": "user@example.com", "redirect_uri": "http://testserver/"},
        )

    # Simulate worker restart: wipe in-memory state.
    if hasattr(sync_server, "_rate_limits"):
        sync_server._rate_limits.clear()

    # 4th attempt within same window — must still be blocked.
    r = client.post(
        "/auth/request-link",
        json={"email": "user@example.com", "redirect_uri": "http://testserver/"},
    )
    assert r.status_code == 200  # silently rate-limited
    assert len(mock_smtp) == 3   # 4th not sent
