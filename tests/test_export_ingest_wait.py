"""Waiting for a freshly queued video to turn up in the archive.

The manifest only says *that* a new export happened, never which video it
brought, so the page reloads into ?v=ID and lets the deep-link view decide.
That makes two things critical: the saved baseline must be re-pinned to the
export currently being viewed (otherwise every poll reloads again), and a
video that is already in the archive must not be waited for at all.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from export_harness import extract_script, node_available, render_export, run_node, video

pytestmark = pytest.mark.skipif(not node_available(), reason="node not installed")

SYNC = "https://sync.example.com"


def _archive(n=60):
    return [video("v%03d" % i, "2026-01-01T00:%02d:00Z" % i) for i in range(n)]


def _script(videos=None, **kw):
    kw.setdefault("sync_url", SYNC)
    return extract_script(render_export(videos or _archive(), **kw))


def _run(snippet, videos=None, prelude="", **kw):
    return run_node(prelude + _script(videos, **kw), snippet)


def _json(snippet, videos=None, prelude="", **kw):
    return json.loads(_run(snippet, videos, prelude, **kw))


# ── the decision ──────────────────────────────────────────────────────────────

def test_an_unchanged_manifest_is_not_a_reason_to_reload():
    out = _json("""
      var saved = {video_id: 'v001', generated_at: MANIFEST.generated_at, started: Date.now()};
      console.log(JSON.stringify(ingestWaitReady(saved, MANIFEST)));
      process.exit(0);
    """)
    assert out is False


def test_a_new_export_is():
    out = _json("""
      var saved = {video_id: 'v001', generated_at: '2020-01-01T00:00:00Z', started: Date.now()};
      console.log(JSON.stringify(ingestWaitReady(saved, MANIFEST)));
      process.exit(0);
    """)
    assert out is True


def test_a_manifest_without_a_timestamp_decides_nothing():
    out = _json("""
      var saved = {video_id: 'v001', generated_at: 'x', started: Date.now()};
      console.log(JSON.stringify([ingestWaitReady(saved, {}), ingestWaitReady(null, MANIFEST)]));
      process.exit(0);
    """)
    assert out == [False, False]


def test_the_wait_gives_up_after_ten_minutes():
    out = _json("""
      var now = Date.now();
      console.log(JSON.stringify([
        ingestWaitExpired({started: now - 9 * 60 * 1000}, now),
        ingestWaitExpired({started: now - 11 * 60 * 1000}, now)
      ]));
      process.exit(0);
    """)
    assert out == [False, True]


# ── the reload ────────────────────────────────────────────────────────────────

def test_a_new_export_sends_the_page_to_that_video():
    out = _run("""
      saveIngestWait({video_id: 'abc12345678', generated_at: 'older', started: Date.now()});
      globalThis.fetch = function () {
        return Promise.resolve({ok: true, json: function () {
          return Promise.resolve({generated_at: 'newer'});
        }});
      };
      pollIngestWait().then(function () {
        console.log(location.href);
        process.exit(0);
      });
    """)
    assert out.strip() == "https://example.com/x.html?v=abc12345678"


def test_an_unchanged_manifest_leaves_the_page_where_it_is():
    out = _json("""
      var href = location.href;
      saveIngestWait({video_id: 'abc12345678', generated_at: MANIFEST.generated_at, started: Date.now()});
      globalThis.fetch = function () {
        return Promise.resolve({ok: true, json: function () {
          return Promise.resolve(MANIFEST);
        }});
      };
      pollIngestWait().then(function () {
        console.log(JSON.stringify({moved: location.href !== href, kept: !!loadIngestWait()}));
        process.exit(0);
      });
    """)
    assert out == {"moved": False, "kept": True}


def test_resuming_re_pins_the_baseline_to_the_export_being_viewed():
    """Without this the very next poll sees a manifest that differs from the
    stale saved value and reloads again -- forever."""
    out = _json("""
      saveIngestWait({video_id: 'v003', generated_at: 'ancient', started: Date.now()});
      resumeIngestWait();
      console.log(JSON.stringify(loadIngestWait().generated_at === MANIFEST.generated_at));
      process.exit(0);
    """)
    assert out is True


# ── arriving, and not arriving ────────────────────────────────────────────────

def test_the_wait_ends_when_the_video_is_on_screen():
    out = _json(
        """
        setTimeout(function () {
          console.log(JSON.stringify({pending: !!loadIngestWait()}));
          process.exit(0);
        }, 400);
        """,
        prelude=("location.search = '?v=v003';\n"
                 "globalThis.localStorage.setItem('yt_ingest_wait', JSON.stringify("
                 "{video_id: 'v003', generated_at: 'ancient', started: Date.now()}));\n"),
    )
    assert out == {"pending": False}


def test_a_reload_that_did_not_bring_the_video_keeps_waiting():
    out = _json(
        """
        setTimeout(function () {
          var saved = loadIngestWait();
          console.log(JSON.stringify({pending: !!saved, id: saved && saved.video_id}));
          process.exit(0);
        }, 400);
        """,
        prelude=("location.search = '?v=zzz11111111';\n"
                 "globalThis.localStorage.setItem('yt_ingest_wait', JSON.stringify("
                 "{video_id: 'zzz11111111', generated_at: 'ancient', started: Date.now()}));\n"),
    )
    assert out == {"pending": True, "id": "zzz11111111"}


# ── a video that is already there ─────────────────────────────────────────────

def test_a_video_already_in_the_archive_needs_no_queueing():
    out = _json("""
      setTimeout(function () {
        ingestTargetKnown('v003').then(function (known) {
          console.log(JSON.stringify(known));
          process.exit(0);
        });
      }, 300);
    """)
    assert out is True


def test_a_video_in_the_archive_without_a_summary_is_worth_queueing():
    videos = _archive(30)
    videos[5]["summary"] = None
    videos[5]["transcript_error"] = "ip_blocked"
    out = _json("""
      setTimeout(function () {
        ingestTargetKnown('v005').then(function (known) {
          console.log(JSON.stringify(known));
          process.exit(0);
        });
      }, 300);
    """, videos=videos)
    assert out is False


def test_an_unknown_video_is_worth_queueing():
    out = _json("""
      setTimeout(function () {
        ingestTargetKnown('zzz11111111').then(function (known) {
          console.log(JSON.stringify(known));
          process.exit(0);
        });
      }, 300);
    """)
    assert out is False


def test_a_submission_before_the_index_is_decoded_still_sees_the_archive():
    """The sync boot reveals the Ingest box from /api/whoami, before the data
    blob is parsed -- and the userscript submits the moment it appears. Judging
    "not in the archive" then would queue videos that are already there."""
    # doIngest() only accepts a real 11-character ID, so the archive needs one.
    videos = _archive(30) + [video("abcdefgh123", "2026-02-01T00:00:00Z")]
    out = _json("""
      var posted = [];
      var realFetch = globalThis.fetch;
      globalThis.fetch = function (url, opts) {
        if (String(url).indexOf('/api/ingest') !== -1) posted.push(url);
        return realFetch(url, opts);
      };
      document.getElementById('ingest-input').value = 'abcdefgh123';
      doIngest();                       // before bootstrap() has run
      setTimeout(function () {
        console.log(JSON.stringify({posted: posted.length, href: location.href}));
        process.exit(0);
      }, 500);
    """, videos=videos)
    assert out["posted"] == 0
    assert out["href"].endswith("?v=abcdefgh123")


def test_a_file_url_archive_is_not_offered_a_wait_it_cannot_carry_out():
    """No server, no manifest to poll: the wait could only ever time out."""
    out = _json("""
      location.protocol = 'file:';
      showWaitOffer('abc12345678');
      console.log(JSON.stringify(document.getElementById('ingest-wait-btn').style.display || 'never shown'));
      process.exit(0);
    """)
    assert out == "never shown"


def test_an_expired_wait_is_not_silently_given_another_ten_minutes():
    out = _json("""
      saveIngestWait({video_id: 'zzz11111111', generated_at: 'ancient',
                      started: Date.now() - 11 * 60 * 1000});
      resumeIngestWait();
      var saved = loadIngestWait();
      console.log(JSON.stringify({
        polling: waitTimer !== null,
        started_moved: saved.started > Date.now() - 60 * 1000
      }));
      process.exit(0);
    """)
    assert out == {"polling": False, "started_moved": False}


def test_both_languages_carry_the_wait_strings():
    html = render_export(_archive(3), sync_url=SYNC)
    for key in ("waitBtn", "waitRunning", "waitArrived", "waitTimeout", "alreadyInArchive"):
        assert html.count(key + ":") == 2, f"{key} missing from one of the JS I18N dicts"


# ── the verdict the userscript reads ──────────────────────────────────────────
# The button's disabled flag is not a signal anyone outside the page can sample
# reliably: postIngest() re-enables it before clearing the input, so a fast 202
# leaves no trace between two polls. The outcome is therefore stated outright.

def test_a_queued_video_says_so():
    videos = _archive(30)
    out = _json("""
      globalThis.fetch = function () {
        return Promise.resolve({status: 202, json: function () { return Promise.resolve({}); }});
      };
      setTimeout(function () {
        document.getElementById('ingest-input').value = 'abcdefgh123';
        doIngest();
        setTimeout(function () {
          var ds = document.getElementById('sync-ingest').dataset;
          console.log(JSON.stringify({id: ds.ingestId, result: ds.ingestResult}));
          process.exit(0);
        }, 300);
      }, 300);
    """, videos=videos)
    assert out == {"id": "abcdefgh123", "result": "queued"}


def test_a_video_already_in_the_archive_says_that_instead():
    videos = _archive(30) + [video("abcdefgh123", "2026-02-01T00:00:00Z")]
    out = _json("""
      setTimeout(function () {
        document.getElementById('ingest-input').value = 'abcdefgh123';
        doIngest();
        setTimeout(function () {
          var ds = document.getElementById('sync-ingest').dataset;
          console.log(JSON.stringify({id: ds.ingestId, result: ds.ingestResult}));
          process.exit(0);
        }, 300);
      }, 300);
    """, videos=videos)
    assert out == {"id": "abcdefgh123", "result": "already"}


def test_a_refused_request_is_not_reported_as_an_arrival():
    out = _json("""
      globalThis.fetch = function () {
        return Promise.resolve({status: 500, json: function () { return Promise.resolve({}); }});
      };
      setTimeout(function () {
        document.getElementById('ingest-input').value = 'abcdefgh123';
        doIngest();
        setTimeout(function () {
          console.log(JSON.stringify(document.getElementById('sync-ingest').dataset.ingestResult));
          process.exit(0);
        }, 300);
      }, 300);
    """)
    assert out == "failed"


def test_the_previous_submission_s_verdict_is_cleared_before_the_next():
    out = _json("""
      setIngestVerdict('abcdefgh123', 'already');
      document.getElementById('ingest-input').value = 'not-a-video-id-at-all';
      doIngest();
      var ds = document.getElementById('sync-ingest').dataset;
      console.log(JSON.stringify({id: ds.ingestId, result: ds.ingestResult}));
      process.exit(0);
    """)
    assert out["result"] == "failed"
    assert out["id"] != "abcdefgh123"


def test_the_box_carries_the_marker_that_tells_the_userscript_to_read_it():
    html = render_export(_archive(5), sync_url=SYNC)
    assert 'id="sync-ingest"' in html
    assert 'data-ingest-result=""' in html


# ── what a deep link says when the video is not there ────────────────────────
# Every new export reloads a running wait into ?v=ID, and most of those exports
# brought something else. "Video X ist nicht in diesem Archiv", plus a link
# into a full archive that does not have it either, reads as a failure where
# the truth is "not yet".

def test_a_deep_link_without_a_wait_says_the_archive_does_not_have_it():
    out = _json("""
      console.log(JSON.stringify(singleMissingKey('zzz11111111', null, Date.now())));
      process.exit(0);
    """)
    assert out == "notFound"


def test_a_wait_for_this_very_video_makes_it_a_waiting_state():
    out = _json("""
      var saved = {video_id: 'zzz11111111', generated_at: 'x', started: Date.now()};
      console.log(JSON.stringify(singleMissingKey('zzz11111111', saved, Date.now())));
      process.exit(0);
    """)
    assert out == "waiting"


def test_a_wait_for_another_video_says_nothing_about_this_one():
    out = _json("""
      var saved = {video_id: 'v001', generated_at: 'x', started: Date.now()};
      console.log(JSON.stringify(singleMissingKey('zzz11111111', saved, Date.now())));
      process.exit(0);
    """)
    assert out == "notFound"


def test_an_expired_wait_no_longer_excuses_the_missing_video():
    out = _json("""
      var started = Date.now() - (WAIT_MAX_MS + 1000);
      var saved = {video_id: 'zzz11111111', generated_at: 'x', started: started};
      console.log(JSON.stringify(singleMissingKey('zzz11111111', saved, Date.now())));
      process.exit(0);
    """)
    assert out == "notFound"


FULL = "https://sync.example.com/yt.full.html"


def test_the_waiting_page_offers_no_link_into_the_full_archive():
    """The full archive does not have the video either -- it has not been
    collected yet. Only the real "not in this archive" carries that link, so
    this runs against a filtered export, where the link exists at all."""
    out = _json(
        """
        setTimeout(function () {
          console.log(JSON.stringify(document.getElementById('grid').innerHTML));
          process.exit(0);
        }, 400);
        """,
        prelude=("location.search = '?v=zzz11111111';\n"
                 "globalThis.localStorage.setItem('yt_ingest_wait', JSON.stringify("
                 "{video_id: 'zzz11111111', generated_at: 'ancient', started: Date.now()}));\n"),
        full_url=FULL,
    )
    assert "zzz11111111" in out
    assert "nicht in diesem Archiv" not in out
    assert "full-link" not in out
    assert "eingereiht" in out


def test_without_a_wait_the_same_deep_link_still_says_not_in_this_archive():
    out = _json(
        """
        setTimeout(function () {
          console.log(JSON.stringify(document.getElementById('grid').innerHTML));
          process.exit(0);
        }, 400);
        """,
        prelude="location.search = '?v=zzz11111111';\n",
        full_url=FULL,
    )
    assert "nicht in diesem Archiv" in out
    assert "full-link" in out


def test_a_cancelled_wait_stops_promising_a_reload():
    """singleMissingKey() is read at render time, so a wait that ends while
    its message is on screen has to re-render it -- otherwise the page keeps
    announcing a reload nobody will send."""
    out = _json(
        """
        setTimeout(function () {
          var before = document.getElementById('grid').innerHTML;
          cancelIngestWait();
          console.log(JSON.stringify({
            before: before, after: document.getElementById('grid').innerHTML
          }));
          process.exit(0);
        }, 400);
        """,
        prelude=("location.search = '?v=zzz11111111';\n"
                 "globalThis.localStorage.setItem('yt_ingest_wait', JSON.stringify("
                 "{video_id: 'zzz11111111', generated_at: 'ancient', started: Date.now()}));\n"),
        full_url=FULL,
    )
    assert "eingereiht" in out["before"]
    assert "nicht in diesem Archiv" in out["after"]
    assert "full-link" in out["after"]


def test_continuing_an_expired_wait_takes_the_message_back():
    """"Weiter warten" after a timeout starts a real wait again; the message
    under it must stop saying the archive does not have the video."""
    out = _json(
        """
        setTimeout(function () {
          var before = document.getElementById('grid').innerHTML;
          startIngestWait('zzz11111111');
          console.log(JSON.stringify({
            before: before, after: document.getElementById('grid').innerHTML
          }));
          process.exit(0);
        }, 400);
        """,
        prelude=("location.search = '?v=zzz11111111';\n"
                 "globalThis.localStorage.setItem('yt_ingest_wait', JSON.stringify("
                 "{video_id: 'zzz11111111', generated_at: 'ancient', "
                 "started: Date.now() - 11 * 60 * 1000}));\n"),
        full_url=FULL,
    )
    assert "nicht in diesem Archiv" in out["before"]
    assert "eingereiht" in out["after"]


def test_both_languages_carry_the_waiting_text():
    html = render_export(_archive(3), sync_url=SYNC)
    assert html.count("singleWaiting:") == 2
