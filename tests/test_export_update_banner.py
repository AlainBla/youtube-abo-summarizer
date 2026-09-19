"""The update banner must name what actually changed, not just fall back to
"Archiv aktualisiert" / "Archive updated".

updateBannerKey(current, pending) is a pure decision function: it never
touches the DOM or a real MANIFEST, so every branch can be pinned with two
literal manifest objects and no bootstrap() call.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from export_harness import extract_script, node_available, render_export, run_node, video


def _script() -> str:
    return extract_script(render_export([video("v1", "2026-01-01T00:00:00Z")]))


def _key(current: dict, pending: dict) -> dict:
    snippet = "console.log(JSON.stringify(updateBannerKey(%s, %s)));" % (
        json.dumps(current),
        json.dumps(pending),
    )
    out = run_node(_script(), snippet)
    return json.loads(out.strip().splitlines()[-1])


FULL = {
    "generated_at": "2026-01-01T00:00:00+00:00",
    "video_count": 10,
    "newest_id": "v009",
    "newest_published_at": "2026-01-01T00:00:00Z",
    "summary_count": 8,
    "summary_digest": "aaaaaaaaaaaaaaaa",
}


# The same manifest with a recent_ids window: the IDs of the most recently
# collected videos, newest arrival first. FULL deliberately carries none, so
# the tests using it pin the fallback for archives exported before the field
# existed.
def _with_recent(manifest: dict, ids: list[str], **over) -> dict:
    return dict(manifest, recent_ids=list(ids), **over)


WINDOW = ["v009", "v008", "v007", "v006", "v005"]


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_videos_added_wins_over_everything_else():
    current = dict(FULL)
    pending = dict(FULL, video_count=12, summary_count=5, summary_digest="bbbbbbbbbbbbbbbb")
    key = _key(current, pending)
    assert key == {"kind": "videos", "count": 2, "removed": 0}


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_arrivals_are_counted_when_just_as_many_videos_left():
    # The case a personal export (--user) produces constantly: two new videos
    # in, two read ones aged out, video_count unchanged. Netting them out is
    # what used to leave only "Archiv aktualisiert".
    current = _with_recent(FULL, WINDOW)
    pending = _with_recent(FULL, ["v011", "v010"] + WINDOW[:3], summary_digest="bbbbbbbbbbbbbbbb")
    key = _key(current, pending)
    assert key == {"kind": "videos", "count": 2, "removed": 2}


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_arrivals_are_counted_when_more_videos_left_than_arrived():
    current = _with_recent(FULL, WINDOW)
    pending = _with_recent(FULL, ["v011"] + WINDOW, video_count=7)
    key = _key(current, pending)
    assert key == {"kind": "videos", "count": 1, "removed": 4}


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_departures_alone_do_not_claim_new_videos():
    current = _with_recent(FULL, WINDOW)
    pending = _with_recent(FULL, WINDOW[:3], video_count=8, summary_count=6,
                           summary_digest="dddddddddddddddd")
    key = _key(current, pending)
    assert key == {"kind": "generic"}


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_a_window_sharing_nothing_counts_the_whole_window():
    # A page left open long enough that every ID in the pending window is new
    # to it: the count is capped by the window, never wrong in the other
    # direction, and still names arrivals rather than falling back to generic.
    current = _with_recent(FULL, WINDOW)
    pending = _with_recent(FULL, ["n1", "n2", "n3"], video_count=13)
    key = _key(current, pending)
    assert key == {"kind": "videos", "count": 3, "removed": 0}


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_ids_that_only_slide_into_the_window_are_not_arrivals():
    # Removals let older IDs enter the pending window that the current one
    # never carried. They sit behind the first known ID, so the prefix count
    # ignores them -- a set difference would report them as new.
    current = _with_recent(FULL, ["v009", "v008", "v007"])
    pending = _with_recent(FULL, ["v010", "v008", "v006", "v005"], video_count=10)
    key = _key(current, pending)
    assert key == {"kind": "videos", "count": 1, "removed": 1}


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_recent_ids_on_one_side_only_falls_back_to_the_net_count():
    current = dict(FULL)
    pending = _with_recent(FULL, ["v011"] + WINDOW, video_count=11)
    assert _key(current, pending) == {"kind": "videos", "count": 1, "removed": 0}
    # ... and a shrink the net figure cannot explain stays generic, exactly as
    # it did before recent_ids existed.
    assert _key(current, dict(FULL, video_count=8)) == {"kind": "generic"}


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_new_summaries_at_unchanged_video_count():
    current = dict(FULL)
    pending = dict(FULL, summary_count=11, summary_digest="bbbbbbbbbbbbbbbb")
    key = _key(current, pending)
    assert key == {"kind": "newSummaries", "count": 3}


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_changed_digest_at_unchanged_counts_reads_as_summaries_changed():
    current = dict(FULL)
    pending = dict(FULL, summary_digest="cccccccccccccccc")
    key = _key(current, pending)
    assert key == {"kind": "summariesChanged"}


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_shrinking_export_with_changed_digest_stays_generic():
    # --prune-days / a narrower --hours drops videos, which changes the
    # digest too (the departed videos' records are gone) -- that must not
    # be reported as "summaries updated".
    current = dict(FULL)
    pending = dict(FULL, video_count=7, summary_count=6, summary_digest="dddddddddddddddd")
    key = _key(current, pending)
    assert key == {"kind": "generic"}


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_missing_summary_fields_on_pending_stays_generic():
    current = dict(FULL)
    pending = {
        "generated_at": "2026-01-02T00:00:00+00:00",
        "video_count": 10,
        "newest_id": "v009",
        "newest_published_at": "2026-01-01T00:00:00Z",
    }
    key = _key(current, pending)
    assert key == {"kind": "generic"}


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_missing_summary_fields_on_current_stays_generic():
    current = {
        "generated_at": "2025-01-01T00:00:00+00:00",
        "video_count": 10,
        "newest_id": "v009",
        "newest_published_at": "2026-01-01T00:00:00Z",
    }
    pending = dict(FULL, summary_digest="eeeeeeeeeeeeeeee")
    key = _key(current, pending)
    assert key == {"kind": "generic"}


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_an_unchanged_window_still_reports_new_summaries():
    current = _with_recent(FULL, WINDOW)
    pending = _with_recent(FULL, WINDOW, summary_count=11, summary_digest="bbbbbbbbbbbbbbbb")
    key = _key(current, pending)
    assert key == {"kind": "newSummaries", "count": 3}


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_nothing_changed_stays_generic():
    current = dict(FULL)
    pending = dict(FULL)
    key = _key(current, pending)
    assert key == {"kind": "generic"}


# ── rendered German strings (plural bug regression) ─────────────────────────


def _i18n_string(lang: str, key: str, *args) -> str:
    script = _script()
    call = "I18N['%s'].%s" % (lang, key)
    if args:
        call += "(%s)" % ", ".join(json.dumps(a) for a in args)
    out = run_node(script, "console.log(JSON.stringify(%s));" % call)
    return json.loads(out.strip().splitlines()[-1])


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_german_video_wording_singular():
    assert _i18n_string("de", "updateNew", 1) == "1 neues Video verfügbar"


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_german_video_wording_plural():
    assert _i18n_string("de", "updateNew", 2) == "2 neue Videos verfügbar"


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_german_video_wording_names_departures_separately():
    assert _i18n_string("de", "updateNew", 3, 5) == "3 neue Videos verfügbar, 5 entfernt"
    assert _i18n_string("de", "updateNew", 2, 0) == "2 neue Videos verfügbar"


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_english_video_wording_names_departures_separately():
    assert _i18n_string("en", "updateNew", 3, 5) == "3 new videos available, 5 removed"
    assert _i18n_string("en", "updateNew", 1, 0) == "1 new video available"


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_german_summary_wording_singular():
    assert _i18n_string("de", "updateNewSummaries", 1) == "1 neue Zusammenfassung verfügbar"


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_german_summary_wording_plural():
    assert _i18n_string("de", "updateNewSummaries", 3) == "3 neue Zusammenfassungen verfügbar"


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_english_video_wording_unaffected():
    assert _i18n_string("en", "updateNew", 1) == "1 new video available"
    assert _i18n_string("en", "updateNew", 2) == "2 new videos available"


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_english_summary_wording():
    assert _i18n_string("en", "updateNewSummaries", 1) == "1 new summary available"
    assert _i18n_string("en", "updateNewSummaries", 3) == "3 new summaries available"


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_summaries_changed_plain_strings():
    assert _i18n_string("de", "updateSummariesChanged") == "Zusammenfassungen aktualisiert"
    assert _i18n_string("en", "updateSummariesChanged") == "Summaries updated"


# ── The glue between the decision and the rendered banner ────────────────────
#
# updateBannerKey() and the I18N strings are each pinned above, but nothing so
# far exercises the mapping between them inside renderUpdateBanner(). A typo
# there (s.updateNewSummarie) would write "undefined" into the banner with
# every test still green, so these two drive the whole chain -- poll response,
# decision, string, DOM -- for the two new wordings. The old two are covered
# the same way in tests/test_export_manifest.py, whose fetch stub is reused
# here rather than copied.
from test_export_manifest import POLL_SETUP  # noqa: E402


def _poll_script() -> str:
    return POLL_SETUP + "\n" + _script()


def _banner_after_poll(pending_fields: str) -> dict:
    snippet = """
    stubManifest(Object.assign({}, MANIFEST, {generated_at: '2099-01-01T00:00:00+00:00'}, %s));
    checkForUpdate().then(function () {
      console.log(JSON.stringify({
        display: document.getElementById('update-banner').style.display,
        text: document.getElementById('update-text').textContent
      }));
      process.exit(0);
    });
    """ % pending_fields
    return json.loads(run_node(_poll_script(), snippet))


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_a_poll_finding_more_summaries_renders_the_summary_wording():
    out = _banner_after_poll(
        "{summary_count: MANIFEST.summary_count + 2, summary_digest: 'ffffffffffffffff'}")
    assert out["display"] != "none"
    assert out["text"] == "2 neue Zusammenfassungen verfügbar"


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_a_poll_finding_only_a_changed_digest_renders_the_changed_wording():
    out = _banner_after_poll("{summary_digest: 'ffffffffffffffff'}")
    assert out["display"] != "none"
    assert out["text"] == "Zusammenfassungen aktualisiert"


@pytest.mark.skipif(not node_available(), reason="node not installed")
def test_a_poll_finding_arrivals_and_departures_renders_both_numbers():
    # The whole chain for the case that started this: same video count, but
    # the window shows two arrivals.
    # A window whose IDs the page does not know at all, at an unchanged
    # video_count: as many videos left as arrived.
    out = _banner_after_poll("{recent_ids: ['n1', 'n2'], video_count: MANIFEST.video_count}")
    assert out["display"] != "none"
    assert out["text"] == "2 neue Videos verfügbar, 2 entfernt"
