"""TRUSTED_PROXIES makes the per-IP rate limit see the real client behind a proxy.

ProxyFix is applied at import time, so each case runs in its own interpreter.
"""
import os
import subprocess
import sys
import textwrap

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT = textwrap.dedent("""
    import sys, sync_server
    sync_server.DB_PATH = sys.argv[1]
    sync_server.init_db(sys.argv[1])
    c = sync_server.app.test_client()
    def hit(ip):
        return c.post("/auth/passkey/login/options",
                      headers={"X-Forwarded-For": ip}).status_code
    for _ in range(sync_server.PASSKEY_RATE_LIMIT_MAX):
        hit("203.0.113.1")
    print(hit("203.0.113.1"), hit("198.51.100.7"))
""")


def _run(tmp_path, trusted):
    env = dict(os.environ, SECRET_KEY="x", BASE_URL="http://testserver",
               TRUSTED_PROXIES=trusted)
    out = subprocess.run(
        [sys.executable, "-c", SCRIPT, str(tmp_path / "t.db")],
        cwd=HERE, env=env, capture_output=True, text=True, check=True,
    ).stdout.split()
    return [int(x) for x in out]


def test_limit_is_per_client_behind_trusted_proxy(tmp_path):
    assert _run(tmp_path, "1") == [429, 200]


def test_forwarded_header_ignored_by_default(tmp_path):
    # Without a trusted proxy X-Forwarded-For is client-controlled, so it must
    # not let a caller dodge the limit by inventing addresses.
    assert _run(tmp_path, "0") == [429, 429]
