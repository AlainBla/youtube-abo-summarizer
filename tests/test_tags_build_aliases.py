"""Tests for the alias-table builder's parsing and validation."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import tags


BATCH = ["Bloodborne", "Valve", "Elon Ruskin"]


def test_plain_json_object_is_parsed():
    text = '{"Bloodborne": ["Soulslike"], "Valve": ["Steam"], "Elon Ruskin": []}'
    assert tags.parse_alias_response(text, BATCH) == {
        "Bloodborne": ["Soulslike"],
        "Valve": ["Steam"],
        "Elon Ruskin": [],
    }


def test_code_fence_is_stripped():
    text = '```json\n{"Bloodborne": ["Soulslike"]}\n```'
    assert tags.parse_alias_response(text, BATCH) == {"Bloodborne": ["Soulslike"]}


def test_targets_outside_the_vocabulary_are_dropped():
    text = '{"Valve": ["Steam", "Halbwegs Erfunden"]}'
    assert tags.parse_alias_response(text, BATCH) == {"Valve": ["Steam"]}


def test_more_than_two_targets_are_cut():
    text = '{"Bloodborne": ["Soulslike", "Rollenspiel", "Gaming", "Horror-Spiel"]}'
    assert tags.parse_alias_response(text, BATCH) == {
        "Bloodborne": ["Soulslike", "Rollenspiel"]
    }


def test_keys_outside_the_batch_are_ignored():
    text = '{"Bloodborne": ["Soulslike"], "Nicht Gefragt": ["Gaming"]}'
    assert tags.parse_alias_response(text, BATCH) == {"Bloodborne": ["Soulslike"]}


def test_terms_the_model_omitted_are_absent_so_a_later_run_retries_them():
    text = '{"Bloodborne": ["Soulslike"]}'
    result = tags.parse_alias_response(text, BATCH)
    assert "Valve" not in result
    assert "Elon Ruskin" not in result


def test_a_non_list_value_is_ignored():
    text = '{"Valve": "Steam"}'
    assert tags.parse_alias_response(text, BATCH) == {}


def test_unparseable_output_gives_an_empty_mapping():
    assert tags.parse_alias_response("Tut mir leid, das kann ich nicht.", BATCH) == {}


def test_duplicate_targets_collapse():
    text = '{"Valve": ["Steam", "Steam"]}'
    assert tags.parse_alias_response(text, BATCH) == {"Valve": ["Steam"]}


def test_build_aliases_queues_a_case_insensitive_duplicate_only_once(tmp_path, monkeypatch, capsys):
    """Two store spellings of the same tag ('AI' and 'ai') must not both be
    queued — the later one written would silently overwrite the earlier one
    once sort_keys=True picks a winner."""
    monkeypatch.setattr(tags, "ALIASES_PATH", tmp_path / "tag_aliases.json")
    monkeypatch.setattr(tags, "stored_tags", lambda: ["AI", "ai", "ML", "AI"])
    tags.build_aliases(None, True, "some-model")
    out = capsys.readouterr().out
    assert "2 unzugeordnete Tags in 1 Stapel(n) à 100" in out
