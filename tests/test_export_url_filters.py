"""The filter configuration lives in the query string, so a reload keeps it.

Every render mirrors the controls into the URL via replaceState, and
bootstrap() reads them back before its first render. Defaults stay out of the
URL, `v` (the deep link) and foreign parameters pass through, and a stale or
hand-edited value never reaches a <select>.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from export_harness import extract_script, node_available, render_export, run_node, video

pytestmark = pytest.mark.skipif(not node_available(), reason="node not installed")


def _archive():
    """60 videos over two channels; the even ones carry an extra tag."""
    out = []
    for i in range(60):
        over = {"channel_id": "UC2", "channel_title": "Channel Two"} if i % 2 else {}
        if i % 3 == 0:
            over["tags"] = ["Tag A", "Tag B"]
        out.append(video("v%03d" % i, "2026-01-01T00:%02d:00Z" % i, **over))
    return out


def _script(search=""):
    return "location.search = %s;\n" % json.dumps(search) + extract_script(render_export(_archive()))


def _eval(expr, search=""):
    snippet = """
    console.log(JSON.stringify(%s));
    process.exit(0);
    """ % expr
    return json.loads(run_node(_script(search), snippet))


def _after_bootstrap(expr, search=""):
    snippet = """
    var __urls = [];
    history.replaceState = function (a, b, url) { __urls.push(url); };
    setTimeout(function () {
      console.log(JSON.stringify(%s));
      process.exit(0);
    }, 300);
    """ % expr
    return json.loads(run_node(_script(search), snippet))


def test_defaults_produce_a_bare_url():
    assert _eval("filterStateToSearch({sort: 'date-desc', page: 1}, '')") == ""


def test_state_round_trips_through_the_query_string():
    state = {"q": "rust & go", "channel": "UC2", "tag": "Tag B", "read": "unread",
             "bm": "bookmarked", "from": "2026-01-01", "len": 20, "sort": "title", "page": 3}
    out = _eval("filterStateFromSearch(filterStateToSearch(%s, ''))" % json.dumps(state))
    assert out == state


def test_the_deep_link_and_foreign_parameters_survive():
    out = _eval("filterStateToSearch({tag: 'X', page: 1}, '?v=abc&tag=old&page=4&foo=1')")
    assert out == "?v=abc&foo=1&tag=X"


def test_invalid_values_are_dropped():
    out = _eval("filterStateFromSearch('?read=maybe&bm=yes&from=gestern&len=7&sort=evil&page=-2')")
    assert out == {}


def test_a_reload_restores_filters_and_page():
    out = _after_bootstrap(
        "{channel: document.getElementById('channel-filter').value,"
        " sort: document.getElementById('sort').value,"
        " page: currentPage,"
        " ids: filtered.map(function (v) { return v.channel_id; })}",
        search="?channel=UC2&sort=title&page=2",
    )
    assert out["channel"] == "UC2"
    assert out["sort"] == "title"
    assert out["page"] == 2
    assert set(out["ids"]) == {"UC2"}


def test_unknown_channel_and_tag_from_a_stale_url_are_ignored():
    out = _after_bootstrap(
        "{channel: document.getElementById('channel-filter').value,"
        " tag: document.getElementById('tag-filter').value,"
        " count: filtered.length}",
        search="?channel=UCgone&tag=Weg",
    )
    assert out == {"channel": "", "tag": "", "count": 60}


def test_a_filter_change_is_written_to_the_url():
    out = _after_bootstrap(
        "(function () {"
        "  document.getElementById('tag-filter').value = 'Tag B';"
        "  applyFiltersAndSort();"
        "  return __urls[__urls.length - 1];"
        "})()",
    )
    assert out == "/x.html?tag=Tag+B"


def test_the_deep_link_view_leaves_the_url_alone():
    out = _after_bootstrap("__urls", search="?v=v005&tag=Tag+B")
    assert out == []
