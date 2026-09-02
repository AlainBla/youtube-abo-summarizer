"""Tests for _drop_stray_paragraph_ends — models closing </p> mid-sentence.

Some summaries close a paragraph right after every timestamp link, before the
sentence-final period ("... </a></p>. Next sentence ..."). The browser then ends
the paragraph there and every following sentence renders as its own block
starting with a dot.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from openrouter import _drop_stray_paragraph_ends as drop
from openrouter import repair_summary_html


LINK = '<a href="https://www.youtube.com/watch?v=X&t=64" class="ts-link">1:04</a>'


def test_stray_close_before_sentence_period_is_dropped():
    html = f"<p>Erster Satz {LINK}</p>. Zweiter Satz.</p>"
    assert drop(html) == f"<p>Erster Satz {LINK}. Zweiter Satz.</p>"


def test_stray_close_before_capitalised_sentence_is_dropped():
    html = f"<p>Erster Satz {LINK}</p> Am Ende folgt mehr.</p>"
    assert drop(html) == f"<p>Erster Satz {LINK} Am Ende folgt mehr.</p>"


def test_stray_close_without_link_drops_the_space_before_punctuation():
    html = '<p>Assets des Prequels "F-stop" </p>. Da Steam Plattform war.</p>'
    assert drop(html) == '<p>Assets des Prequels "F-stop". Da Steam Plattform war.</p>'


def test_real_paragraph_end_before_a_heading_is_untouched():
    html = f"<p>Ein Satz {LINK}</p>\n<h3>Nächster Abschnitt</h3>"
    assert drop(html) == html


def test_real_paragraph_end_before_another_paragraph_is_untouched():
    html = f"<p>Ein Satz {LINK}</p>\n\n<p>Noch ein Satz.</p>"
    assert drop(html) == html


def test_paragraph_end_at_end_of_document_is_untouched():
    html = f"<p>Ein Satz {LINK}</p>\n"
    assert drop(html) == html


def test_list_item_closings_are_untouched():
    html = "<ul><li>Erstens</li><li>Zweitens</li></ul> Loser Text."
    assert drop(html) == html


def test_anchor_closed_with_p_still_repaired_by_the_full_pass():
    """Ordering guard: _fix_timestamp_links must run before the dropper.

    A model that closes the anchor itself with </p> relies on that tag being
    present; dropping it first would leave the link open.
    """
    html = '<p>Ein Satz <a href="https://www.youtube.com/watch?v=X&t=64" class="ts-link">1:04</p>. Weiter geht es.</p>'
    assert repair_summary_html(html) == (
        '<p>Ein Satz <a href="https://www.youtube.com/watch?v=X&t=64" class="ts-link">1:04</a>. Weiter geht es.</p>'
    )


def test_full_pass_leaves_a_clean_summary_alone():
    html = f"<p>Ein Satz {LINK}</p>\n<h3>Titel</h3>\n<p>Noch einer.</p>"
    assert repair_summary_html(html) == html


def test_repeated_paragraph_ends_collapse():
    html = f"<p>Ein Satz {LINK}</p></p>\n<h3>Titel</h3>"
    assert drop(html) == f"<p>Ein Satz {LINK}</p>\n<h3>Titel</h3>"


def test_separate_paragraphs_are_not_collapsed():
    html = "<p>Eins.</p>\n<p>Zwei.</p>"
    assert drop(html) == html


def test_dedup_takes_the_space_before_a_dropped_link():
    html = (
        f"<p>Erster Satz {LINK}. Zweiter Satz {LINK}. Dritter.</p>"
    )
    assert repair_summary_html(html) == f"<p>Erster Satz {LINK}. Zweiter Satz. Dritter.</p>"
