"""Tests for the controlled tag vocabulary itself."""
import collections
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import tags


def test_no_tag_appears_in_two_groups():
    counter = collections.Counter(t for group in tags.VOCABULARY.values() for t in group)
    assert [t for t, c in counter.items() if c > 1] == []


def test_all_tags_matches_the_groups():
    flat = [t for group in tags.VOCABULARY.values() for t in group]
    assert tags.ALL_TAGS == frozenset(flat)
    assert len(flat) == len(tags.ALL_TAGS)


def test_tags_are_stripped_and_non_empty():
    for tag in tags.ALL_TAGS:
        assert tag == tag.strip()
        assert tag


def test_prompt_block_lists_every_tag_and_group():
    block = tags.prompt_block()
    for group, entries in tags.VOCABULARY.items():
        assert group in block
        for tag in entries:
            assert tag in block


def test_prompt_block_has_one_line_per_group():
    assert len(tags.prompt_block().splitlines()) == len(tags.VOCABULARY)
