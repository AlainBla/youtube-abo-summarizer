"""Project-level test configuration.

Sets env vars required by modules that read os.environ at import time
(send_mail.py, openrouter.py, etc.).
"""
import os
import sys
import types

# Ensure the project root is on the import path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("SMTP_HOST", "localhost")
os.environ.setdefault("SMTP_PORT", "587")
os.environ.setdefault("SMTP_USER", "test@example.com")
os.environ.setdefault("SMTP_PASS", "testpass")
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")

import pytest


@pytest.fixture(autouse=True)
def _clean_proxy_env(monkeypatch):
    """No proxy is configured unless the test says so, and no probe is inherited.

    proxies.py calls load_dotenv() at import, so without this a developer's own
    .env would decide whether the proxy tests pass. The probe cache is
    per-process and would otherwise carry one test's verdict into the next.
    """
    import proxies

    # PySocks is optional in a checkout (requirements.txt installs it), but the
    # proxy tests are about the ordering, not about whether it happens to be
    # installed here -- so its presence is stubbed when it is missing. The one
    # test that is about the missing package removes it again.
    try:
        import socks  # noqa: F401
    except ImportError:
        monkeypatch.setitem(sys.modules, "socks", types.ModuleType("socks"))

    monkeypatch.delenv("SOCKS_PROXY_URL", raising=False)
    monkeypatch.delenv("WEBSHARE_PROXY_URL", raising=False)
    proxies.reset_probe_cache()
    yield
    proxies.reset_probe_cache()
