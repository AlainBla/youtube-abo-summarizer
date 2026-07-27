"""The pre-rendered first page must match what buildCard() produces."""
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from export_harness import extract_script, node_available, render_export, run_node, video


def _normalize(html: str) -> str:
    """Collapse whitespace between tags so formatting differences do not matter."""
    return re.sub(r">\s+<", "><", html).strip()


def test_first_page_cards_are_in_the_html_before_the_data_blob():
    videos = [video("v%03d" % i, "2026-01-01T00:%02d:00Z" % i) for i in range(30)]
    html = render_export(videos)
    first_card = html.index('data-video-id="v029"')
    blob = html.index("const INDEX_B64")
    assert first_card < blob, "cards must precede the data blob so they paint first"
    # exactly one page of cards is pre-rendered
    assert html.count('class="video-card') == 20
    assert 'data-video-id="v010"' in html      # 20th newest
    assert 'data-video-id="v009"' not in html  # 21st is not pre-rendered


def test_pre_rendered_card_shows_summary_preview_and_toggle():
    html = render_export([video("v1", "2026-01-01T00:00:00Z")])
    assert "Summary of v1" in html
    assert "summary-details" in html


def test_page_size_is_rendered_from_the_pre_render_count():
    import renderer
    html = render_export([video("v1", "2026-01-01T00:00:00Z")])
    assert "const PAGE_SIZE = %d;" % renderer.EXPORT_FIRST_PAGE in html


def test_filter_controls_do_not_restore_stale_values_on_reload():
    # A restored <select> value would contradict the static grid until
    # bootstrap() runs, and a toggle in that window blanks the grid.
    html = render_export([video("v1", "2026-01-01T00:00:00Z")])
    for control in ('id="channel-filter"', 'id="tag-filter"', 'id="read-filter"',
                    'id="bookmark-filter"', 'id="sort"', 'id="date-filter"'):
        tag_start = html.index(control)
        tag = html[html.rindex("<", 0, tag_start):html.index(">", tag_start)]
        assert 'autocomplete="off"' in tag, control


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_pre_rendered_markup_matches_buildCard_output():
    videos = [
        video("v1", "2026-03-01T00:00:00Z"),
        video("v2", "2026-02-01T00:00:00Z", tags=[], duration="", summary=None,
              transcript_error="country_blocked"),
        video("v3", "2026-01-01T00:00:00Z", title='Quote " & <tag>'),
    ]
    html = render_export(videos)
    script = extract_script(html)
    js_cards = run_node(script, """
      bootstrap().then(function () {
        console.log(JSON.stringify(VIDEOS.map(buildCard)));
      });
    """)
    built = json.loads(js_cards.strip().splitlines()[-1])

    grid = re.search(r'<div id="grid">(.*?)</div>\s*<!-- /grid -->', html, re.S)
    assert grid, "grid marker not found"
    pre_rendered = re.findall(r"<article .*?</article>", grid.group(1), re.S)
    assert len(pre_rendered) == len(built) == 3

    for pre, js in zip(pre_rendered, built):
        assert _normalize(pre) == _normalize(js)
