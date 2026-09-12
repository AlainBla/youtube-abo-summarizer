"""A token check that never opens a browser.

build_service() falls into InstalledAppFlow.run_local_server() whenever the
cached credentials cannot be used or refreshed -- a browser prompt with nobody
to answer it under cron, and it blocks forever instead of failing. Callers that
must not block ask has_usable_token() first, so the same decision is made
without side effects.
"""
import os
import pickle
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, REPO)

yc = pytest.importorskip("youtube_client", reason="googleapiclient unavailable")


class _Creds:
    """Stand-in for google.oauth2.credentials.Credentials.

    Only the three attributes build_service() consults; picklable, so the file
    on disk is the real thing rather than a patched loader.
    """

    def __init__(self, valid=True, expired=False, refresh_token="rt"):
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token


def _write_token(tmp_path, monkeypatch, obj):
    path = tmp_path / "token.pickle"
    with open(path, "wb") as f:
        pickle.dump(obj, f)
    monkeypatch.setattr(yc, "TOKEN_FILE", str(path))
    return path


def test_no_token_file_at_all(tmp_path, monkeypatch):
    monkeypatch.setattr(yc, "TOKEN_FILE", str(tmp_path / "nothing.pickle"))
    assert yc.has_usable_token() is False


def test_valid_credentials(tmp_path, monkeypatch):
    _write_token(tmp_path, monkeypatch, _Creds(valid=True))
    assert yc.has_usable_token() is True


def test_expired_but_refreshable(tmp_path, monkeypatch):
    # build_service() refreshes this one. The refresh may still be refused --
    # that raises RefreshError, which the caller can act on, unlike a hang.
    _write_token(tmp_path, monkeypatch, _Creds(valid=False, expired=True, refresh_token="rt"))
    assert yc.has_usable_token() is True


def test_expired_without_a_refresh_token(tmp_path, monkeypatch):
    # This is the hang: build_service() would go for the browser flow.
    _write_token(tmp_path, monkeypatch, _Creds(valid=False, expired=True, refresh_token=None))
    assert yc.has_usable_token() is False


def test_invalid_and_not_even_expired(tmp_path, monkeypatch):
    _write_token(tmp_path, monkeypatch, _Creds(valid=False, expired=False, refresh_token="rt"))
    assert yc.has_usable_token() is False


def test_a_truncated_token_file(tmp_path, monkeypatch):
    path = tmp_path / "token.pickle"
    path.write_bytes(b"")
    monkeypatch.setattr(yc, "TOKEN_FILE", str(path))
    assert yc.has_usable_token() is False


def test_a_file_that_is_not_a_pickle_at_all(tmp_path, monkeypatch):
    path = tmp_path / "token.pickle"
    path.write_text("definitely not a pickle")
    monkeypatch.setattr(yc, "TOKEN_FILE", str(path))
    assert yc.has_usable_token() is False


def test_a_pickle_of_something_else(tmp_path, monkeypatch):
    # An object without .valid must not raise AttributeError out of the check.
    _write_token(tmp_path, monkeypatch, {"not": "credentials"})
    assert yc.has_usable_token() is False


def test_the_check_never_opens_the_oauth_flow(tmp_path, monkeypatch):
    class _Flow:
        @staticmethod
        def from_client_secrets_file(*args, **kwargs):
            pytest.fail("has_usable_token() must not start the OAuth flow")

    monkeypatch.setattr(yc, "TOKEN_FILE", str(tmp_path / "nothing.pickle"))
    monkeypatch.setattr(yc, "InstalledAppFlow", _Flow)
    assert yc.has_usable_token() is False
