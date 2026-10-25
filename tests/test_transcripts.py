"""Tests for transcripts.get_transcript() (original-language) and get_manual_transcript()."""
import pytest
from unittest.mock import MagicMock, patch


def _api_serving(transcript_list):
    """A YouTubeTranscriptApi stand-in whose list() serves one transcript list."""
    api = MagicMock()
    api.list.return_value = transcript_list
    return api


def _entry(start=0.0, text="Hello world"):
    e = MagicMock()
    e.start = start
    e.text = text
    return e


def _make_transcript(lang, is_generated, text="content"):
    t = MagicMock()
    t.language_code = lang
    t.is_generated = is_generated
    t.fetch.return_value = [_entry(0.0, text)]
    return t


def _transcript_list(*transcripts):
    tl = MagicMock()
    # Use side_effect (factory) so each iter() call gets a fresh iterator.
    # return_value would share one exhausted iterator across multiple for-loops.
    tl.__iter__ = MagicMock(side_effect=lambda: iter(transcripts))
    def find_generated(langs):
        from youtube_transcript_api import NoTranscriptFound
        for t in transcripts:
            if t.is_generated and t.language_code in langs:
                return t
        raise NoTranscriptFound("", langs, [])
    def find_manual(langs):
        from youtube_transcript_api import NoTranscriptFound
        for t in transcripts:
            if not t.is_generated and t.language_code in langs:
                return t
        raise NoTranscriptFound("", langs, [])
    tl.find_generated_transcript = find_generated
    tl.find_manually_created_transcript = find_manual
    return tl


class TestGetTranscriptOriginalLanguage:
    def _call(self, transcript_list):
        import transcripts as tr
        with patch.object(tr, "_api_for", return_value=_api_serving(transcript_list)):
            return tr.get_transcript("vid123")

    def test_returns_three_tuple(self):
        tl = _transcript_list(_make_transcript("ja", is_generated=True))
        text, lang, err = self._call(tl)
        assert lang == "ja"
        assert err is None
        assert text is not None

    def test_prefers_manual_in_original_lang(self):
        # Both manual-ja and generated-ja exist — manual wins
        manual_ja = _make_transcript("ja", is_generated=False, text="manual")
        gen_ja = _make_transcript("ja", is_generated=True, text="generated")
        tl = _transcript_list(gen_ja, manual_ja)
        text, lang, err = self._call(tl)
        assert lang == "ja"
        assert "manual" in text

    def test_falls_back_to_generated_when_no_manual(self):
        gen_ja = _make_transcript("ja", is_generated=True, text="auto")
        tl = _transcript_list(gen_ja)
        text, lang, err = self._call(tl)
        assert lang == "ja"
        assert "auto" in text

    def test_falls_back_to_first_transcript_when_no_generated(self):
        # Only manually created, no auto-generated → take first
        manual_en = _make_transcript("en", is_generated=False, text="english manual")
        tl = _transcript_list(manual_en)
        text, lang, err = self._call(tl)
        assert lang == "en"
        assert text is not None

    def test_returns_unavailable_when_transcript_list_empty(self):
        from youtube_transcript_api import NoTranscriptFound
        tl = MagicMock()
        tl.__iter__ = MagicMock(side_effect=lambda: iter([]))
        tl.find_generated_transcript = MagicMock(side_effect=NoTranscriptFound("", [], []))
        tl.find_manually_created_transcript = MagicMock(side_effect=NoTranscriptFound("", [], []))
        import transcripts as tr
        with patch.object(tr, "_api_for", return_value=_api_serving(tl)):
            text, lang, err = tr.get_transcript("vid123")
        assert text is None
        assert lang is None
        assert err == "unavailable"


class TestGetManualTranscript:
    def _call(self, transcript_list, preferred=None):
        import transcripts as tr
        kwargs = {"preferred_langs": preferred} if preferred else {}
        with patch.object(tr, "_api_for", return_value=_api_serving(transcript_list)):
            return tr.get_manual_transcript("vid123", **kwargs)

    def test_returns_manual_de_when_available(self):
        manual_de = _make_transcript("de", is_generated=False, text="deutsch")
        gen_ja = _make_transcript("ja", is_generated=True)
        tl = _transcript_list(gen_ja, manual_de)
        text, lang = self._call(tl)
        assert lang == "de"
        assert "deutsch" in text

    def test_returns_manual_en_when_only_en_available(self):
        manual_en = _make_transcript("en", is_generated=False, text="english")
        gen_ja = _make_transcript("ja", is_generated=True)
        tl = _transcript_list(gen_ja, manual_en)
        text, lang = self._call(tl)
        assert lang == "en"

    def test_returns_none_none_when_no_manual(self):
        gen_ja = _make_transcript("ja", is_generated=True)
        tl = _transcript_list(gen_ja)
        text, lang = self._call(tl)
        assert text is None
        assert lang is None

    def test_prefers_de_over_en(self):
        manual_de = _make_transcript("de", is_generated=False)
        manual_en = _make_transcript("en", is_generated=False)
        gen_ja = _make_transcript("ja", is_generated=True)
        tl = _transcript_list(gen_ja, manual_de, manual_en)
        text, lang = self._call(tl, preferred=["de", "en"])
        assert lang == "de"


class TestProxyOrder:
    """A reachable SOCKS proxy is what the first attempt goes through.

    This module has never tried a direct connection first -- YouTube blocks a
    server's own IP for transcript requests too readily -- so the ordering
    here is: SOCKS, then Webshare, then the country-pinned Webshare URL.
    """

    def _apis(self, monkeypatch, behaviour):
        """Patch transcripts._make_api and record the proxy URL of each api built."""
        import transcripts as tr

        built = []

        def make(url):
            built.append(url)
            api = MagicMock()
            outcome = behaviour(url)
            if isinstance(outcome, Exception):
                api.list.side_effect = outcome
            else:
                api.list.return_value = outcome
            return api

        tr._api_cache.clear()
        monkeypatch.setattr(tr, "_make_api", make)
        return built

    def _socks_alive(self, monkeypatch, alive=True):
        import socket
        from test_proxies import _FakeSocket, _connects

        monkeypatch.setenv("SOCKS_PROXY_URL", "socks5h://127.0.0.1:9050")
        sock = _FakeSocket() if alive else ConnectionRefusedError()
        monkeypatch.setattr(socket, "create_connection", _connects(sock))

    def test_the_socks_proxy_is_used_before_webshare(self, monkeypatch):
        import transcripts as tr

        self._socks_alive(monkeypatch)
        monkeypatch.setenv("WEBSHARE_PROXY_URL", "http://user:pw@proxy.example:80")
        tl = _transcript_list(_make_transcript("de", is_generated=True))
        built = self._apis(monkeypatch, lambda url: tl)

        text, lang, err = tr.get_transcript("vid123")
        assert err is None
        assert built == ["socks5h://127.0.0.1:9050"]

    def test_an_unreachable_socks_proxy_leaves_webshare_as_the_primary(self, monkeypatch):
        import transcripts as tr

        self._socks_alive(monkeypatch, alive=False)
        monkeypatch.setenv("WEBSHARE_PROXY_URL", "http://user:pw@proxy.example:80")
        tl = _transcript_list(_make_transcript("de", is_generated=True))
        built = self._apis(monkeypatch, lambda url: tl)

        tr.get_transcript("vid123")
        assert built == ["http://user:pw@proxy.example:80"]

    def test_a_blocked_socks_proxy_falls_back_to_webshare(self, monkeypatch):
        from youtube_transcript_api import IpBlocked
        import transcripts as tr

        self._socks_alive(monkeypatch)
        monkeypatch.setenv("WEBSHARE_PROXY_URL", "http://user:pw@proxy.example:80")
        tl = _transcript_list(_make_transcript("de", is_generated=True))
        built = self._apis(
            monkeypatch,
            lambda url: IpBlocked("vid123") if url.startswith("socks") else tl,
        )

        text, lang, err = tr.get_transcript("vid123")
        assert err is None
        assert built[0].startswith("socks")
        assert built[1] == "http://user:pw@proxy.example:80"

    def test_the_country_retry_is_derived_from_webshare_not_from_socks(self, monkeypatch):
        import transcripts as tr

        self._socks_alive(monkeypatch)
        monkeypatch.setenv("WEBSHARE_PROXY_URL", "http://user:pw@proxy.example:80")
        monkeypatch.setattr(tr, "_FALLBACK_COUNTRY", "DE")
        from youtube_transcript_api import VideoUnplayable

        built = self._apis(
            monkeypatch,
            lambda url: VideoUnplayable("vid123", reason="Video not available in your country",
                                        sub_reasons=[])
            if "-DE" not in url else _transcript_list(_make_transcript("de", is_generated=True)),
        )

        text, lang, err = tr.get_transcript("vid123")
        assert err is None
        # The socks URL is never rewritten: -DE is a Webshare username convention.
        assert built[-1] == "http://user-DE:pw@proxy.example:80"
        assert not any(url.startswith("socks") and "-DE" in url for url in built)

    def test_no_proxy_uses_a_direct_connection(self, monkeypatch):
        import transcripts as tr

        self._socks_alive(monkeypatch)
        monkeypatch.setenv("WEBSHARE_PROXY_URL", "http://user:pw@proxy.example:80")
        tl = _transcript_list(_make_transcript("de", is_generated=True))
        built = self._apis(monkeypatch, lambda url: tl)

        tr.get_transcript_no_proxy("vid123")
        assert built == [None]


# ── Original language from YouTube's audio-track data (AI dubbing) ───────────
#
# Since autumn 2026 many English videos carry ~20 AI-dubbed audio tracks, each
# with an auto-generated caption track, listed alphabetically by language name:
# "Arabic (auto-generated)" first. Taking the first ASR track as the original
# stored 181 Arabic transcripts, whose quotes then leaked into summaries.

DUB_LANGS = ["ar", "bn", "nl", "en", "fr", "de", "uk"]


def _track(code, asr=True):
    t = {"baseUrl": f"https://example.com/{code}", "languageCode": code,
         "name": {"runs": [{"text": code}]}, "isTranslatable": True}
    if asr:
        t["kind"] = "asr"
    return t


def _dubbed_captions(manual_en=False):
    """Captions JSON shaped like the real one: original English ASR at index 3."""
    tracks = [_track(c) for c in DUB_LANGS]
    order = [3] + [i for i in range(len(tracks)) if i != 3]
    if manual_en:
        tracks.insert(3, _track("en-US", asr=False))   # manual track, index 3
        order = [3, 4] + [i for i in range(len(tracks)) if i not in (3, 4)]
    audio = [{"audioTrackId": f"{c}.10", "captionTrackIndices": order} for c in ("pt-BR", "fr-FR", "de-DE")]
    audio.append({"audioTrackId": "en-US.4", "captionTrackIndices": order})
    return {"captionTracks": tracks, "audioTracks": audio, "defaultAudioTrackIndex": 0}


def test_original_language_skips_arabic_dub():
    from transcripts import _original_language
    assert _original_language(_dubbed_captions()) == "en"


def test_original_language_prefers_listed_manual_track():
    from transcripts import _original_language
    assert _original_language(_dubbed_captions(manual_en=True)) == "en-US"


def test_original_language_independent_of_default_audio_track():
    from transcripts import _original_language
    cj = _dubbed_captions()
    for d in range(len(cj["audioTracks"])):
        cj["defaultAudioTrackIndex"] = d
        assert _original_language(cj) == "en"


def test_original_language_unknown_without_audio_tracks():
    from transcripts import _original_language
    assert _original_language({"captionTracks": [_track("en")]}) is None
    assert _original_language({"captionTracks": [], "audioTracks": [{"captionTrackIndices": [5]}]}) is None


def test_list_transcripts_reads_hint_from_one_request():
    import transcripts as tr
    from youtube_transcript_api import YouTubeTranscriptApi
    api = YouTubeTranscriptApi()
    calls = []
    fetcher = MagicMock()
    fetcher._fetch_captions_json.side_effect = lambda vid: calls.append(vid) or _dubbed_captions()
    api._fetcher = fetcher
    tl, hint = tr._list_transcripts(api, "vid")
    assert hint == "en"
    assert calls == ["vid"]
    assert {t.language_code for t in tl} == set(DUB_LANGS)


def test_fetch_original_uses_hint_not_first_asr_track():
    import transcripts as tr
    tl = _transcript_list(*[_make_transcript(c, True, text=f"text-{c}") for c in DUB_LANGS])
    with patch.object(tr, "_list_transcripts", return_value=(tl, "en")):
        text, lang, err = tr._fetch_original(MagicMock(), "vid")
    assert (lang, err) == ("en", None)
    assert "text-en" in text


def test_fetch_original_hint_prefers_manual_over_asr_same_language():
    import transcripts as tr
    tl = _transcript_list(_make_transcript("ar", True, "arabic"),
                          _make_transcript("en", True, "asr"),
                          _make_transcript("en-US", False, "manual"))
    with patch.object(tr, "_list_transcripts", return_value=(tl, "en-US")):
        text, lang, _ = tr._fetch_original(MagicMock(), "vid")
    assert lang == "en-US" and "manual" in text


def test_fetch_original_without_hint_keeps_old_behaviour():
    import transcripts as tr
    tl = _transcript_list(_make_transcript("uk", True, "ukrainian"))
    with patch.object(tr, "_list_transcripts", return_value=(tl, None)):
        _, lang, _ = tr._fetch_original(MagicMock(), "vid")
    assert lang == "uk"
