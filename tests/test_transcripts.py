"""Tests for transcripts.get_transcript() (original-language) and get_manual_transcript()."""
import pytest
from unittest.mock import MagicMock, patch


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
        with patch.object(tr._api, "list", return_value=transcript_list):
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


class TestGetManualTranscript:
    def _call(self, transcript_list, preferred=None):
        import transcripts as tr
        kwargs = {"preferred_langs": preferred} if preferred else {}
        with patch.object(tr._api, "list", return_value=transcript_list):
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
