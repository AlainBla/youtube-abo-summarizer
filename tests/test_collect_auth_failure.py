"""collect.py tells the caller when OAuth -- not the network -- is the problem.

collect.sh re-runs the collection from a channel file on exactly one exit code.
Getting the predicate wrong in either direction is expensive: too narrow and a
dead token still kills the run, too wide and a DNS hiccup or an exhausted quota
triggers a second full pass over every channel.
"""
import os
import socket
import sys
from datetime import datetime, timezone

import pytest

REPO = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, REPO)

collect = pytest.importorskip("collect", reason="collect.py runtime deps unavailable")

import httplib2
from google.auth.exceptions import RefreshError, TransportError
from googleapiclient.errors import HttpError


def _http_error(status: int, body: bytes = b"{}") -> HttpError:
    """A real HttpError -- a Mock would exercise neither status_code nor str()."""
    return HttpError(httplib2.Response({"status": status}), body, uri="https://example.invalid")


AUTH_FAILURES = [
    ("refresh refused", RefreshError("invalid_grant: Token has been expired or revoked.")),
    ("client_secrets gone", FileNotFoundError("client_secrets.json")),
    ("token unreadable", PermissionError("token.pickle")),
    ("401 on subscriptions", _http_error(401)),
]

NOT_AUTH_FAILURES = [
    # A 5xx from the token endpoint sets retryable -- transport, not auth.
    ("refresh, but retryable", RefreshError("backend error", retryable=True)),
    ("quota exhausted", _http_error(403, b'{"error": {"errors": [{"reason": "quotaExceeded"}]}}')),
    ("rate limited", _http_error(403, b'{"error": {"errors": [{"reason": "rateLimitExceeded"}]}}')),
    ("server error", _http_error(500)),
    ("transport", TransportError("connection reset")),
    ("dns", socket.gaierror("Name or service not known")),
    ("connection refused", ConnectionRefusedError("nope")),
    ("something else entirely", ValueError("bad data")),
]


@pytest.mark.parametrize("label,exc", AUTH_FAILURES, ids=[c[0] for c in AUTH_FAILURES])
def test_auth_failures_are_recognised(label, exc):
    assert collect._is_auth_failure(exc) is True


@pytest.mark.parametrize("label,exc", NOT_AUTH_FAILURES, ids=[c[0] for c in NOT_AUTH_FAILURES])
def test_everything_else_is_not_an_auth_failure(label, exc):
    assert collect._is_auth_failure(exc) is False


def test_the_exit_code_is_distinct_from_the_others():
    assert collect.EXIT_AUTH_FAILED not in (0, 1, collect.EXIT_NEW_VIDEOS)


def test_a_successful_run_can_never_produce_the_auth_code():
    assert collect._exit_code(0) != collect.EXIT_AUTH_FAILED
    assert collect._exit_code(7) != collect.EXIT_AUTH_FAILED


class _Args:
    """argparse.Namespace stand-in for main()'s --auth path."""
    auth = True
    file = None
    video = None
    channels = []
    hours = 4
    prune_days = None
    include_shorts = False
    no_proxy = False
    no_rss = False


def _run_main_auth(monkeypatch):
    monkeypatch.setattr(collect, "parse_args", lambda: _Args())
    monkeypatch.setattr(collect.tr, "log_proxy_config", lambda no_proxy=False: None)
    return collect.main()


def test_an_unusable_token_exits_with_the_auth_code_without_building_a_service(monkeypatch):
    monkeypatch.setattr(collect.youtube_client, "has_usable_token", lambda: False)
    monkeypatch.setattr(
        collect, "build_service",
        lambda: pytest.fail("build_service() would open the browser flow and hang under cron"),
    )

    with pytest.raises(SystemExit) as exc:
        _run_main_auth(monkeypatch)
    assert exc.value.code == collect.EXIT_AUTH_FAILED


def test_a_revoked_token_surfacing_as_401_exits_with_the_auth_code(monkeypatch):
    # The token is locally valid, so build_service() succeeds and the 401 only
    # arrives from subscriptions.list. That is why the guard sits there.
    monkeypatch.setattr(collect.youtube_client, "has_usable_token", lambda: True)
    monkeypatch.setattr(collect, "build_service", lambda: object())

    def boom(service):
        raise _http_error(401)

    monkeypatch.setattr(collect, "get_subscribed_channels", boom)

    with pytest.raises(SystemExit) as exc:
        _run_main_auth(monkeypatch)
    assert exc.value.code == collect.EXIT_AUTH_FAILED


def test_a_non_auth_error_is_passed_through_untouched(monkeypatch):
    monkeypatch.setattr(collect.youtube_client, "has_usable_token", lambda: True)
    monkeypatch.setattr(collect, "build_service", lambda: object())

    def boom(service):
        raise _http_error(500)

    monkeypatch.setattr(collect, "get_subscribed_channels", boom)

    with pytest.raises(HttpError):
        _run_main_auth(monkeypatch)


def test_an_account_without_subscriptions_is_reported_as_an_auth_problem(monkeypatch):
    # A token for the wrong Google account authenticates fine and returns
    # nothing -- silent zero-video runs forever. The channel file is the better
    # answer, so say so instead of exiting 0.
    monkeypatch.setattr(collect.youtube_client, "has_usable_token", lambda: True)
    monkeypatch.setattr(collect, "build_service", lambda: object())
    monkeypatch.setattr(collect, "get_subscribed_channels", lambda service: [])

    with pytest.raises(SystemExit) as exc:
        _run_main_auth(monkeypatch)
    assert exc.value.code == collect.EXIT_AUTH_FAILED


def test_a_run_that_never_meant_to_authorise_refuses_to_build_a_service(monkeypatch):
    # --file/--video runs must fail fast instead of reaching build_service(),
    # which would open a browser prompt and block forever under cron.
    monkeypatch.setattr(collect.youtube_client, "has_usable_token", lambda: False)
    monkeypatch.setattr(
        collect, "build_service",
        lambda: pytest.fail("build_service() must not be reached without a usable token"),
    )

    get = collect._lazy_service(require_token=True)
    with pytest.raises(RuntimeError):
        get()


def test_the_interactive_first_authorisation_is_still_possible(monkeypatch):
    # --auth does not set require_token: a terminal run with no token yet has to
    # be able to go through the browser flow, that is how it is set up at all.
    monkeypatch.setattr(collect.youtube_client, "has_usable_token", lambda: False)
    monkeypatch.setattr(collect, "build_service", lambda: "service")

    assert collect._lazy_service()() == "service"
