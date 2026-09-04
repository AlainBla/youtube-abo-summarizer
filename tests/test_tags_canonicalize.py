"""Tests for canonicalize() — the only gate that decides what a tag is."""
import json
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

import tags


@pytest.fixture
def aliases(tmp_path, monkeypatch):
    """Point the alias table at a temp file and return a writer for it."""
    path = tmp_path / "tag_aliases.json"
    monkeypatch.setattr(tags, "ALIASES_PATH", path)

    def write(mapping):
        path.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
        tags.load_aliases.cache_clear()

    write({})
    yield write
    tags.load_aliases.cache_clear()


def test_exact_vocabulary_hit_is_kept(aliases):
    assert tags.canonicalize(["Indie-Spiele"]) == (["Indie-Spiele"], [])


def test_case_only_difference_is_mapped_to_the_canonical_spelling(aliases):
    assert tags.canonicalize(["indie-spiele"]) == (["Indie-Spiele"], [])


def test_surrounding_whitespace_is_ignored(aliases):
    assert tags.canonicalize(["  Gaming  "]) == (["Gaming"], [])


def test_alias_maps_to_one_tag(aliases):
    aliases({"Bloodborne": ["Soulslike"]})
    assert tags.canonicalize(["Bloodborne"]) == (["Soulslike"], [])


def test_alias_maps_to_two_tags(aliases):
    aliases({"PSVR2": ["VR-Gaming", "PlayStation"]})
    assert tags.canonicalize(["PSVR2"]) == (["VR-Gaming", "PlayStation"], [])


def test_alias_lookup_ignores_case(aliases):
    aliases({"Bloodborne": ["Soulslike"]})
    assert tags.canonicalize(["BLOODBORNE"]) == (["Soulslike"], [])


def test_alias_with_an_empty_target_is_dropped_without_being_reported(aliases):
    """An empty list means "deliberately unmapped" — not a candidate."""
    aliases({"Elon Ruskin": []})
    assert tags.canonicalize(["Elon Ruskin"]) == ([], [])


def test_unknown_tag_is_rejected_and_reported(aliases):
    assert tags.canonicalize(["Straße von Hormus"]) == ([], ["Straße von Hormus"])


def test_alias_targets_outside_the_vocabulary_are_ignored(aliases):
    aliases({"Valve": ["Steam", "Erfundener Tag"]})
    assert tags.canonicalize(["Valve"]) == (["Steam"], [])


def test_duplicates_collapse_and_keep_first_order(aliases):
    aliases({"Indie Game": ["Indie-Spiele"]})
    assert tags.canonicalize(["Gaming", "Indie Game", "Indie-Spiele", "Gaming"]) == (
        ["Gaming", "Indie-Spiele"],
        [],
    )


def test_more_than_five_tags_are_cut(aliases):
    raw = ["Gaming", "Indie-Spiele", "Retro-Gaming", "Spieletest", "Nintendo", "Steam"]
    kept, rejected = tags.canonicalize(raw)
    assert kept == raw[: tags.MAX_TAGS]
    assert rejected == []


def test_empty_input_gives_empty_output(aliases):
    assert tags.canonicalize([]) == ([], [])


def test_empty_strings_are_skipped_silently(aliases):
    assert tags.canonicalize(["", "   "]) == ([], [])


def test_missing_alias_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(tags, "ALIASES_PATH", tmp_path / "does-not-exist.json")
    tags.load_aliases.cache_clear()
    assert tags.load_aliases() == {}
    tags.load_aliases.cache_clear()
