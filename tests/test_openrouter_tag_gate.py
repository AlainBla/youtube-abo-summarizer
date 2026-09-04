"""summarize_video() must return only vocabulary tags and log the rest."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

import openrouter
import tags


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)
        self.finish_reason = "stop"


class _Response:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _Completions:
    def __init__(self, content):
        self._content = content

    def create(self, **kwargs):
        return _Response(self._content)


class _Client:
    def __init__(self, content):
        self.chat = type("Chat", (), {"completions": _Completions(content)})()


@pytest.fixture(autouse=True)
def no_aliases(tmp_path, monkeypatch):
    """Point the alias table at a path that does not exist.

    A later task lands a real tag_aliases.json at the repository root that
    maps "Bloodborne" onto a real vocabulary tag — without this isolation
    these gate tests would break the moment it lands.
    """
    path = tmp_path / "tag_aliases.json"
    monkeypatch.setattr(tags, "ALIASES_PATH", path)
    tags.load_aliases.cache_clear()
    yield
    tags.load_aliases.cache_clear()


@pytest.fixture
def recorded(monkeypatch):
    """Capture what record_candidates() is handed instead of writing a file."""
    seen: list[list[str]] = []
    monkeypatch.setattr(tags, "record_candidates", lambda rejected: seen.append(list(rejected)))
    monkeypatch.setattr(openrouter.tag_vocab, "record_candidates", lambda r: seen.append(list(r)))
    return seen


def _run(monkeypatch, content):
    monkeypatch.setattr(openrouter, "build_client", lambda: _Client(content))
    return openrouter.summarize_video("vid1", "Titel", "[0:00] Transkript", "test-model")


def test_off_list_tags_do_not_reach_the_caller(monkeypatch, recorded):
    content = "<p>Text</p>\n<!-- tags: Gaming, Bloodborne, Indie-Spiele -->"
    _summary, kept = _run(monkeypatch, content)
    assert kept == ["Gaming", "Indie-Spiele"]


def test_off_list_tags_are_recorded_as_candidates(monkeypatch, recorded):
    content = "<p>Text</p>\n<!-- tags: Gaming, Bloodborne -->"
    _run(monkeypatch, content)
    assert recorded == [["Bloodborne"]]


def test_a_response_with_no_usable_tag_returns_an_empty_list(monkeypatch, recorded):
    content = "<p>Text</p>\n<!-- tags: Bloodborne, Valve -->"
    _summary, kept = _run(monkeypatch, content)
    assert kept == []


def test_the_summary_itself_is_unaffected(monkeypatch, recorded):
    content = "<p>Text</p>\n<!-- tags: Gaming -->"
    summary, _kept = _run(monkeypatch, content)
    assert "<p>Text</p>" in summary
