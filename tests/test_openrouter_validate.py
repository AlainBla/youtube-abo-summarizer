"""Tests for _validate_summary — degenerate and truncated model output."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

import openrouter
from openrouter import SummaryRejected


def test_good_summary_passes():
    openrouter._validate_summary("<h3>A</h3>\n<p>Ein normaler Satz.</p>", "stop")


def test_finish_reason_length_is_rejected():
    with pytest.raises(SummaryRejected, match="truncated"):
        openrouter._validate_summary("<p>Ein abgeschnittener Satz", "length")


def test_missing_finish_reason_is_tolerated():
    """Not every provider reports finish_reason; absence must not reject."""
    openrouter._validate_summary("<p>Ein normaler Satz.</p>", None)


def test_repetition_loop_is_rejected():
    with pytest.raises(SummaryRejected, match="repetition loop"):
        openrouter._validate_summary("<p>" + "our " * 200 + "</p>", "stop")


def test_repetition_across_newlines_is_rejected():
    with pytest.raises(SummaryRejected, match="repetition loop"):
        openrouter._validate_summary("our\n" * 200, "stop")


def test_repetition_below_threshold_passes():
    """Fewer than 30 repeats is not enough evidence of a loop."""
    openrouter._validate_summary("<p>" + "our " * 10 + "Ende.</p>", "stop")


def test_repeated_word_with_other_words_between_passes():
    openrouter._validate_summary("<p>" + "our game our game " * 50 + "</p>", "stop")


def test_real_degenerate_summary_shape_is_rejected():
    """The exact shape that broke T0oY6gr5oHM and B870YxMs-Gs."""
    with pytest.raises(SummaryRejected):
        openrouter._validate_summary(("our " * 16384).strip(), "length")
