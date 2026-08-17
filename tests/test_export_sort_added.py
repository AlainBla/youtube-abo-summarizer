"""The 'added-desc' sort orders by collected_at, not published_at."""
import json

import pytest

from export_harness import extract_script, node_available, render_export, run_node, video


def _order(html_grid: str, ids: list[str]) -> list[str]:
    """Return ids in the order their cards appear in the rendered grid."""
    return sorted(ids, key=lambda vid: html_grid.index('data-video-id="%s"' % vid))


def _render_with_sort(videos, sort_value, **render_kwargs):
    html = render_export(videos, **render_kwargs)
    script = "globalThis.requestAnimationFrame = function () {};\n" + extract_script(html)
    snippet = """
    bootstrap().then(function () {
      document.getElementById('sort').value = '%s';
      applyFiltersAndSort();
      console.log(JSON.stringify({grid: document.getElementById('grid').innerHTML}));
      process.exit(0);
    });
    """ % sort_value
    out = run_node(script, snippet)
    return json.loads(out.strip().splitlines()[-1])["grid"]


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_added_desc_sorts_by_collected_at_not_published_at():
    # Publish order (newest first) is v1, v2, v3 -- the reverse of the order in
    # which they entered the store. A video ingested on demand today is old by
    # publish date but must sort to the top under 'added-desc'.
    videos = [
        video("v1", "2026-03-03T00:00:00Z", collected_at="2026-03-03T10:00:00+00:00"),
        video("v2", "2026-02-02T00:00:00Z", collected_at="2026-04-04T10:00:00+00:00"),
        video("v3", "2026-01-01T00:00:00Z", collected_at="2026-05-05T10:00:00+00:00"),
    ]
    grid = _render_with_sort(videos, "added-desc")
    assert _order(grid, ["v1", "v2", "v3"]) == ["v3", "v2", "v1"]


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_added_desc_breaks_collected_at_ties_by_publish_date():
    # collect.py stamps one timestamp per run, so a whole run shares a
    # collected_at. Within such a block the newest video must come first.
    run = "2026-04-04T10:00:00+00:00"
    videos = [
        video("v1", "2026-01-01T00:00:00Z", collected_at=run),
        video("v2", "2026-01-03T00:00:00Z", collected_at=run),
        video("v3", "2026-01-02T00:00:00Z", collected_at=run),
    ]
    grid = _render_with_sort(videos, "added-desc")
    assert _order(grid, ["v1", "v2", "v3"]) == ["v2", "v3", "v1"]


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_added_desc_works_on_the_uncompressed_path_too():
    # --no-compress embeds one plain {index, summaries} object instead of the
    # chunked gzip blobs; collected_at has to survive that path as well.
    videos = [
        video("v1", "2026-03-03T00:00:00Z", collected_at="2026-03-03T10:00:00+00:00"),
        video("v2", "2026-02-02T00:00:00Z", collected_at="2026-04-04T10:00:00+00:00"),
        video("v3", "2026-01-01T00:00:00Z", collected_at="2026-05-05T10:00:00+00:00"),
    ]
    grid = _render_with_sort(videos, "added-desc", compress=False)
    assert _order(grid, ["v1", "v2", "v3"]) == ["v3", "v2", "v1"]


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_added_desc_falls_back_to_published_at_when_collected_at_missing():
    # Archives exported before collected_at was embedded carry no such field;
    # the sort must degrade to publish order instead of throwing.
    videos = [
        video("v1", "2026-01-01T00:00:00Z"),
        video("v2", "2026-01-03T00:00:00Z"),
        video("v3", "2026-01-02T00:00:00Z"),
    ]
    grid = _render_with_sort(videos, "added-desc")
    assert _order(grid, ["v1", "v2", "v3"]) == ["v2", "v3", "v1"]
